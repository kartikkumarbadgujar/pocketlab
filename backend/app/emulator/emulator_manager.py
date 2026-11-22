"""Lifecycle orchestration of one disposable Android execution environment.

QUEUED -> PREFLIGHT -> PROVISIONING -> BOOTING -> READY -> INSTALLING -> APPLYING_CONDITIONS ->
VERIFYING_CONDITIONS -> EXECUTING -> COLLECTING_METRICS -> ANALYZING -> COMPLETED -> CLEANING -> CLEANED
(any active state -> FAILED | CANCEL_REQUESTED -> CLEANING -> CLEANED)

Cleanup always runs (finally), including after failures and cancellation. Every transition is reported.
"""
from __future__ import annotations

import json
import logging
import shutil
import socket
import sys
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from app.emulator import planner
from app.emulator.adb_manager import AdbManager, list_devices
from app.emulator.apk_installer import ApkInstaller
from app.emulator.avd_manager import AvdManager
from app.emulator.capability_detector import CapabilityDetector
from app.emulator.console_manager import EmulatorConsole
from app.emulator.emulator_cleanup import StateFile, remove_run_directory, stop_emulator, sweep_orphans
from app.emulator.emulator_config import AndroidSdk, WorkerSettings, avd_name_for_run, build_avd_spec
from app.emulator.emulator_monitor import Heartbeat, LogcatCapture, ProcessWatch, detect_failures
from app.emulator.environment_manager import EnvironmentManager, ensure_strict
from app.emulator.exceptions import (
    ApkValidationError, CancelledError, DeviceNotReadyError, EmulatorError, PreflightError, ResourceUnavailableError,
)
from app.emulator.measurement_collector import MeasurementCollector
from app.emulator.network_manager import NetemGateway
from app.emulator.process import ProcessRunner
from app.emulator.scenario_executor import ScenarioContext, ScenarioExecutor, ScenarioResult
from app.emulator.schemas import (
    ApplicationStatus as A, ConditionRecord, EnvironmentRequest, EnvironmentStatus as S, MeasurementRecord,
)

logger = logging.getLogger("pocketlab.emulator.manager")


class Reporter(Protocol):
    def set_status(self, status: S, message: str, **fields: Any) -> dict: ...
    def event(self, event_type: str, message: str, severity: str = "info", data: dict | None = None) -> None: ...
    def conditions(self, records: list[ConditionRecord]) -> None: ...
    def measurements(self, records: list[MeasurementRecord], device_id: int) -> None: ...
    def finding(self, **fields: Any) -> None: ...
    def artifact(self, path: Path, artifact_type: str, content_type: str) -> int | None: ...
    def scenario_result(self, run_scenario_id: int, status: str, result: dict) -> None: ...
    def register_device(self, properties: dict) -> int: ...
    def heartbeat(self) -> bool: ...
    def download_apk(self, destination: Path) -> Path: ...


@dataclass
class RunJob:
    run_id: int
    attempt: int
    environment_id: int
    environment_type: str
    request: EnvironmentRequest
    package_name: str
    apk_sha256: str
    scenarios: list[dict]
    actual_device_id: int | None = None

    @classmethod
    def from_claim(cls, job: dict) -> "RunJob":
        env = job.get("environment") or {}
        build = job.get("build") or {}
        if not env or not build:
            raise PreflightError("Claimed job has no environment or approved build")
        devices = [p.get("actual_device_id") for p in job.get("profiles", []) if p.get("actual_device_id")]
        return cls(run_id=job["run_id"], attempt=job.get("attempt_count", 1), environment_id=env["id"], environment_type=env["environment_type"],
                   request=EnvironmentRequest.model_validate(env["requested_configuration"]), package_name=build["package_name"],
                   apk_sha256=build["apk_sha256"], scenarios=job.get("scenarios", []), actual_device_id=devices[0] if devices else None)


