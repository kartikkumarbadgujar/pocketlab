"""Network condition simulation with pluggable shaping backends.

EmulatorConsoleShaper  bandwidth + latency (+ jitter as a delay range) through the official emulator
                       network controls, read back with the console's 'network status'.
NetemGateway           Linux only. The emulator *and its adb server* run inside a dedicated per-run network
                       namespace; tc netem is applied on that namespace's veth pair, so only this emulator's
                       traffic is shaped. The developer's host interfaces are never touched by shaping.
Connectivity           OS-level offline/online (svc wifi/data + emulator 'gsm data'), verified via
                       dumpsys connectivity.
NetworkProbe           in-guest ICMP probe for measured RTT/loss/jitter. Configured values are never
                       reported as measured values.
NetworkTimeline        timed replay of stages during a running scenario, with exact transition timestamps.
"""
from __future__ import annotations

import getpass
import logging
import random
import re
import threading
import time
from datetime import datetime, timezone
from typing import Callable

from app.emulator.adb_manager import AdbManager
from app.emulator.console_manager import EmulatorConsole
from app.emulator.emulator_config import emulator_netdelay, emulator_netspeed
from app.emulator.exceptions import ConditionApplicationError, EmulatorError
from app.emulator.process import ProcessRunner
from app.emulator.schemas import ApplicationStatus as A, ConditionRecord, MeasurementRecord, MeasurementStatus as M, NetworkCondition, TimelineStage

logger = logging.getLogger("pocketlab.emulator.network")
now = lambda: datetime.now(timezone.utc)  # noqa: E731


# connectivity
class Connectivity:
    def __init__(self, adb: AdbManager, console: EmulatorConsole | None):
        self.adb = adb
        self.console = console

    def state(self) -> str | None:
        """'none' when Android has no default network, a network id otherwise; None if unparseable."""
        text = self.adb.shell("dumpsys", "connectivity", timeout=30).stdout
        m = re.search(r"Active default network:\s*(\S+)", text)
        return m.group(1) if m else None

    def set_online(self, online: bool, timeout_s: float = 20) -> ConditionRecord:
        verb = "enable" if online else "disable"
        outputs = [self.adb.shell("svc", "wifi", verb, timeout=20).evidence(200), self.adb.shell("svc", "data", verb, timeout=20).evidence(200)]
        if self.console:
            try:
                outputs.append({"console": self.console.command(f"gsm data {'on' if online else 'off'}")[-200:]})
            except EmulatorError as exc:
                outputs.append({"console_error": exc.message})
        deadline, observed = time.monotonic() + timeout_s, None
        while time.monotonic() < deadline:
            observed = self.state()
            if observed is not None and ((observed == "none") != online):
                break
            time.sleep(1)
        verified = observed is not None and ((observed == "none") != online)
        return ConditionRecord(parameter_key="network.online", requested_value=online, applied_value=online,
                               observed_value={"active_default_network": observed},
                               application_status=A.APPLIED_VERIFIED if verified else (A.APPLIED_UNVERIFIED if observed is None else A.FAILED),
                               mechanism="svc wifi/data" + (" + emulator gsm data" if self.console else "") + "; verified via dumpsys connectivity",
                               evidence={"commands": outputs}, verified_at=now())


# shapers
class NetworkShaper:
    name = "none"
    supports: set[str] = set()

    def apply(self, condition: NetworkCondition, *, seed: int | None = None) -> list[ConditionRecord]:
        raise NotImplementedError

    def reset(self) -> list[ConditionRecord]:
        return []


GATEWAY_KEYS = {"network.packet_loss_pct", "network.burst_loss", "network.packet_duplication_pct", "network.packet_reordering_pct", "network.dns_failure"}


def _requested(condition: NetworkCondition, key: str):
    return {"network.packet_loss_pct": condition.packet_loss_pct,
            "network.burst_loss": {"enter_pct": condition.burst_loss_enter_pct, "exit_pct": condition.burst_loss_exit_pct},
            "network.packet_duplication_pct": condition.duplication_pct, "network.packet_reordering_pct": condition.reorder_pct,
            "network.dns_failure": condition.dns_failure}[key]


