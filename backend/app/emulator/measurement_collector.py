"""Measurement collection. Every record carries parameter, value, unit, source, timestamp, status and method.

Unavailable data is reported as UNAVAILABLE/UNSUPPORTED with a reason, never estimated.
"""
from __future__ import annotations

import re

from app.emulator.adb_manager import AdbManager, validate_package
from app.emulator.memory_manager import MemoryManager, parse_meminfo
from app.emulator.schemas import MeasurementRecord, MeasurementStatus as M
from app.emulator.storage_manager import StorageManager

MB = 1024 * 1024


def parse_gfxinfo(text: str) -> dict:
    out: dict = {}
    for key, pattern in (("total_frames", r"Total frames rendered:\s*(\d+)"), ("janky_frames", r"Janky frames:\s*(\d+)"),
                         ("p50_ms", r"50th percentile:\s*(\d+)ms"), ("p90_ms", r"90th percentile:\s*(\d+)ms"),
                         ("p95_ms", r"95th percentile:\s*(\d+)ms"), ("p99_ms", r"99th percentile:\s*(\d+)ms")):
        if m := re.search(pattern, text):
            out[key] = int(m.group(1))
    if m := re.search(r"HISTOGRAM:\s*(.+)", text):
        buckets = [(int(a), int(b)) for a, b in re.findall(r"(\d+)ms=(\d+)", m.group(1))]
        out["slow_frames"] = sum(c for ms, c in buckets if ms > 16)
        out["frozen_frames"] = sum(c for ms, c in buckets if ms > 700)
    return out


def parse_am_start(text: str) -> dict:
    out: dict = {}
    for key, pattern, cast in (("status", r"Status:\s*(\w+)", str.lower), ("launch_state", r"LaunchState:\s*(\w+)", str.upper),
                               ("total_time_ms", r"TotalTime:\s*(\d+)", int), ("wait_time_ms", r"WaitTime:\s*(\d+)", int)):
        if m := re.search(pattern, text):
            out[key] = cast(m.group(1))
    if "Error" in text and "status" not in out:
        out["status"] = "error"
    return out


def parse_displayed(lines: list[str], package: str, marker: str = "Displayed") -> int | None:
    """'Displayed pkg/.Main: +1s234ms' -> 1234. marker='Fully drawn' for TTFD."""
    for line in lines:
        if marker in line and package in line:
            if m := re.search(r"\+(?:(\d+)s)?(\d+)ms", line):
                return int(m.group(1) or 0) * 1000 + int(m.group(2))
    return None


def parse_proc_stat_pair(text: str) -> float | None:
    """Two samples of '/proc/stat cpu line' + '/proc/<pid>/stat' -> app CPU % of total device CPU capacity."""
    totals, procs = [], []
    for line in text.splitlines():
        if line.startswith("cpu "):
            totals.append(sum(int(x) for x in line.split()[1:] if x.isdigit()))
        elif ")" in line:
            fields = line.rsplit(")", 1)[1].split()
            if len(fields) > 12 and fields[11].isdigit():
                procs.append(int(fields[11]) + int(fields[12]))
    if len(totals) >= 2 and len(procs) >= 2 and totals[1] > totals[0]:
        return round(100.0 * (procs[1] - procs[0]) / (totals[1] - totals[0]), 2)
    return None


