"""Executes versioned scenario definitions against the installed app and validates expected behaviour.

A scenario is only PASSED when at least one expectation was evaluated and every evaluated expectation held,
and no crash/ANR of the app occurred in the scenario window. Finishing the steps is not a pass.

Outcomes: passed | failed | blocked (missing binding/capability, or a non-executable v1 step) |
          error (automation failure) | inconclusive (nothing could be evaluated).
"""
from __future__ import annotations

import json
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.emulator.adb_manager import AdbManager
from app.emulator.battery_manager import BatteryManager
from app.emulator.emulator_monitor import LogcatCapture, detect_failures
from app.emulator.exceptions import EmulatorError, ScenarioDefinitionError
from app.emulator.measurement_collector import MeasurementCollector, parse_am_start, parse_displayed
from app.emulator.memory_manager import MemoryManager
from app.emulator.network_manager import NetworkManager
from app.emulator.process import device_quote
from app.emulator.schemas import (
    ApplicationStatus as A, ConditionRecord, EnvironmentRequest, MeasurementRecord, MeasurementStatus as M, NetworkCondition, SelectorSpec, TimelineStage,
)
from app.emulator.storage_manager import count_storage_errors
from app.emulator.thermal_manager import ThermalManager

SCENARIO_DIR = Path(__file__).parent / "scenarios"
UI_DUMP = "/data/local/tmp/pocketlab_ui.xml"
_BOUNDS = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")
_TYPEABLE = re.compile(r"^[A-Za-z0-9 ._@\-]{0,200}$")


class StepBlocked(Exception):
    pass