class EmulatorConsoleShaper(NetworkShaper):
    name = "emulator_console"
    supports = {"network.bandwidth_kbps", "network.latency_ms", "network.jitter_ms"}

    def __init__(self, console: EmulatorConsole):
        self.console = console

    def apply(self, condition: NetworkCondition, *, seed: int | None = None) -> list[ConditionRecord]:
        speed = emulator_netspeed(condition) or "full"
        delay = emulator_netdelay(condition) or "none"
        responses = [self.console.command(f"network speed {speed}")[-200:], self.console.command(f"network delay {delay}")[-200:]]
        status = self.console.network_status()
        records = []
        if condition.download_kbps or condition.upload_kbps:
            up, down = (int(x) * 1000 for x in speed.split(":"))
            ok = status.get("download_bps") == down and status.get("upload_bps") == up
            records.append(ConditionRecord(parameter_key="network.bandwidth_kbps", requested_value={"down": condition.download_kbps, "up": condition.upload_kbps},
                                           applied_value=speed, unit="kbps",
                                           observed_value={"download_bps": status.get("download_bps"), "upload_bps": status.get("upload_bps")},
                                           application_status=A.APPLIED_VERIFIED if ok else A.APPLIED_UNVERIFIED,
                                           mechanism="emulator console 'network speed'; read back with 'network status'",
                                           evidence={"responses": responses, "network_status": status, "observed_meaning": "emulator shaper setting, not measured throughput"},
                                           verified_at=now()))
        if condition.latency_ms or condition.jitter_ms:
            lo, hi = (int(x) for x in delay.split(":"))
            ok = status.get("min_latency_ms") == lo and status.get("max_latency_ms") == hi
            records.append(ConditionRecord(parameter_key="network.latency_ms", requested_value=condition.latency_ms, applied_value=delay, unit="ms",
                                           observed_value={"min_latency_ms": status.get("min_latency_ms"), "max_latency_ms": status.get("max_latency_ms")},
                                           application_status=A.APPLIED_VERIFIED if ok else A.APPLIED_UNVERIFIED,
                                           mechanism="emulator console 'network delay min:max'; read back with 'network status'",
                                           evidence={"network_status": status}, verified_at=now()))
            if condition.jitter_ms:
                records.append(ConditionRecord(parameter_key="network.jitter_ms", requested_value=condition.jitter_ms, applied_value=delay, unit="ms",
                                               application_status=A.EMULATED if ok else A.APPLIED_UNVERIFIED,
                                               mechanism="emulator delay range (latency ± jitter)",
                                               evidence={"note": "Range, not a netem distribution; measured RTT spread is reported by the network probe."}))
        for key in sorted(GATEWAY_KEYS):
            value = _requested(condition, key)
            if value and value != {"enter_pct": None, "exit_pct": None}:
                records.append(ConditionRecord(parameter_key=key, requested_value=value, application_status=A.REQUIRES_LINUX_GATEWAY,
                                               evidence={"reason": "Android Emulator has no packet-level impairment control; needs the Linux netem gateway worker."}))
        return records

    def reset(self) -> list[ConditionRecord]:
        self.console.command("network speed full")
        self.console.command("network delay none")
        return [ConditionRecord(parameter_key="network.shaping_reset", requested_value="full/none", application_status=A.REVERTED,
                                evidence={"network_status": self.console.network_status()})]