# host resources
class HostLock:
    """Process-wide exclusive lock: one emulator test per worker host by default."""

    def __init__(self, path: Path):
        self.path = path
        self._handle = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a+")
        try:
            if sys.platform == "win32":
                import msvcrt
                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._handle.close()
            self._handle = None
            raise ResourceUnavailableError("Another PocketLab emulator job holds the host lock") from exc

    def release(self) -> None:
        if not self._handle:
            return
        try:
            if sys.platform == "win32":
                import msvcrt
                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None


def allocate_console_port(runner: ProcessRunner) -> int:
    in_use = {serial for serial, _ in list_devices(runner)}
    for port in range(5554, 5684, 2):
        if f"emulator-{port}" in in_use:
            continue
        ok = True
        for p in (port, port + 1):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                try:
                    s.bind(("127.0.0.1", p))
                except OSError:
                    ok = False
        if ok:
            return port
    raise ResourceUnavailableError("No free emulator console/adb port pair in 5554-5683")


def check_host_capacity(settings: WorkerSettings, request: EnvironmentRequest) -> dict:
    import psutil
    vm = psutil.virtual_memory()
    free_mb = vm.available // (1024 * 1024)
    need = request.device.ram_mb + settings.host_ram_overhead_mb
    disk_free = shutil.disk_usage(settings.work_dir).free // (1024 * 1024)
    disk_need = request.device.data_partition_mb + request.device.sdcard_mb + 2048
    load = psutil.cpu_percent(interval=1.0)
    if free_mb < need:
        raise ResourceUnavailableError(f"Host has {free_mb} MB RAM available; {need} MB required")
    if disk_free < disk_need:
        raise ResourceUnavailableError(f"Host has {disk_free} MB disk free in the work directory; {disk_need} MB required")
    if (psutil.cpu_count() or 1) < request.device.cpu_cores:
        raise ResourceUnavailableError("Requested guest CPU cores exceed host logical CPUs")
    return {"ram_available_mb": free_mb, "ram_reserved_mb": need, "disk_free_mb": disk_free, "cpu_load_pct": load}


# manager
@dataclass
class _Run:
    job: RunJob
    reporter: Reporter
    run_dir: Path
    cancel: threading.Event = field(default_factory=threading.Event)
    status: S = S.QUEUED
    device_id: int | None = None
    adb: AdbManager | None = None
    console: EmulatorConsole | None = None
    emulator_proc: Any = None
    avd_name: str | None = None
    gateway: NetemGateway | None = None
    env: EnvironmentManager | None = None
    logcat: LogcatCapture | None = None
    watch: ProcessWatch | None = None
    installed: bool = False
    runner: ProcessRunner | None = None
    conditions: list[ConditionRecord] = field(default_factory=list)
    effective: dict = field(default_factory=dict)