def load_definitions(directory: Path = SCENARIO_DIR) -> list[dict]:
    definitions = []
    for path in sorted(directory.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema_version") != 1 or not data.get("steps"):
            raise ScenarioDefinitionError(f"{path.name}: unsupported schema_version or empty steps")
        for step in data["steps"]:
            if step["action"] not in ACTIONS:
                raise ScenarioDefinitionError(f"{path.name}: unknown action {step['action']}")
        definitions.append(data)
    return definitions


# ------------------------------------------------------------------------------------------ UI driver
class UiAutomatorDriver:
    """UI automation through the on-device UIAutomator framework: hierarchy dump + input injection."""

    def __init__(self, adb: AdbManager):
        self.adb = adb

    def dump(self) -> list[dict]:
        self.adb.shell("uiautomator", "dump", UI_DUMP, timeout=30)
        xml = self.adb.shell("cat", UI_DUMP, timeout=20).stdout
        if not xml.strip().startswith("<?xml") or len(xml) > 5_000_000:
            return []
        try:
            root = ET.fromstring(xml)
        except ET.ParseError:
            return []
        return [dict(node.attrib) for node in root.iter("node")]

    @staticmethod
    def matches(node: dict, selector: SelectorSpec) -> bool:
        if selector.resource_id and node.get("resource-id") != selector.resource_id and not node.get("resource-id", "").endswith(f":id/{selector.resource_id}"):
            return False
        if selector.text and node.get("text") != selector.text:
            return False
        if selector.text_contains and selector.text_contains.lower() not in node.get("text", "").lower():
            return False
        if selector.content_desc and node.get("content-desc") != selector.content_desc:
            return False
        return True

    def find(self, selector: SelectorSpec, timeout_s: float = 10) -> dict | None:
        deadline = time.monotonic() + timeout_s
        while True:
            for node in self.dump():
                if self.matches(node, selector):
                    return node
            if time.monotonic() >= deadline:
                return None
            time.sleep(1)

    def tap(self, node: dict) -> tuple[int, int]:
        m = _BOUNDS.fullmatch(node.get("bounds", ""))
        if not m:
            raise EmulatorError("Matched node has no bounds")
        x1, y1, x2, y2 = (int(v) for v in m.groups())
        x, y = (x1 + x2) // 2, (y1 + y2) // 2
        self.adb.shell("input", "tap", str(x), str(y), timeout=15, check=True)
        return x, y


# ------------------------------------------------------------------------------------------ context/results
@dataclass
class ScenarioContext:
    adb: AdbManager
    package: str
    component: str
    request: EnvironmentRequest
    collector: MeasurementCollector
    memory: MemoryManager
    battery: BatteryManager
    thermal: ThermalManager
    network: NetworkManager | None
    logcat: LogcatCapture
    artifacts_dir: Path
    emulator: bool
    cancelled: Callable[[], bool]
    emit_event: Callable[[str, str, dict], None]
    record_condition: Callable[[ConditionRecord], None]
    perfetto_supported: bool = False


@dataclass
class StepResult:
    index: int
    action: str
    status: str = "done"
    started_at: str = ""
    duration_ms: int = 0
    expectations: list[dict] = field(default_factory=list)
    evidence: dict = field(default_factory=dict)
    error: str | None = None


@dataclass
class ScenarioResult:
    run_scenario_id: int
    name: str
    version: int | None
    status: str = "running"
    reason: str | None = None
    started_at: str = ""
    completed_at: str = ""
    steps: list[StepResult] = field(default_factory=list)
    measurements: list[MeasurementRecord] = field(default_factory=list)
    artifacts: list[tuple[Path, str, str]] = field(default_factory=list)
    failures: dict = field(default_factory=dict)

    def as_json(self) -> dict:
        return {"status": self.status, "reason": self.reason, "started_at": self.started_at, "completed_at": self.completed_at,
                "steps": [s.__dict__ for s in self.steps], "failures": self.failures,
                "artifacts": [{"file": p.name, "type": t} for p, t, _ in self.artifacts]}


# ------------------------------------------------------------------------------------------ executor
class ScenarioExecutor:
    def __init__(self, ctx: ScenarioContext):
        self.ctx = ctx
        self.ui = UiAutomatorDriver(ctx.adb)
        self.state: dict[str, Any] = {}

    # -- helpers ----------------------------------------------------------------------------
    def _selector(self, config: dict) -> SelectorSpec | None:
        sel = config.get("selector") or {}
        if "binding" in sel:
            bound = self.ctx.request.scenario_bindings.get(sel["binding"])
            if bound is None:
                if config.get("optional"):
                    return None
                raise StepBlocked(f"needs app-specific UI binding '{sel['binding']}' (configure it in the Test Lab)")
            return bound
        return SelectorSpec.model_validate(sel) if sel else None

    def _health(self, since_epoch: float) -> dict:
        pid = self.ctx.adb.pidof(self.ctx.package)
        activities = self.ctx.adb.shell("dumpsys", "activity", "activities", timeout=30).stdout
        resumed = re.search(r"(?:mResumedActivity|topResumedActivity|ResumedActivity)[:=]\s*ActivityRecord\{[^}]*\s([\w.]+)/", activities)
        failures = detect_failures(self.ctx.logcat.lines_since(since_epoch), self.ctx.package)
        return {"process_alive": pid is not None, "pid": pid, "foreground": bool(resumed and resumed.group(1) == self.ctx.package),
                "no_crash": not (failures["crashes"] or failures["native_crashes"]), "no_anr": not failures["anrs"], "failures": failures}

    def _screenshot(self, label: str, result: ScenarioResult) -> str:
        safe = re.sub(r"[^a-z0-9_]", "_", label.lower())[:40]
        path = self.ctx.artifacts_dir / f"scenario_{result.run_scenario_id}_{len(result.artifacts):02d}_{safe}.png"
        self.ctx.adb.screencap(path)
        result.artifacts.append((path, "screenshot", "image/png"))
        return path.name

    def _launch(self, config: dict, result: ScenarioResult) -> dict:
        cold = config.get("cold", True)
        epoch = self.ctx.adb.device_epoch()
        args = ["am", "start", "-W"] + (["-S"] if cold else []) + ["-n", self.ctx.component]
        out = self.ctx.adb.shell(*args, timeout=config.get("timeout_s", 60))
        parsed = parse_am_start(out.stdout)
        time.sleep(1.5)
        lines = self.ctx.logcat.lines_since(epoch - 1)
        ttid = parse_displayed(lines, self.ctx.package, "Displayed")
        ttfd = parse_displayed(lines, self.ctx.package, "Fully drawn")
        sid = result.run_scenario_id
        method = "am start -W TotalTime"
        if "total_time_ms" in parsed:
            key = "performance.startup_cold_ms" if cold else "performance.startup_warm_ms"
            result.measurements.append(MeasurementRecord(parameter_key=key, value=parsed["total_time_ms"], unit="ms", source="android_api", status=M.ACTUAL,
                                                         collection_method=method, run_scenario_id=sid, evidence={"am_start": parsed}))
        result.measurements.append(MeasurementRecord(parameter_key="performance.ttid_ms", value=ttid, unit="ms", source="android_api",
                                                     status=M.ACTUAL if ttid is not None else M.UNAVAILABLE, run_scenario_id=sid,
                                                     collection_method="ActivityTaskManager 'Displayed' logcat line",
                                                     reason=None if ttid is not None else "No 'Displayed' line for this launch"))
        result.measurements.append(MeasurementRecord(parameter_key="performance.ttfd_ms", value=ttfd, unit="ms", source="android_api",
                                                     status=M.ACTUAL if ttfd is not None else M.UNAVAILABLE, run_scenario_id=sid,
                                                     collection_method="'Fully drawn' logcat line (Activity.reportFullyDrawn)",
                                                     reason=None if ttfd is not None else "App does not call reportFullyDrawn(); TTFD cannot be measured"))
        pid = self.ctx.adb.pidof(self.ctx.package)
        return {"launch_status": parsed.get("status", "unknown"), "duration_ms": parsed.get("total_time_ms"), "launch_state": parsed.get("launch_state"),
                "pid": pid, "pid_changed": pid is not None and pid != self.state.get("pid_before_kill"), "am_start": out.stdout[-400:]}

    def _scroll(self, config: dict) -> dict:
        size = self.ctx.adb.shell("wm", "size", timeout=10).stdout
        dims = re.findall(r"(\d+)x(\d+)", size)
        w, h = (int(dims[-1][0]), int(dims[-1][1])) if dims else (720, 1600)
        count = min(int(config.get("count", 10)), 200)
        duration = min(max(int(config.get("duration_ms", 300)), 50), 3000)
        x, top, bottom = w // 2, int(h * 0.25), int(h * 0.75)
        for i in range(count):
            if self.ctx.cancelled():
                break
            down = config.get("direction", "down") == "down" if config.get("direction") != "alternate" else i % 2 == 0
            y1, y2 = (bottom, top) if down else (top, bottom)
            self.ctx.adb.shell("input", "swipe", str(x), str(y1), str(x), str(y2), str(duration), timeout=15)
        return {"swipes": count, "screen": [w, h]}

    # -- action handlers: return observed values used by expectations ------------------------
    def a_require_condition(self, c, r):
        """Block the scenario when the environment condition it is designed around was not requested."""
        req = self.ctx.request
        present = {"storage_constraint": req.storage.free_target_mb is not None or req.storage.free_target_pct is not None,
                   "memory_pressure": req.memory.pressure_enabled and req.memory.target_available_mb is not None,
                   "network_timeline": bool(req.network.timeline),
                   "network_shaping": bool(req.network.latency_ms or req.network.download_kbps or req.network.packet_loss_pct)}
        key = c.get("condition", "")
        if key not in present:
            raise ScenarioDefinitionError(f"unknown required condition '{key}'")
        if not present[key]:
            raise StepBlocked(f"requires the '{key}' environment condition, which this run did not request")
        return {}

    def a_force_stop(self, c, r):
        self.ctx.adb.force_stop(self.ctx.package)
        return {"process_alive": self.ctx.adb.pidof(self.ctx.package) is not None}

    def a_launch_app(self, c, r):
        return self._launch(c, r)

    def a_relaunch(self, c, r):
        return self._launch({**c, "cold": c.get("cold", False)}, r)

    def a_wait(self, c, r):
        seconds = min(float(c.get("seconds", 2)), 300)
        end = time.monotonic() + seconds
        while time.monotonic() < end and not self.ctx.cancelled():
            time.sleep(0.5)
        return {"waited_s": seconds}

    def a_screenshot(self, c, r):
        return {"screenshot": self._screenshot(c.get("label", "screen"), r)}

    def a_tap(self, c, r):
        selector = self._selector(c)
        if selector is None:
            return {"skipped": "optional binding not configured"}
        node = self.ui.find(selector, timeout_s=float(c.get("timeout_s", 10)))
        if node is None:
            return {"visible": False, "tapped": False}
        return {"visible": True, "tapped": True, "at": self.ui.tap(node)}

    def a_type_text(self, c, r):
        text = str(c.get("text", ""))
        if not _TYPEABLE.fullmatch(text):
            raise ScenarioDefinitionError("type_text only accepts letters, digits, space and ._@-")
        self.ctx.adb.shell("input", "text", text.replace(" ", "%s"), timeout=20)
        return {"typed_chars": len(text)}

    def a_scroll(self, c, r):
        return self._scroll(c)

    def a_press_key(self, c, r):
        self.ctx.adb.input_keyevent(c.get("key", "KEYCODE_BACK"))
        return {"key": c.get("key")}

    def a_background_app(self, c, r):
        self.ctx.adb.input_keyevent("KEYCODE_HOME")
        time.sleep(1)
        return {}

    def a_assert_visible(self, c, r):
        selector = self._selector(c)
        if selector is None:
            return {"skipped": "optional binding not configured"}
        node = self.ui.find(selector, timeout_s=float(c.get("timeout_s", 15)))
        return {"visible": node is not None, "matched": {k: node.get(k) for k in ("text", "resource-id", "content-desc")} if node else None}

    def a_assert_app_healthy(self, c, r):
        return self._health(self.state["scenario_epoch"])

    def a_kill_process(self, c, r):
        self.state["pid_before_kill"] = pid = self.ctx.adb.pidof(self.ctx.package)
        self.ctx.adb.input_keyevent("KEYCODE_HOME")
        time.sleep(1.5)
        self.ctx.adb.shell("am", "kill", self.ctx.package, timeout=20)
        time.sleep(1.5)
        after = self.ctx.adb.pidof(self.ctx.package)
        if after is not None and c.get("allow_force", True):
            self.ctx.adb.shell("am", "force-stop", self.ctx.package, timeout=20)   # fallback: e.g. foreground service keeps it alive
            after = self.ctx.adb.pidof(self.ctx.package)
            method = "am force-stop (am kill did not stop the process)"
        else:
            method = "am kill (background process death, as the low-memory killer would)"
        self.state["pocketlab_kills"] = self.state.get("pocketlab_kills", 0) + (1 if pid and after is None else 0)
        return {"killed": pid is not None and after is None, "pid_before": pid, "method": method}

    def a_network_offline(self, c, r):
        return self._network_online(False)

    def a_network_online(self, c, r):
        return self._network_online(True)

    def _network_online(self, online: bool):
        if self.ctx.network is None:
            raise StepBlocked("network control unavailable in this environment")
        record = self.ctx.network.connectivity.set_online(online)
        self.ctx.record_condition(record)
        return {"network_state": "online" if online else "offline", "verified": record.application_status is A.APPLIED_VERIFIED,
                "observed": record.observed_value}

    def a_network_apply(self, c, r):
        if self.ctx.network is None:
            raise StepBlocked("network control unavailable in this environment")
        records = self.ctx.network.apply_condition(NetworkCondition.model_validate(c.get("condition", {})))
        for record in records:
            self.ctx.record_condition(record)
        applied = [x for x in records if x.application_status not in (A.REQUIRES_LINUX_GATEWAY, A.UNSUPPORTED)]
        return {"verified": all(x.application_status in (A.APPLIED_VERIFIED, A.EMULATED) for x in applied),
                "not_applied": [x.parameter_key for x in records if x.application_status in (A.REQUIRES_LINUX_GATEWAY, A.UNSUPPORTED)]}

    def a_network_restore(self, c, r):
        if self.ctx.network is None:
            raise StepBlocked("network control unavailable in this environment")
        for record in self.ctx.network.apply_base():
            self.ctx.record_condition(record)
        return {"network_state": "online"}

    def a_network_timeline_start(self, c, r):
        if self.ctx.network is None:
            raise StepBlocked("network control unavailable in this environment")
        stages = [TimelineStage.model_validate(s) for s in c["stages"]] if c.get("stages") else self.ctx.request.network.timeline
        if not stages:
            if c.get("optional"):
                return {"skipped": "no timeline requested"}
            raise StepBlocked("scenario needs a network timeline; none was requested")
        self.state["timeline"] = self.ctx.network.start_timeline(stages, self.ctx.emit_event, self.ctx.cancelled)
        return {"stages": len(stages), "seed": self.state["timeline"].seed}

    def a_network_timeline_await(self, c, r):
        timeline = self.state.get("timeline")
        if timeline is None:
            return {"skipped": "no timeline running"}
        done = timeline.await_completion(float(c.get("timeout_s", 1800)))
        return {"timeline_completed": done, "verified": all(s["verified"] for s in timeline.results), "stages": timeline.results}

    def a_network_switch(self, c, r):
        to = c.get("to", "mobile")
        before = self.ctx.network.connectivity.state() if self.ctx.network else None
        self.ctx.adb.shell("svc", "wifi", "disable" if to == "mobile" else "enable", timeout=20)
        time.sleep(float(c.get("settle_s", 6)))
        after = self.ctx.network.connectivity.state() if self.ctx.network else None
        transport = self.ctx.adb.shell("dumpsys", "connectivity", timeout=30).stdout
        has_cell = "CELLULAR" in transport
        return {"handover": before is not None and after not in (None, before, "none"), "default_network_before": before,
                "default_network_after": after, "cellular_transport_seen": has_cell}

    def a_network_probe(self, c, r):
        if self.ctx.network is None or self.ctx.network.probe is None:
            raise StepBlocked("network probe unavailable")
        r.measurements += self.ctx.network.probe.measure(int(c.get("count", 20)), run_scenario_id=r.run_scenario_id, label=c.get("label", ""))
        return {}

    def a_memory_pressure_apply(self, c, r):
        target = c.get("target_available_mb") or self.ctx.request.memory.target_available_mb
        if not target:
            raise StepBlocked("scenario needs memory.target_available_mb")
        record = self.ctx.memory.apply_pressure(int(target))
        self.ctx.record_condition(record)
        if record.application_status is A.UNSUPPORTED:
            raise StepBlocked("bounded memory pressure needs a rootable image (google_apis)")
        r.measurements.append(MeasurementRecord(parameter_key="memory.available_mb", value=record.observed_value, unit="MB", source="android_api",
                                                status=M.ACTUAL, collection_method="/proc/meminfo MemAvailable under applied pressure",
                                                run_scenario_id=r.run_scenario_id))
        return {"verified": record.application_status is A.APPLIED_VERIFIED, "available_mb": record.observed_value}

    def a_memory_pressure_release(self, c, r):
        record = self.ctx.memory.release()
        if record:
            self.ctx.record_condition(record)
        kills = self.ctx.memory.kill_events(self.ctx.package, self.state["scenario_epoch"])
        return {"released": record is None or record.application_status is A.REVERTED, "kill_events": kills[:10]}

    def a_trim_memory(self, c, r):
        level = c.get("level", "RUNNING_CRITICAL")
        if level not in ("RUNNING_MODERATE", "RUNNING_LOW", "RUNNING_CRITICAL", "COMPLETE", "MODERATE", "BACKGROUND", "HIDDEN"):
            raise ScenarioDefinitionError("invalid trim level")
        out = self.ctx.adb.shell("am", "send-trim-memory", self.ctx.package, level, timeout=20)
        return {"signal_sent": out.ok, "note": "onTrimMemory signal only (EMULATED pressure), not real memory exhaustion"}

    def a_battery_saver(self, c, r):
        record = self.ctx.battery.set_saver(bool(c.get("enabled", True)))
        self.ctx.record_condition(record)
        return {"verified": record.application_status is A.APPLIED_VERIFIED}

    def a_frame_metrics_reset(self, c, r):
        self.ctx.collector.frame_metrics_reset()
        return {}

    def a_frame_metrics_collect(self, c, r):
        stats, records = self.ctx.collector.frame_metrics(scenario=r.run_scenario_id)
        r.measurements += records
        total = stats.get("total_frames") or 0
        return {"frames": total, "janky_pct": round(100 * stats.get("janky_frames", 0) / total, 2) if total else None, "gfxinfo": stats}

    def a_perfetto_start(self, c, r):
        if not self.ctx.perfetto_supported:
            return {"skipped": "perfetto needs API 28+"}
        seconds = min(int(c.get("duration_s", 15)), 120)
        remote = "/data/misc/perfetto-traces/pocketlab.pftrace"
        self.ctx.adb.shell_pipeline(f"perfetto -o {remote} -t {seconds}s --background sched freq idle am wm gfx view binder_driver", timeout=30)
        self.state["perfetto"] = (remote, time.monotonic() + seconds)
        return {"duration_s": seconds}

    def a_perfetto_stop(self, c, r):
        if "perfetto" not in self.state:
            return {"skipped": "no trace running"}
        remote, ready_at = self.state.pop("perfetto")
        time.sleep(max(0.0, ready_at - time.monotonic()) + 3)
        local = self.ctx.artifacts_dir / f"scenario_{r.run_scenario_id}_trace.pftrace"
        pulled = self.ctx.adb.pull(remote, local)
        if pulled.ok and local.exists():
            r.artifacts.append((local, "perfetto_trace", "application/octet-stream"))
        return {"trace": local.name if pulled.ok else None}

    def a_collect_app_metrics(self, c, r):
        r.measurements += self.ctx.collector.app_metrics(scenario=r.run_scenario_id)
        return {}

    def a_storage_errors_check(self, c, r):
        lines = [l for l in self.ctx.logcat.lines_since(self.state["scenario_epoch"]) if self.ctx.package in l or "AndroidRuntime" in l]
        errors = count_storage_errors(lines)
        r.measurements.append(MeasurementRecord(parameter_key="storage.file_creation_errors", value=errors, unit="count", source="android_api",
                                                status=M.ACTUAL, collection_method="logcat ENOSPC/SQLiteFull lines (lower bound)",
                                                run_scenario_id=r.run_scenario_id))
        return {"storage_errors": errors}

    def a_network_errors_check(self, c, r):
        lines = [l for l in self.ctx.logcat.lines_since(self.state["scenario_epoch"]) if self.ctx.package in l or "Exception" in l]
        dns = sum(1 for l in lines if "UnknownHostException" in l)
        timeouts = sum(1 for l in lines if "SocketTimeoutException" in l or "InterruptedIOException: timeout" in l)
        method = "logged exceptions in app logcat (lower bound; apps may not log)"
        r.measurements += [MeasurementRecord(parameter_key="network.dns_failures", value=dns, unit="count", source="android_api", status=M.ACTUAL,
                                             collection_method=method, run_scenario_id=r.run_scenario_id),
                           MeasurementRecord(parameter_key="network.request_timeouts", value=timeouts, unit="count", source="android_api", status=M.ACTUAL,
                                             collection_method=method, run_scenario_id=r.run_scenario_id)]
        return {"dns_failures": dns, "timeouts": timeouts}

    def a_thermal_sample(self, c, r):
        r.measurements += self.ctx.thermal.measurements(self.ctx.thermal.snapshot(), run_scenario_id=r.run_scenario_id)
        return {}

    def a_sustained_workload(self, c, r):
        seconds = min(int(c.get("seconds") or self.ctx.request.thermal.sustained_workload_seconds or 60), 1800)
        interval = max(10, int(c.get("interval_s", 30)))
        start = time.monotonic()
        next_sample = start
        while time.monotonic() - start < seconds and not self.ctx.cancelled():
            if time.monotonic() >= next_sample:
                r.measurements += self.ctx.thermal.measurements(self.ctx.thermal.snapshot(), run_scenario_id=r.run_scenario_id)
                next_sample += interval
            self._scroll({"count": 4, "direction": "alternate", "duration_ms": 250})
        elapsed = round(time.monotonic() - start, 1)
        r.measurements.append(MeasurementRecord(parameter_key="thermal.sustained_workload_seconds", value=elapsed, unit="s", source="runner",
                                                status=M.ACTUAL, collection_method="wall clock of UI scroll workload", run_scenario_id=r.run_scenario_id))
        return {"elapsed_s": elapsed}

    # -- expectations ------------------------------------------------------------------------
    @staticmethod
    def evaluate(expect: dict, observed: dict) -> list[dict]:
        results = []
        for name, expected in expect.items():
            if name == "max_duration_ms":
                actual = observed.get("duration_ms")
                passed = actual is not None and actual <= expected
            elif name == "min_frames":
                actual = observed.get("frames")
                passed = actual is not None and actual >= expected
            elif name == "max_janky_pct":
                actual = observed.get("janky_pct")
                passed = actual is not None and actual <= expected
            elif name == "max_storage_errors":
                actual = observed.get("storage_errors")
                passed = actual is not None and actual <= expected
            else:
                actual = observed.get(name)
                passed = actual == expected
            results.append({"name": name, "expected": expected, "actual": actual, "passed": bool(passed)})
        return results

    def run(self, run_scenario_id: int, name: str, version: int | None, steps: list[dict]) -> ScenarioResult:
        result = ScenarioResult(run_scenario_id=run_scenario_id, name=name, version=version, started_at=datetime.now(timezone.utc).isoformat())
        self.state = {"scenario_epoch": self.ctx.adb.device_epoch() - 1}
        unknown = [s.get("action_type") for s in steps if s.get("action_type") not in ACTIONS]
        if not steps or unknown:
            result.status = "blocked"
            result.reason = (f"Scenario version {version} has non-executable steps {unknown}; select an executable (v2+) version"
                             if unknown else "Scenario has no steps")
            result.completed_at = datetime.now(timezone.utc).isoformat()
            return result
        try:
            for index, step in enumerate(sorted(steps, key=lambda s: s.get("step_order", 0))):
                if self.ctx.cancelled():
                    result.status, result.reason = "error", "canceled"
                    break
                action, config = step["action_type"], step.get("configuration") or {}
                expect = step.get("expected") or {}
                sr = StepResult(index=index, action=action, started_at=datetime.now(timezone.utc).isoformat())
                t = time.monotonic()
                try:
                    observed = ACTIONS[action](self, config, result) or {}
                    if "skipped" in observed:
                        sr.status = "skipped"
                    else:
                        sr.expectations = self.evaluate(expect, observed)
                        sr.status = "failed" if any(not e["passed"] for e in sr.expectations) else ("passed" if sr.expectations else "done")
                    sr.evidence = {k: v for k, v in observed.items() if k not in ("failures",)}
                    if observed.get("failures"):
                        result.failures = observed["failures"]
                    if sr.status == "failed" and config.get("screenshot_on_failure", True):
                        try:
                            sr.evidence["failure_screenshot"] = self._screenshot(f"fail_{action}", result)
                        except EmulatorError:
                            pass
                except StepBlocked as exc:
                    sr.status, sr.error = "blocked", str(exc)
                    result.steps.append(sr)
                    result.status, result.reason = "blocked", f"Step {index} ({action}) {exc}"
                    break
                except EmulatorError as exc:
                    sr.status, sr.error = "error", exc.message
                    result.steps.append(sr)
                    result.status, result.reason = "error", f"Step {index} ({action}) automation error: {exc.message}"
                    break
                finally:
                    sr.duration_ms = int((time.monotonic() - t) * 1000)
                if sr not in result.steps:
                    result.steps.append(sr)
            if result.status == "running":
                health = self._health(self.state["scenario_epoch"])
                result.failures = health["failures"]
                implicit_failed = not health["no_crash"] or not health["no_anr"]
                evaluated = [e for s in result.steps for e in s.expectations]
                if implicit_failed:
                    result.status = "failed"
                    result.reason = "Application crashed or reported an ANR during the scenario"
                elif any(not e["passed"] for e in evaluated):
                    first = next(e for s in result.steps for e in s.expectations if not e["passed"])
                    result.status, result.reason = "failed", f"Expectation '{first['name']}' expected {first['expected']!r}, observed {first['actual']!r}"
                elif evaluated:
                    result.status = "passed"
                else:
                    result.status, result.reason = "inconclusive", "No expectation could be evaluated (optional bindings missing?)"
        finally:
            timeline = self.state.get("timeline")
            if timeline and timeline.is_alive():
                timeline.stop()
                timeline.await_completion(30)
            if self.ctx.memory.active:
                record = self.ctx.memory.release()
                if record:
                    self.ctx.record_condition(record)
            result.completed_at = datetime.now(timezone.utc).isoformat()
        return result


ACTIONS: dict[str, Callable[[ScenarioExecutor, dict, ScenarioResult], dict]] = {
    name[2:]: fn for name, fn in vars(ScenarioExecutor).items() if name.startswith("a_") and callable(fn)
}

# device_quote is re-exported for scenario helpers that build fixed device scripts.
__all__ = ["ScenarioExecutor", "ScenarioContext", "ScenarioResult", "load_definitions", "ACTIONS", "device_quote"]
