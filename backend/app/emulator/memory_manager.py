"""Guest RAM verification, bounded memory pressure and application memory measurement.

Memory pressure is produced *inside the guest* by filling a size-capped tmpfs with incompressible data as
root. The tmpfs size option is a hard upper bound, and a MemAvailable watchdog stops (and backs off) before
the configured floor, so the emulator's own system processes keep running. Requires a rootable image.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone

from app.emulator.adb_manager import AdbManager, validate_package
from app.emulator.schemas import ApplicationStatus as A, ConditionRecord

MEM_DIR = "/mnt/pocketlab_mem"
CHUNK_MB = 64


def parse_meminfo(text: str) -> dict[str, int]:
    """/proc/meminfo -> kB values."""
    out = {}
    for line in text.splitlines():
        m = re.match(r"^(\w+):\s+(\d+)\s*kB", line.strip())
        if m:
            out[m.group(1)] = int(m.group(2))
    return out


def parse_app_meminfo(text: str) -> dict[str, float]:
    """dumpsys meminfo <package> -> PSS/RSS/heap in MB. Only fields actually present are returned."""
    out: dict[str, float] = {}
    if m := re.search(r"TOTAL PSS:\s+(\d+)", text):
        out["pss_mb"] = int(m.group(1)) / 1024
    elif m := re.search(r"^\s*TOTAL\s+(\d+)", text, re.M):
        out["pss_mb"] = int(m.group(1)) / 1024
    if m := re.search(r"TOTAL RSS:\s+(\d+)", text):
        out["rss_mb"] = int(m.group(1)) / 1024
    # "Dalvik Heap   pss  private-dirty ... Heap Size  Heap Alloc  Heap Free" -> heap alloc is 2nd from last
    if m := re.search(r"^\s*Dalvik Heap\s+((?:\d+\s+)+\d+)\s*$", text, re.M):
        nums = [int(x) for x in m.group(1).split()]
        if len(nums) >= 3:
            out["heap_alloc_mb"] = nums[-2] / 1024
            out["heap_size_mb"] = nums[-3] / 1024
    return out


class MemoryManager:
    def __init__(self, adb: AdbManager, *, floor_mb: int, rooted: bool, tolerance_mb: int = 96, max_seconds: int = 600):
        self.adb = adb
        self.floor_kb = floor_mb * 1024
        self.rooted = rooted
        self.tolerance_kb = tolerance_mb * 1024
        self.max_seconds = max_seconds
        self.active = False
        self.baseline: dict[str, int] | None = None

    def meminfo(self) -> dict[str, int]:
        return parse_meminfo(self.adb.shell("cat", "/proc/meminfo", timeout=15).stdout)

    def verify_guest_ram(self, requested_mb: int) -> ConditionRecord:
        info = self.meminfo()
        total_mb = info.get("MemTotal", 0) / 1024
        # The kernel reserves memory for itself, firmware and carve-outs, so MemTotal is always below hw.ramSize.
        ok = 0.75 * requested_mb <= total_mb <= requested_mb * 1.02
        return ConditionRecord(parameter_key="device.ram_mb", requested_value=requested_mb, applied_value=requested_mb,
                               observed_value=round(total_mb, 1), unit="MB",
                               application_status=A.APPLIED_VERIFIED if ok else A.FAILED,
                               mechanism="AVD hw.ramSize + emulator -memory; read back from /proc/meminfo MemTotal",
                               evidence={"meminfo_kb": {k: info.get(k) for k in ("MemTotal", "MemAvailable", "SwapTotal")},
                                         "tolerance": "MemTotal within 75-102% of requested guest RAM (kernel reservations)"},
                               verified_at=datetime.now(timezone.utc))

    def apply_pressure(self, target_available_mb: int) -> ConditionRecord:
        key = "memory.target_available_mb"
        if not self.rooted:
            return ConditionRecord(parameter_key=key, requested_value=target_available_mb, unit="MB", application_status=A.UNSUPPORTED,
                                   evidence={"reason": "adb root unavailable on this image; bounded in-guest pressure not possible"})
        self.baseline = base = self.meminfo()
        target_kb = target_available_mb * 1024
        if target_kb < self.floor_kb:
            return ConditionRecord(parameter_key=key, requested_value=target_available_mb, unit="MB", application_status=A.FAILED,
                                   evidence={"reason": f"target below the {self.floor_kb // 1024} MB watchdog floor"})
        available = base.get("MemAvailable", 0)
        evidence: dict = {"baseline_kb": {k: base.get(k) for k in ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree")}, "chunks": []}
        if available <= target_kb + self.tolerance_kb:
            return ConditionRecord(parameter_key=key, requested_value=target_available_mb, observed_value=round(available / 1024, 1), unit="MB",
                                   application_status=A.APPLIED_VERIFIED, mechanism="no allocation needed",
                                   evidence={**evidence, "note": "MemAvailable already at or below target"}, verified_at=datetime.now(timezone.utc))
        cap_mb = max(CHUNK_MB, (available - self.floor_kb) // 1024)
        self.adb.shell_pipeline(f"mkdir -p {MEM_DIR} && mount -t tmpfs -o size={int(cap_mb)}m pocketlab_mem {MEM_DIR}", timeout=20, check=True)
        self.active = True
        deadline = time.monotonic() + self.max_seconds
        watchdog_tripped = False
        index = 0
        while time.monotonic() < deadline and index < 256:
            info = self.meminfo()
            avail = info.get("MemAvailable", 0)
            if avail <= target_kb + self.tolerance_kb // 2:
                break
            chunk = min(CHUNK_MB, max(8, (avail - target_kb) // 1024))
            path = f"{MEM_DIR}/c{index:03d}"
            result = self.adb.shell_pipeline(f"dd if=/dev/urandom of={path} bs=1048576 count={int(chunk)} 2>&1", timeout=180)
            after = self.meminfo().get("MemAvailable", 0)
            evidence["chunks"].append({"mb": chunk, "available_kb_after": after, "ok": result.ok})
            index += 1
            if after < self.floor_kb:
                self.adb.shell("rm", "-f", path, timeout=30)
                watchdog_tripped = True
                break
            if not result.ok:   # tmpfs cap reached: the hard bound did its job
                break
        final = self.meminfo().get("MemAvailable", 0)
        reached = final <= target_kb + self.tolerance_kb
        evidence.update(final_available_kb=final, tmpfs_cap_mb=cap_mb, watchdog_tripped=watchdog_tripped)
        return ConditionRecord(parameter_key=key, requested_value=target_available_mb, applied_value=target_available_mb,
                               observed_value=round(final / 1024, 1), unit="MB",
                               application_status=A.APPLIED_VERIFIED if reached else A.FAILED,
                               mechanism="size-capped tmpfs filled with /dev/urandom data as root; MemAvailable from /proc/meminfo",
                               evidence={**evidence, **({} if reached else {"reason": "Target not reached before tmpfs cap, watchdog or timeout (zram/lmkd may reclaim)."})},
                               verified_at=datetime.now(timezone.utc))

    def release(self) -> ConditionRecord | None:
        if not self.active:
            return None
        self.adb.shell_pipeline(f"rm -f {MEM_DIR}/*; umount {MEM_DIR}; rmdir {MEM_DIR}", timeout=60)
        still_mounted = MEM_DIR in self.adb.shell("cat", "/proc/mounts", timeout=15).stdout
        after = self.meminfo().get("MemAvailable", 0)
        self.active = still_mounted
        return ConditionRecord(parameter_key="memory.pressure_release", requested_value="release memory workload",
                               observed_value=round(after / 1024, 1), unit="MB",
                               application_status=A.FAILED if still_mounted else A.REVERTED,
                               mechanism="rm + umount tmpfs; verified via /proc/mounts and /proc/meminfo",
                               evidence={"baseline_available_kb": (self.baseline or {}).get("MemAvailable"), "available_kb_after": after,
                                         "tmpfs_unmounted": not still_mounted},
                               verified_at=datetime.now(timezone.utc))

    def app_memory(self, package: str) -> tuple[dict[str, float], str]:
        text = self.adb.shell("dumpsys", "meminfo", validate_package(package), timeout=30).stdout
        return parse_app_meminfo(text), text

    def kill_events(self, package: str, since_epoch: float) -> list[str]:
        """am_kill / lowmemorykiller events for the package since a device epoch timestamp."""
        log = self.adb.logcat_dump("events,main,system")
        hits = []
        for line in log.splitlines():
            parts = line.split(None, 1)
            try:
                ts = float(parts[0])
            except (ValueError, IndexError):
                continue
            if ts >= since_epoch and package in line and re.search(r"am_kill|lowmemorykiller|lmkd|Killing \d+:", line):
                hits.append(line.strip()[:300])
        return hits