class NetemGateway(NetworkShaper):
    """Per-run Linux network namespace with a veth pair; netem on both ends. Requires a sudoers allowlist."""
    name = "linux_netem_gateway"
    supports = {"network.bandwidth_kbps", "network.latency_ms", "network.jitter_ms"} | GATEWAY_KEYS

    def __init__(self, runner: ProcessRunner, run_id: int, index: int):
        self.runner = runner
        n = run_id % 10000
        self.ns, self.host_if, self.ns_if = f"pl{n}", f"plh{n}", f"pln{n}"
        octet = index % 250 + 1
        self.host_ip, self.ns_ip, self.subnet = f"10.213.{octet}.1", f"10.213.{octet}.2", f"10.213.{octet}.0/30"
        self.created = False

    def _sudo(self, *args: str, check: bool = True):
        return self.runner.run([str(self.runner.path("sudo")), "-n", *args], timeout=20, check=check)

    def _ip(self, *args: str, check: bool = True):
        return self._sudo(str(self.runner.path("ip")), *args, check=check)

    def _tc(self, *args: str, check: bool = True):
        return self._sudo(str(self.runner.path("tc")), *args, check=check)

    def setup(self) -> dict:
        ip = self._ip
        ip("netns", "add", self.ns)
        self.created = True
        ip("link", "add", self.host_if, "type", "veth", "peer", "name", self.ns_if)
        ip("link", "set", self.ns_if, "netns", self.ns)
        ip("addr", "add", f"{self.host_ip}/30", "dev", self.host_if)
        ip("link", "set", self.host_if, "up")
        ip("netns", "exec", self.ns, str(self.runner.path("ip")), "addr", "add", f"{self.ns_ip}/30", "dev", self.ns_if)
        ip("netns", "exec", self.ns, str(self.runner.path("ip")), "link", "set", self.ns_if, "up")
        ip("netns", "exec", self.ns, str(self.runner.path("ip")), "link", "set", "lo", "up")
        ip("netns", "exec", self.ns, str(self.runner.path("ip")), "route", "add", "default", "via", self.host_ip)
        # NAT for the namespace subnet only; installed by the gateway helper rule (see docs/ANDROID_WORKER.md).
        return {"namespace": self.ns, "veth": [self.host_if, self.ns_if], "subnet": self.subnet}

    def launch_prefix(self) -> list[str]:
        """Run emulator/adb inside the namespace, then drop back to the unprivileged worker user."""
        return [str(self.runner.path("sudo")), "-n", str(self.runner.path("ip")), "netns", "exec", self.ns,
                str(self.runner.path("sudo")), "-n", "-u", getpass.getuser(),
                "--preserve-env=ANDROID_SDK_ROOT,ANDROID_HOME,ANDROID_AVD_HOME,HOME"]

    @staticmethod
    def netem_args(condition: NetworkCondition, *, direction: str, seed: int | None) -> list[str]:
        args = ["netem"]
        if direction == "egress" and (condition.latency_ms or condition.jitter_ms):
            args += ["delay", f"{condition.latency_ms or 0}ms"]
            if condition.jitter_ms:
                args += [f"{condition.jitter_ms}ms", "distribution", "normal"]
        if not condition.online:
            args += ["loss", "100%"]
        elif condition.burst_loss_enter_pct:
            # Gilbert-Elliott: p = P(good->bad), r = P(bad->good)
            args += ["loss", "gemodel", f"{condition.burst_loss_enter_pct}%", f"{condition.burst_loss_exit_pct}%"]
        elif condition.packet_loss_pct:
            args += ["loss", f"{condition.packet_loss_pct}%"]
        if condition.duplication_pct:
            args += ["duplicate", f"{condition.duplication_pct}%"]
        if condition.reorder_pct and direction == "egress":
            args += ["reorder", f"{condition.reorder_pct}%", "50%"]
        rate = condition.upload_kbps if direction == "egress" else condition.download_kbps
        if rate:
            args += ["rate", f"{rate}kbit"]
        if seed is not None:
            args += ["seed", str(seed)]
        return args

    def apply(self, condition: NetworkCondition, *, seed: int | None = None) -> list[ConditionRecord]:
        egress = self.netem_args(condition, direction="egress", seed=seed)
        ingress = self.netem_args(condition, direction="ingress", seed=seed)
        self._ip("netns", "exec", self.ns, str(self.runner.path("tc")), "qdisc", "replace", "dev", self.ns_if, "root", *egress)
        self._tc("qdisc", "replace", "dev", self.host_if, "root", *ingress)
        dns_rules = []
        dns_verified = True
        if condition.dns_failure:
            for proto in ("udp", "tcp"):
                rule = ["OUTPUT", "-p", proto, "--dport", "53", "-j", "DROP"]
                inserted = self._ip("netns", "exec", self.ns, "iptables", "-I", *rule, check=False)
                readback = self._ip("netns", "exec", self.ns, "iptables", "-C", *rule, check=False)
                dns_verified = dns_verified and inserted.ok and readback.ok
                dns_rules.append({"insert": inserted.evidence(200), "readback": readback.evidence(200)})
        shown_ns = self._ip("netns", "exec", self.ns, str(self.runner.path("tc")), "qdisc", "show", "dev", self.ns_if).stdout
        shown_host = self._tc("qdisc", "show", "dev", self.host_if).stdout
        qdiscs_present = "netem" in shown_ns and "netem" in shown_host
        evidence = {"egress_netem": egress, "ingress_netem": ingress, "tc_show": {"ns": shown_ns[-400:], "host": shown_host[-400:]},
                    "dns_rules": dns_rules, "seed": seed,
                    "observed_meaning": "netem qdisc presence only; qdisc parameters are not parsed for exact read-back"}
        records = []
        # The qdisc output is retained as evidence, but only its presence is checked here;
        # do not claim that requested values were verified without comparing the settings.
        status = A.APPLIED_UNVERIFIED if qdiscs_present else A.FAILED
        for key, value, unit in (("network.bandwidth_kbps", {"down": condition.download_kbps, "up": condition.upload_kbps}, "kbps"),
                                 ("network.latency_ms", condition.latency_ms, "ms"), ("network.jitter_ms", condition.jitter_ms, "ms"),
                                 *((k, _requested(condition, k), "%" if k.endswith("_pct") else None) for k in sorted(GATEWAY_KEYS))):
            if value in (None, False, 0, {"down": None, "up": None}, {"enter_pct": None, "exit_pct": None}):
                continue
            item_status = (A.APPLIED_VERIFIED if dns_verified else A.FAILED) if key == "network.dns_failure" else status
            records.append(ConditionRecord(parameter_key=key, requested_value=value, applied_value=value, unit=unit, application_status=item_status,
                                           mechanism=("iptables DNS drop rules in the per-run namespace; verified with iptables -C"
                                                      if key == "network.dns_failure" else
                                                      "tc netem on per-run namespace veth pair; qdisc presence read back with tc qdisc show"),
                                           evidence=evidence,
                                           verified_at=now()))
        return records

    def reset(self) -> list[ConditionRecord]:
        self._ip("netns", "exec", self.ns, str(self.runner.path("tc")), "qdisc", "del", "dev", self.ns_if, "root", check=False)
        self._tc("qdisc", "del", "dev", self.host_if, "root", check=False)
        return [ConditionRecord(parameter_key="network.shaping_reset", requested_value="remove netem", application_status=A.REVERTED)]

    def teardown(self) -> dict:
        if not self.created:
            return {"removed": True}
        self._ip("netns", "del", self.ns, check=False)
        self._ip("link", "del", self.host_if, check=False)
        remaining = self._ip("netns", "list", check=False).stdout
        self.created = self.ns in remaining.split()
        return {"removed": not self.created}