class MeasurementCollector:
    def __init__(self, adb: AdbManager, package: str, *, emulator: bool, memory: MemoryManager, storage: StorageManager):
        self.adb, self.package, self.emulator, self.memory, self.storage = adb, validate_package(package), emulator, memory, storage
        self.peak_pss_mb: float | None = None

    def _rec(self, key, value, unit, source, method, *, status=M.ACTUAL, scenario=None, reason=None, evidence=None) -> MeasurementRecord:
        return MeasurementRecord(parameter_key=key, value=value if status in (M.ACTUAL, M.EMULATED) else None, unit=unit, source=source,
                                 status=status, collection_method=method, run_scenario_id=scenario, reason=reason, evidence=evidence or {})

    def device_info(self) -> tuple[dict, list[MeasurementRecord]]:
        props = {k: self.adb.getprop(p) for k, p in (("model", "ro.product.model"), ("manufacturer", "ro.product.manufacturer"),
                                                    ("api_level", "ro.build.version.sdk"), ("release", "ro.build.version.release"),
                                                    ("abi", "ro.product.cpu.abi"), ("heap_size", "dalvik.vm.heapsize"),
                                                    ("is_emulator", "ro.kernel.qemu"))}
        mem = parse_meminfo(self.adb.shell("cat", "/proc/meminfo", timeout=15).stdout)
        cores_text = self.adb.shell("cat", "/sys/devices/system/cpu/online", timeout=10).stdout.strip()
        props["cpu_online"] = cores_text
        props["cpu_cores"] = _count_cpu_range(cores_text)
        size = self.adb.shell("wm", "size", timeout=10).stdout
        density = self.adb.shell("wm", "density", timeout=10).stdout
        if m := re.findall(r"(\d+)x(\d+)", size):
            props["display_width"], props["display_height"] = int(m[-1][0]), int(m[-1][1])
        if m := re.findall(r"density:\s*(\d+)", density):
            props["display_density"] = int(m[-1])
        snap = self.storage.snapshot()
        props.update(mem_total_mb=round(mem.get("MemTotal", 0) / 1024, 1), mem_available_mb=round(mem.get("MemAvailable", 0) / 1024, 1),
                     storage_total_bytes=snap.total_bytes, storage_available_bytes=snap.available_bytes)
        records = [
            self._rec("device.model", props["model"] or None, None, "android_api", "getprop ro.product.model",
                      status=M.ACTUAL if props["model"] else M.UNAVAILABLE),
            self._rec("device.manufacturer", props["manufacturer"] or None, None, "android_api", "getprop ro.product.manufacturer",
                      status=M.ACTUAL if props["manufacturer"] else M.UNAVAILABLE),
            self._rec("android.api_level", float(props["api_level"]) if props["api_level"].isdigit() else None, None, "android_api",
                      "getprop ro.build.version.sdk", status=M.ACTUAL if props["api_level"].isdigit() else M.UNAVAILABLE),
            self._rec("ram.total_mb", props["mem_total_mb"], "MB", "android_api", "/proc/meminfo MemTotal"),
            self._rec("memory.available_mb", props["mem_available_mb"], "MB", "android_api", "/proc/meminfo MemAvailable"),
            self._rec("storage.total_bytes", snap.total_bytes, "bytes", "filesystem", snap.method),
            self._rec("storage.available_bytes", snap.available_bytes, "bytes", "filesystem", snap.method),
            self._rec("storage.free_pct", round(100 * snap.available_bytes / snap.total_bytes, 2) if snap.total_bytes else None, "%",
                      "filesystem", snap.method, status=M.ACTUAL if snap.total_bytes else M.UNAVAILABLE),
            self._rec("cpu.core_count", props["cpu_cores"], "count", "android_api", "/sys/devices/system/cpu/online",
                      status=M.ACTUAL if props["cpu_cores"] else M.UNAVAILABLE),
        ]
        for key in ("display_width", "display_height"):
            if key in props:
                records.append(self._rec(f"display.{key.split('_')[1]}_px", props[key], "px", "android_api", "wm size"))
        return props, records

    def app_metrics(self, *, scenario: int | None = None) -> list[MeasurementRecord]:
        records: list[MeasurementRecord] = []
        pid = self.adb.pidof(self.package)
        if pid is None:
            reason = "Application process not running at sample time"
            return [self._rec(k, None, "MB", "android_api", "dumpsys meminfo", status=M.UNAVAILABLE, scenario=scenario, reason=reason)
                    for k in ("app.pss_mb", "app.rss_mb", "app.heap_used_mb")]
        values, raw = self.memory.app_memory(self.package)
        for key, field_name in (("app.pss_mb", "pss_mb"), ("app.rss_mb", "rss_mb"), ("app.heap_used_mb", "heap_alloc_mb")):
            if field_name in values:
                records.append(self._rec(key, round(values[field_name], 2), "MB", "android_api", f"dumpsys meminfo {self.package}", scenario=scenario))
            else:
                records.append(self._rec(key, None, "MB", "android_api", f"dumpsys meminfo {self.package}", status=M.UNAVAILABLE, scenario=scenario,
                                         reason="Field not reported by this Android version's dumpsys meminfo"))
        if "pss_mb" in values:
            self.peak_pss_mb = max(self.peak_pss_mb or 0.0, values["pss_mb"])
        cpu = self.cpu_utilization(pid)
        records.append(self._rec("performance.cpu_utilization_pct", cpu, "%", "android_api",
                                 "/proc/<pid>/stat utime+stime delta over 2s / total /proc/stat delta (share of all cores)",
                                 status=M.ACTUAL if cpu is not None else M.UNAVAILABLE, scenario=scenario,
                                 reason=None if cpu is not None else "procfs sampling failed"))
        return records

    def cpu_utilization(self, pid: int) -> float | None:
        text = self.adb.shell_pipeline(f"head -1 /proc/stat; cat /proc/{int(pid)}/stat; sleep 2; head -1 /proc/stat; cat /proc/{int(pid)}/stat", timeout=20).stdout
        return parse_proc_stat_pair(text)

    def frame_metrics_reset(self) -> None:
        self.adb.shell("dumpsys", "gfxinfo", self.package, "reset", timeout=20)

    def frame_metrics(self, *, scenario: int | None = None) -> tuple[dict, list[MeasurementRecord]]:
        text = self.adb.shell("dumpsys", "gfxinfo", self.package, timeout=30).stdout
        stats = parse_gfxinfo(text)
        method = "dumpsys gfxinfo (since reset)"
        if not stats.get("total_frames"):
            reason = "No frames rendered by the app's hardware renderer in this window"
            return stats, [self._rec(k, None, u, "android_api", method, status=M.UNAVAILABLE, scenario=scenario, reason=reason)
                           for k, u in (("performance.frame_p50_ms", "ms"), ("performance.frame_p95_ms", "ms"), ("performance.slow_frames", "count"))]
        records = [self._rec(f"performance.frame_{p}_ms", stats[f"{p}_ms"], "ms", "android_api", method, scenario=scenario,
                             evidence={"total_frames": stats["total_frames"]})
                   for p in ("p50", "p90", "p95", "p99") if f"{p}_ms" in stats]
        for key in ("slow_frames", "frozen_frames"):
            if key in stats:
                records.append(self._rec(f"performance.{key}", stats[key], "count", "android_api", method + " histogram (>16ms / >700ms)", scenario=scenario))
        return stats, records

    def battery_metrics(self, battery_state: dict, *, scenario: int | None = None) -> list[MeasurementRecord]:
        status = M.EMULATED if self.emulator else M.ACTUAL
        method = "dumpsys battery" + (" (emulator virtual battery)" if self.emulator else "")
        records = []
        if "level" in battery_state:
            records.append(self._rec("battery.level_pct", battery_state["level"], "%", "android_api", method, status=status, scenario=scenario))
        if "status" in battery_state:
            records.append(self._rec("battery.charging", battery_state["status"] == 2, None, "android_api", method, status=status, scenario=scenario))
        return records

    def final_app_metrics(self, *, crashes: int, anrs: int, restarts: int, kills: int) -> list[MeasurementRecord]:
        method = "logcat crash/events buffers + process watch"
        records = [self._rec("app.crashes", crashes, "count", "android_api", method),
                   self._rec("app.anrs", anrs, "count", "android_api", method),
                   self._rec("app.process_restarts", restarts, "count", "runner", "pidof sampling every 2s"),
                   self._rec("process.kill_count", kills, "count", "android_api", "am_kill/lmkd events + PocketLab-initiated kills")]
        if self.peak_pss_mb is not None:
            records.append(self._rec("app.peak_memory_mb", round(self.peak_pss_mb, 2), "MB", "android_api", "max PSS across samples"))
        else:
            records.append(self._rec("app.peak_memory_mb", None, "MB", "android_api", "max PSS across samples", status=M.UNAVAILABLE,
                                     reason="no PSS samples collected"))
        return records


def _count_cpu_range(text: str) -> int | None:
    total = 0
    for part in text.split(","):
        if "-" in part:
            a, b = part.split("-", 1)
            if a.isdigit() and b.isdigit():
                total += int(b) - int(a) + 1
        elif part.strip().isdigit():
            total += 1
    return total or None