class EmulatorManager:
    def __init__(self, settings: WorkerSettings):
        self.settings = settings
        self.state = StateFile(settings.work_dir / "worker_state.json")
        self.lock = HostLock(settings.work_dir / "emulator.lock")
        self.detector = CapabilityDetector(settings)
        self.last_error: Exception | None = None

    # helpers
    def _transition(self, run: _Run, status: S, message: str, **fields: Any) -> None:
        if run.cancel.is_set() and status not in (S.CANCEL_REQUESTED, S.CLEANING, S.CLEANED, S.FAILED):
            raise CancelledError("Cancellation requested")
        response = run.reporter.set_status(status, message, **fields) or {}
        run.status = status
        logger.info("environment_transition", extra={"run_id": run.job.run_id, "status": status.value})
        if response.get("cancel_requested"):
            run.cancel.set()
            if status not in (S.CLEANING, S.CLEANED, S.FAILED):
                raise CancelledError("Cancellation requested")

    def _check_cancel(self, run: _Run) -> None:
        if run.cancel.is_set():
            raise CancelledError("Cancellation requested")

    def _record(self, run: _Run):
        def record(r: ConditionRecord) -> None:
            run.conditions.append(r)
            run.reporter.conditions([r])
        return record

    def recover_orphans(self, active: set[int] = frozenset()) -> list[dict]:
        try:
            sdk = AndroidSdk.locate(self.settings.sdk_root)
        except EmulatorError:
            sdk = None
        avd = None
        if sdk:
            _, _, runner = self.detector.detect()
            avd = AvdManager(runner, self.settings.avd_home, sdk.root)
        return sweep_orphans(self.state, avd, self.settings.runs_dir, active_run_ids=set(active))

    # main entry point
    def execute(self, job: RunJob, reporter: Reporter) -> str:
        run_dir = self.settings.runs_dir / f"run_{job.run_id}_a{job.attempt}"
        run_dir.mkdir(parents=True, exist_ok=True)
        run = _Run(job=job, reporter=reporter, run_dir=run_dir)
        heartbeat = Heartbeat(reporter.heartbeat, self.settings.heartbeat_interval_s, run.cancel.set)
        heartbeat.start()
        outcome, error = "failed", None
        locked = False
        try:
            self._transition(run, S.PREFLIGHT, "Re-checking host capabilities on the worker")
            report, sdk, runner = self.detector.detect()
            run.runner = runner
            plan = planner.plan_environment(job.request, report)
            reporter.event("preflight", "Worker preflight " + ("passed" if plan.ok else "failed"), "info" if plan.ok else "error",
                           {"blocking_errors": plan.blocking_errors, "warnings": plan.warnings, "capabilities": report.summary()})
            if not plan.ok or sdk is None:
                raise PreflightError("; ".join(plan.blocking_errors) or "Android SDK unavailable")
            self.lock.acquire()
            locked = True
            reporter.event("resources_reserved", "Host lock acquired and capacity verified",
                           data=check_host_capacity(self.settings, job.request) if job.environment_type == "emulator" else {})
            if job.environment_type == "emulator":
                self._provision_and_boot(run, runner, sdk, plan)
            else:
                self._attach_physical(run, runner)
            self._ready(run)
            self._install(run, runner)
            self._conditions(run, plan)
            results = self._execute_scenarios(run, report)
            self._collect(run)
            self._analyze(run, results)
            self._transition(run, S.COMPLETED, "Execution finished; results analysed")
            outcome = "completed"
        except CancelledError:
            outcome = "canceled"
            _quiet(reporter.event, "canceled", "Run canceled at user request; cleaning up", "warning")
            self._safe_status(run, S.CANCEL_REQUESTED, "Cancellation acknowledged by worker")
        except EmulatorError as exc:
            error = exc
            _quiet(reporter.event, "failure", f"{exc.code}: {exc.message}", "error", {"code": exc.code, "details": _safe(exc.details)})
            self._safe_status(run, S.FAILED, f"{exc.code}: {exc.message}")
        except Exception as exc:   # never leave an emulator running because of an unexpected bug
            error = exc
            logger.exception("worker_unexpected_error")
            _quiet(reporter.event, "failure", f"Unexpected worker error: {exc}", "error")
            self._safe_status(run, S.FAILED, f"Unexpected worker error: {exc}")
        finally:
            try:
                self._cleanup(run)
            finally:
                heartbeat.stop()
                if locked:
                    self.lock.release()
        self.last_error = error
        if outcome == "failed" and error is not None:
            try:
                reporter.event("outcome", f"Run failed: {getattr(error, 'message', str(error))}", "error")
            except Exception:
                pass
        return outcome

    def _safe_status(self, run: _Run, status: S, message: str) -> None:
        try:
            run.reporter.set_status(status, message)
            run.status = status
        except Exception:
            logger.exception("status_report_failed")

    # stages
    def _provision_and_boot(self, run: _Run, runner: ProcessRunner, sdk: AndroidSdk, plan) -> None:
        job, s = run.job, self.settings
        image = plan.selected_system_image
        self._transition(run, S.PROVISIONING, f"Creating disposable AVD from {image.package}")
        port = allocate_console_port(runner)
        run.avd_name = avd_name_for_run(job.run_id, job.attempt, uuid.uuid4().hex)
        spec = build_avd_spec(job.request, image, run.avd_name, gpu_mode=s.gpu_mode, headless=s.headless, port=port)
        avd = AvdManager(runner, s.avd_home, sdk.root)
        created = avd.create(spec)
        run.reporter.event("avd_created", f"AVD {run.avd_name} created ({created['method']})", data={"config": created["config"]})
        prefix: list[str] = []
        if "network_gateway" in plan.capability_summary and plan.capability_summary["network_gateway"] == "supported":
            run.gateway = NetemGateway(runner, job.run_id, job.run_id)
            run.reporter.event("network_gateway", "Isolated network namespace created", data=run.gateway.setup())
            prefix = run.gateway.launch_prefix()
        self._transition(run, S.BOOTING, f"Launching emulator on port {port}", emulator_name=run.avd_name, emulator_serial=f"emulator-{port}",
                         android_api_level=image.api_level, system_image=image.package)
        log = (run.run_dir / "emulator.log").open("ab")
        run.emulator_proc = runner.popen([*prefix, str(runner.path("emulator")), *spec.launch_args], log_file=log)
        self.state.register(job.run_id, run.avd_name, run.emulator_proc.pid, port)
        run.reporter.event("emulator_started", "Emulator process started", data={"launch_args": spec.launch_args})
        run.adb = AdbManager(runner, f"emulator-{port}", prefix=prefix, default_timeout=s.adb_timeout_s)
        milestones = run.adb.wait_for_boot(s.boot_timeout_s, alive=lambda: run.emulator_proc.poll() is None, cancelled=run.cancel.is_set,
                                           on_progress=lambda m: run.reporter.event("boot_progress", m))
        run.reporter.event("booted", "Android finished booting", data={"milestones_s": milestones})
        run.console = EmulatorConsole(port, adb=run.adb)
        run.effective["avd"] = {"name": run.avd_name, "config": created["config"], "launch_args": spec.launch_args, "boot_milestones_s": milestones}

    def _attach_physical(self, run: _Run, runner: ProcessRunner) -> None:
        serial = self.settings.physical_devices.get(run.job.actual_device_id or -1)
        if not serial:
            raise PreflightError(f"Registered device {run.job.actual_device_id} is not mapped to an ADB serial on this worker")
        states = dict(list_devices(runner))
        if states.get(serial) != "device":
            raise DeviceNotReadyError(f"Physical device is {states.get(serial, 'not connected')} (needs 'device'; authorize USB debugging)")
        run.adb = AdbManager(runner, serial, default_timeout=self.settings.adb_timeout_s)
        run.device_id = run.job.actual_device_id
        self._transition(run, S.READY, "Authorized physical device attached", emulator_serial=None)

    def _ready(self, run: _Run) -> None:
        adb = run.adb
        assert adb is not None
        emulator = run.job.environment_type == "emulator"
        rooted = adb.try_root() if emulator and run.job.request.memory.pressure_enabled else False
        storage_tmp = EnvironmentManager(run.job.request, adb, run.console, self.settings, emulator=emulator, rooted=rooted,
                                         gateway=run.gateway, record=self._record(run))
        run.env = storage_tmp
        collector = MeasurementCollector(adb, run.job.package_name, emulator=emulator, memory=run.env.memory, storage=run.env.storage)
        props, device_records = collector.device_info()
        props["rooted"] = rooted
        run.effective["device"] = props
        if run.device_id is None:
            run.device_id = run.reporter.register_device({"device_name": f"emulator:{run.avd_name}", "manufacturer": props.get("manufacturer"),
                                                          "model": props.get("model"), "android_version": props.get("release"),
                                                          "api_level": int(props["api_level"]) if str(props.get("api_level")).isdigit() else None,
                                                          "cpu_architecture": props.get("abi"), "cpu_core_count": props.get("cpu_cores"),
                                                          "total_ram_mb": int(props["mem_total_mb"]) if props.get("mem_total_mb") else None,
                                                          "total_storage_bytes": props.get("storage_total_bytes"),
                                                          "display_width": props.get("display_width"), "display_height": props.get("display_height")})
        run.reporter.measurements(device_records, run.device_id)
        run.logcat = LogcatCapture(adb, run.run_dir / "logcat.txt")
        run.logcat.start()
        if emulator:
            self._transition(run, S.READY, "Device online over ADB; actual device properties recorded", effective_configuration=run.effective)
        else:
            run.reporter.set_status(S.READY, "Physical device properties recorded", effective_configuration=run.effective)
        run.env.verify_device(props)

    def _install(self, run: _Run, runner: ProcessRunner) -> None:
        job, adb = run.job, run.adb
        self._transition(run, S.INSTALLING, "Downloading and verifying the approved APK")
        apk = run.reporter.download_apk(run.run_dir / "app.apk")
        installer = ApkInstaller(runner, adb, install_timeout_s=self.settings.install_timeout_s)
        artifact = installer.verify_artifact(apk, job.apk_sha256)
        info = installer.inspect(apk)
        signature = installer.verify_signature(apk)
        snap = run.env.storage.snapshot()
        api = int(run.effective["device"].get("api_level") or 0)
        errors = installer.preinstall_checks(info, expected_package=job.package_name, device_api=api, free_bytes=snap.available_bytes,
                                             apk_size=artifact["size_bytes"])
        run.reporter.event("apk_verified", f"APK {info.package} v{info.version_name} (minSdk {info.min_sdk}) verified",
                           data={"artifact": artifact, "manifest_method": info.method, "signature": signature, "preinstall_errors": errors})
        if errors:
            raise ApkValidationError("; ".join(errors))
        result = installer.install(apk, info, grant_permissions=job.request.grant_runtime_permissions)
        run.installed = True
        component = adb.launcher_component(info.package)
        if not component:
            raise ApkValidationError("App installed but has no launchable (MAIN/LAUNCHER) activity")
        run.effective["app"] = {**result, "launcher_component": component}
        run.reporter.event("apk_installed", f"Installed; launcher activity {component}", data=_safe(result))
        run.watch = ProcessWatch(adb, info.package)
        run.watch.start()

    def _conditions(self, run: _Run, plan) -> None:
        env = run.env
        self._transition(run, S.APPLYING_CONDITIONS, "Applying requested storage, battery and network conditions")
        env.record_plan_limitations(plan)
        applied = env.apply_all()
        self._transition(run, S.VERIFYING_CONDITIONS, "Verifying applied conditions by independent read-back")
        verified = [r for r in run.conditions if r.application_status in (A.APPLIED_VERIFIED, A.EMULATED)]
        failed = [r.parameter_key for r in run.conditions if r.application_status is A.FAILED]
        run.reporter.event("conditions_verified", f"{len(verified)} conditions verified, {len(failed)} failed",
                           "error" if failed else "info", {"failed": failed})
        ensure_strict(run.conditions, run.job.request.strict_conditions)
        run.effective["conditions_applied"] = len(applied)

    def _execute_scenarios(self, run: _Run, report) -> list[ScenarioResult]:
        job, adb = run.job, run.adb
        self._transition(run, S.EXECUTING, f"Executing {len(job.scenarios)} scenario(s)")
        collector = MeasurementCollector(adb, job.package_name, emulator=job.environment_type == "emulator", memory=run.env.memory, storage=run.env.storage)
        self._collector = collector
        api = int(run.effective["device"].get("api_level") or 0)
        ctx = ScenarioContext(adb=adb, package=job.package_name, component=run.effective["app"]["launcher_component"], request=job.request,
                              collector=collector, memory=run.env.memory, battery=run.env.battery, thermal=run.env.thermal, network=run.env.network,
                              logcat=run.logcat, artifacts_dir=run.run_dir / "artifacts", emulator=job.environment_type == "emulator",
                              cancelled=run.cancel.is_set, emit_event=lambda t, m, d: run.reporter.event(t, m, data=d),
                              record_condition=self._record(run), perfetto_supported=api >= 28)
        results = []
        for scenario in job.scenarios:
            self._check_cancel(run)
            rs_id = scenario["run_scenario_id"]
            name = scenario.get("name") or f"scenario {scenario.get('scenario_version_id')}"
            run.reporter.set_status(S.EXECUTING, f"Running: {name}", active_run_scenario_id=rs_id)
            run.reporter.scenario_result(rs_id, "running", {})
            result = ScenarioExecutor(ctx).run(rs_id, name, scenario.get("version"), scenario.get("steps", []))
            results.append(result)
            for path, kind, content_type in result.artifacts:
                run.reporter.artifact(path, kind, content_type)
            run.reporter.measurements(result.measurements, run.device_id)
            run.reporter.scenario_result(rs_id, result.status, result.as_json())
            run.reporter.event("scenario_finished", f"{name}: {result.status}" + (f" - {result.reason}" if result.reason else ""),
                               "info" if result.status == "passed" else "warning", {"run_scenario_id": rs_id, "status": result.status})
        timeline = run.env.network.timeline
        if job.request.network.timeline:
            if timeline is None:
                self._record(run)(ConditionRecord(parameter_key="network.timeline", requested_value=len(job.request.network.timeline),
                                                  application_status=A.CONFIGURED, evidence={"reason": "No selected scenario replays the network timeline "
                                                                                                       "(select 'Product image loading under unstable connectivity')."}))
            else:
                ok = bool(timeline.results) and all(s["verified"] for s in timeline.results) and len(timeline.results) == len(job.request.network.timeline)
                self._record(run)(ConditionRecord(parameter_key="network.timeline", requested_value=len(job.request.network.timeline),
                                                  applied_value=len(timeline.results), application_status=A.APPLIED_VERIFIED if ok else A.FAILED,
                                                  mechanism="timed replay with per-transition verification",
                                                  evidence={"seed": timeline.seed, "stages": timeline.results, "error": timeline.error}))
        return results

    def _collect(self, run: _Run) -> None:
        self._transition(run, S.COLLECTING_METRICS, "Collecting final measurements and evidence")
        collector, adb = self._collector, run.adb
        records = collector.app_metrics()
        battery_state, _ = run.env.battery.read()
        records += collector.battery_metrics(battery_state)
        if run.job.request.thermal.observe:
            records += run.env.thermal.measurements(run.env.thermal.snapshot())
        lines = run.logcat.lines_since(0)
        failures = detect_failures(lines, run.job.package_name)
        run.effective["failures"] = {k: len(v) for k, v in failures.items()}
        kills = len(run.env.memory.kill_events(run.job.package_name, 0))
        records += collector.final_app_metrics(crashes=len(failures["crashes"]) + len(failures["native_crashes"]), anrs=len(failures["anrs"]),
                                               restarts=max(0, run.watch.restart_count()) if run.watch else 0, kills=kills)
        run.reporter.measurements(records, run.device_id)
        try:
            adb.screencap(run.run_dir / "artifacts" / "final_screen.png")
            run.reporter.artifact(run.run_dir / "artifacts" / "final_screen.png", "screenshot", "image/png")
        except EmulatorError:
            pass

    def _analyze(self, run: _Run, results: list[ScenarioResult]) -> None:
        self._transition(run, S.ANALYZING, "Deriving findings from scenario outcomes and logs")
        for result in results:
            if result.status != "failed":
                continue
            crash = result.failures.get("crashes") or result.failures.get("native_crashes")
            anr = result.failures.get("anrs")
            failed_exp = next((e for s in result.steps for e in s.expectations if not e["passed"]), None)
            screenshot = next((s.evidence.get("failure_screenshot") for s in result.steps if s.evidence.get("failure_screenshot")), None)
            run.reporter.finding(
                run_scenario_id=result.run_scenario_id,
                finding_type="app_crash" if crash else "app_anr" if anr else "scenario_expectation_failed",
                title=f"{result.name}: " + ("application crashed" if crash else "ANR detected" if anr else f"expectation '{failed_exp['name']}' failed" if failed_exp else "failed"),
                description=result.reason, severity="critical" if crash else "high" if anr else "medium", confidence=1.0 if (crash or anr) else 0.9,
                expected_behavior=f"{failed_exp['name']} = {failed_exp['expected']!r}" if failed_exp else "No crash or ANR",
                actual_behavior=(crash[0]["line"] if crash else anr[0]["line"] if anr else f"observed {failed_exp['actual']!r}" if failed_exp else result.reason),
                reproduction_steps=[{"step": s.index, "action": s.action, "status": s.status} for s in result.steps],
                evidence_reference=screenshot)
        unverified = [r for r in run.conditions if r.application_status is A.FAILED]
        if unverified and not run.job.request.strict_conditions:
            run.reporter.finding(finding_type="condition_not_verified", title="Some requested conditions were not verified",
                                 description=", ".join(sorted({r.parameter_key for r in unverified})), severity="medium", confidence=1.0,
                                 expected_behavior="All supported conditions verified before execution",
                                 actual_behavior="Execution continued with strict_conditions disabled")

    def _cleanup(self, run: _Run) -> None:
        self._safe_status(run, S.CLEANING, "Reverting conditions, stopping the device session and removing temporary resources")
        report: dict = {}
        verified = True
        steps = []
        if run.env is not None and run.adb is not None:
            steps.append(("revert_conditions", lambda: [r.parameter_key for r in run.env.revert_all() if r.application_status is A.FAILED]))
        if run.watch:
            steps.append(("process_watch", lambda: run.watch.stop() and None))
        if run.installed and run.adb is not None and run.job.environment_type == "physical_device":
            steps.append(("uninstall_app", lambda: run.adb.uninstall(run.job.package_name).returncode))
        if run.logcat:
            steps.append(("logcat_capture", lambda: str(run.logcat.stop().name)))
        if run.emulator_proc is not None:
            steps.append(("stop_emulator", lambda: stop_emulator(run.emulator_proc, run.console, run.adb)))
        if run.gateway is not None:
            steps.append(("network_gateway", run.gateway.teardown))
        for name, fn in steps:
            try:
                report[name] = fn()
            except Exception as exc:
                verified = False
                report[name] = {"error": str(exc)}
        if isinstance(report.get("revert_conditions"), list) and report["revert_conditions"]:
            verified = False
        if run.emulator_proc is not None and run.emulator_proc.poll() is None:
            verified = False
        for path, kind, ctype in ((run.run_dir / "logcat.txt", "logcat", "text/plain"), (run.run_dir / "emulator.log", "emulator_log", "text/plain")):
            if path.exists() and path.stat().st_size:
                try:
                    run.reporter.artifact(path, kind, ctype)
                except Exception:
                    pass
        env_report = run.run_dir / "environment_report.json"
        env_report.write_text(json.dumps({"requested": run.job.request.model_dump(mode="json"), "effective": _safe(run.effective),
                                          "conditions": [c.model_dump(mode="json") for c in run.conditions], "cleanup": _safe(report)}, indent=2, default=str))
        try:
            run.reporter.artifact(env_report, "environment_report", "application/json")
        except Exception:
            pass
        if run.avd_name:
            keep = self.settings.keep_failed_avd and run.status is S.FAILED
            if not keep:
                try:
                    sdk = AndroidSdk.locate(self.settings.sdk_root)
                    report["avd_deleted"] = AvdManager(run.runner, self.settings.avd_home, sdk.root).delete(run.avd_name)
                except EmulatorError as exc:
                    verified = False
                    report["avd_deleted"] = {"error": exc.message}
            self.state.unregister(run.job.run_id)
        try:
            report["run_dir_removed"] = remove_run_directory(run.run_dir, self.settings.runs_dir)
        except EmulatorError as exc:
            report["run_dir_removed"] = {"error": exc.message}
        logger.info("cleanup_finished", extra={"run_id": run.job.run_id, "verified": verified})
        _quiet(run.reporter.event, "cleanup", "Cleanup " + ("verified" if verified else "incomplete - see details"), "info" if verified else "error", _safe(report))
        self._safe_status_fields(run, S.CLEANED, "Temporary resources released", cleanup_verified=verified)

    def _safe_status_fields(self, run: _Run, status: S, message: str, **fields: Any) -> None:
        try:
            run.reporter.set_status(status, message, **fields)
        except Exception:
            logger.exception("status_report_failed")


def _quiet(fn, *args: Any) -> None:
    """Best-effort reporting on failure/cleanup paths: a lost lease or API outage must not stop local cleanup."""
    try:
        fn(*args)
    except Exception:
        logger.warning("report_failed_during_cleanup", exc_info=True)


def _safe(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, default=str))
    except (TypeError, ValueError):
        return str(value)