# probe
def parse_ping(text: str) -> dict:
    out: dict = {}
    if m := re.search(r"(\d+) packets transmitted, (\d+) (?:packets )?received.*?([\d.]+)% packet loss", text, re.S):
        out.update(transmitted=int(m.group(1)), received=int(m.group(2)), loss_pct=float(m.group(3)))
    if m := re.search(r"= ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+) ms", text):
        out.update(rtt_min_ms=float(m.group(1)), rtt_avg_ms=float(m.group(2)), rtt_max_ms=float(m.group(3)), rtt_mdev_ms=float(m.group(4)))
    return out


class NetworkProbe:
    def __init__(self, adb: AdbManager, host: str):
        if not re.fullmatch(r"[A-Za-z0-9.\-]{1,253}", host):
            raise ValueError("Invalid probe host")
        self.adb, self.host = adb, host

    def measure(self, count: int = 20, *, run_scenario_id: int | None = None, label: str = "") -> list[MeasurementRecord]:
        result = self.adb.shell("ping", "-c", str(count), "-i", "0.2", "-W", "2", self.host, timeout=count * 3 + 15)
        stats = parse_ping(result.stdout)
        method = f"in-guest ICMP ping x{count} to {self.host}"
        evidence = {"ping": stats, "label": label, "output_tail": result.stdout[-400:]}
        if not stats or stats.get("received", 0) == 0:
            reason = "ICMP probe received no replies; emulator user-mode networking may not forward ICMP, so RTT/loss cannot be attributed."
            return [MeasurementRecord(parameter_key=k, unit=u, source=s, status=M.UNAVAILABLE, collection_method=method, reason=reason,
                                      run_scenario_id=run_scenario_id, evidence=evidence)
                    for k, u, s in (("network.rtt_ms", "ms", "network_probe"), ("network.packet_loss_pct", "%", "network_probe"),
                                    ("network.jitter_ms", "ms", "network_probe"))]
        return [
            MeasurementRecord(parameter_key="network.rtt_ms", value=stats["rtt_avg_ms"], unit="ms", source="network_probe", status=M.ACTUAL,
                              collection_method=method, run_scenario_id=run_scenario_id, evidence=evidence),
            MeasurementRecord(parameter_key="network.packet_loss_pct", value=stats["loss_pct"], unit="%", source="network_probe", status=M.ACTUAL,
                              collection_method=method + " (ICMP loss; a lower bound for application-level effects)", run_scenario_id=run_scenario_id, evidence=evidence),
            MeasurementRecord(parameter_key="network.jitter_ms", value=stats["rtt_mdev_ms"], unit="ms", source="network_probe", status=M.ACTUAL,
                              collection_method=method + " (RTT mean deviation)", run_scenario_id=run_scenario_id, evidence=evidence),
        ]


# facade + timeline
class NetworkManager:
    def __init__(self, shaper: NetworkShaper | None, connectivity: Connectivity, probe: NetworkProbe | None, base: NetworkCondition, seed: int | None):
        self.shaper, self.connectivity, self.probe, self.base, self.seed = shaper, connectivity, probe, base, seed
        self.timeline: NetworkTimeline | None = None
        self._lock = threading.Lock()

    def apply_condition(self, condition: NetworkCondition) -> list[ConditionRecord]:
        with self._lock:
            records: list[ConditionRecord] = []
            if self.shaper is None:
                for key in ("network.bandwidth_kbps", "network.latency_ms", "network.jitter_ms", *condition.needs_gateway()):
                    records.append(ConditionRecord(parameter_key=key, application_status=A.REQUIRES_LINUX_GATEWAY,
                                                   evidence={"reason": "No network shaper available for this environment."}))
            else:
                # Always re-apply the full condition (unset values reset to unshaped), so restoring the base
                # after a timeline stage really removes that stage's shaping.
                records += self.shaper.apply(condition, seed=self.seed)
            records.append(self.connectivity.set_online(condition.online))
            return records

    def apply_base(self) -> list[ConditionRecord]:
        return self.apply_condition(self.base)

    def start_timeline(self, stages: list[TimelineStage], on_event: Callable[[str, str, dict], None], cancelled: Callable[[], bool]) -> "NetworkTimeline":
        self.timeline = NetworkTimeline(self, stages, on_event, cancelled, self.seed)
        self.timeline.start()
        return self.timeline

    def reset(self) -> list[ConditionRecord]:
        records = []
        if self.timeline and self.timeline.is_alive():
            self.timeline.stop()
        if self.shaper:
            try:
                records += self.shaper.reset()
            except EmulatorError as exc:
                records.append(ConditionRecord(parameter_key="network.shaping_reset", application_status=A.FAILED, evidence={"error": exc.message}))
        records.append(self.connectivity.set_online(True))
        return records


class NetworkTimeline(threading.Thread):
    def __init__(self, manager: NetworkManager, stages: list[TimelineStage], on_event, cancelled, seed: int | None):
        super().__init__(name="pocketlab-network-timeline", daemon=True)
        self.manager, self.stages, self.on_event, self.cancelled = manager, stages, on_event, cancelled
        self.seed = seed if seed is not None else random.SystemRandom().randint(0, 2**31 - 1)
        self.results: list[dict] = []
        self._stop_event = threading.Event()
        self.error: str | None = None

    def stop(self):
        self._stop_event.set()

    def _sleep_until(self, t0: float, offset: float) -> bool:
        while time.monotonic() < t0 + offset:
            if self._stop_event.is_set() or self.cancelled():
                return False
            time.sleep(min(0.25, max(0.0, t0 + offset - time.monotonic())))
        return True

    def run(self):
        t0 = time.monotonic()
        self.on_event("network_timeline_started", f"Network timeline started ({len(self.stages)} stages, seed {self.seed})", {"seed": self.seed})
        try:
            for stage in self.stages:
                if not self._sleep_until(t0, stage.start_s):
                    break
                started = now()
                records = self.manager.apply_condition(stage.condition)
                verified = all(r.application_status in (A.APPLIED_VERIFIED, A.EMULATED) for r in records)
                entry = {"label": stage.label, "planned_start_s": stage.start_s, "planned_end_s": stage.end_s, "actual_start": started.isoformat(),
                         "actual_start_offset_s": round(time.monotonic() - t0, 2), "condition": stage.condition.model_dump(),
                         "verified": verified, "applications": [r.model_dump(mode="json") for r in records]}
                self.results.append(entry)
                self.on_event("network_stage_applied", f"Stage '{stage.label}' applied at +{entry['actual_start_offset_s']}s ({'verified' if verified else 'NOT fully verified'})",
                              {k: entry[k] for k in ("label", "planned_start_s", "actual_start", "actual_start_offset_s", "verified")})
                if not self._sleep_until(t0, stage.end_s):
                    entry["actual_end"] = now().isoformat()
                    break
                entry["actual_end"] = now().isoformat()
                entry["actual_end_offset_s"] = round(time.monotonic() - t0, 2)
        except EmulatorError as exc:
            self.error = exc.message
            self.on_event("network_timeline_error", f"Network timeline failed: {exc.message}", {})
        finally:
            restored = self.manager.apply_base()
            self.on_event("network_timeline_finished", "Network timeline finished; base condition restored",
                          {"restored": [r.application_status.value for r in restored]})

    def await_completion(self, timeout_s: float) -> bool:
        self.join(timeout_s)
        return not self.is_alive()


def require_condition_ok(records: list[ConditionRecord]) -> None:
    failed = [r.parameter_key for r in records if r.application_status is A.FAILED]
    if failed:
        raise ConditionApplicationError(f"Network conditions failed verification: {', '.join(failed)}")
