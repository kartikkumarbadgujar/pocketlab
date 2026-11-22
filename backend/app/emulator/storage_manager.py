"""Real free-storage constraints on the Android /data partition.

The data partition's *capacity* is set at AVD creation (disk.dataPartition.size on wiped userdata).
The *free space* target is reached by allocating bounded files in a dedicated PocketLab directory on the
same filesystem as app data, re-reading statfs after every chunk, and never going below a safety reserve.
Everything allocated is removed afterwards and the recovery of free space is verified.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from app.emulator.adb_manager import AdbManager
from app.emulator.exceptions import ConditionApplicationError, UnsafeConditionError
from app.emulator.process import device_quote
from app.emulator.schemas import ApplicationStatus as A, ConditionRecord

FILL_DIR = "/data/local/tmp/pocketlab_fill"
MB = 1024 * 1024


@dataclass
class StorageSnapshot:
    total_bytes: int
    available_bytes: int
    method: str
    raw: str

    def as_dict(self) -> dict:
        return {"total_bytes": self.total_bytes, "available_bytes": self.available_bytes, "method": self.method}


def parse_statfs(text: str) -> tuple[int, int] | None:
    parts = text.split()
    if len(parts) >= 3 and all(p.isdigit() for p in parts[:3]):
        block, total, avail = (int(p) for p in parts[:3])
        return block * total, block * avail
    return None


def parse_df(text: str) -> tuple[int, int] | None:
    for line in reversed(text.strip().splitlines()):
        cols = line.split()
        if len(cols) >= 4 and cols[1].isdigit() and cols[3].isdigit():
            return int(cols[1]) * 1024, int(cols[3]) * 1024
    return None


class StorageManager:
    def __init__(self, adb: AdbManager, *, reserve_mb: int, tolerance_mb: int = 24, chunk_mb: int = 256, restore_tolerance_mb: int = 96):
        self.adb = adb
        self.reserve = reserve_mb * MB
        self.tolerance = tolerance_mb * MB
        self.chunk = chunk_mb * MB
        self.restore_tolerance = restore_tolerance_mb * MB
        self.baseline: StorageSnapshot | None = None
        self.allocated_files: list[str] = []

    def snapshot(self) -> StorageSnapshot:
        result = self.adb.shell("stat", "-f", "-c", "%S %b %a", "/data", timeout=20)
        parsed = parse_statfs(result.stdout) if result.ok else None
        if parsed:
            return StorageSnapshot(parsed[0], parsed[1], "statfs(/data)", result.stdout.strip())
        result = self.adb.shell("df", "-k", "/data", timeout=20)
        parsed = parse_df(result.stdout)
        if not parsed:
            raise ConditionApplicationError("Could not read /data capacity with statfs or df", details={"stat": result.stdout[-500:]})
        return StorageSnapshot(parsed[0], parsed[1], "df -k /data", result.stdout.strip())

    def verify_partition(self, requested_mb: int) -> ConditionRecord:
        snap = self.snapshot()
        total_mb = snap.total_bytes / MB
        # ext4/f2fs metadata and reserved blocks make usable capacity smaller than the raw partition size.
        ok = 0.80 * requested_mb <= total_mb <= requested_mb * 1.05
        return ConditionRecord(parameter_key="device.data_partition_mb", requested_value=requested_mb, applied_value=requested_mb,
                               observed_value=round(total_mb, 1), unit="MB",
                               application_status=A.APPLIED_VERIFIED if ok else A.FAILED,
                               mechanism="AVD disk.dataPartition.size; read back with " + snap.method,
                               evidence={"statfs": snap.as_dict(), "tolerance": "observed filesystem capacity within 80-105% of requested partition size"},
                               verified_at=datetime.now(timezone.utc))

    def _allocate(self, path: str, size: int) -> str:
        quoted = device_quote(path)
        result = self.adb.shell_pipeline(f"fallocate -l {int(size)} {quoted}", timeout=120)
        if result.ok:
            return "fallocate"
        count = max(1, size // MB)
        dd = self.adb.shell_pipeline(f"dd if=/dev/zero of={quoted} bs=1048576 count={int(count)} 2>&1", timeout=600)
        if not dd.ok:
            raise ConditionApplicationError("Could not allocate storage test file", details=dd.evidence())
        return "dd"

    def apply_free_target(self, target_bytes: int, requested_label: tuple[str, float, str]) -> ConditionRecord:
        key, requested_value, unit = requested_label
        if target_bytes < self.reserve:
            raise UnsafeConditionError(f"Free-space target {target_bytes // MB} MB is below the {self.reserve // MB} MB safety reserve")
        self.baseline = baseline = self.snapshot()
        if target_bytes >= baseline.total_bytes:
            raise UnsafeConditionError("Free-space target exceeds the partition capacity")
        evidence: dict = {"baseline": baseline.as_dict(), "fill_dir": FILL_DIR, "chunks": []}
        if baseline.available_bytes < target_bytes - self.tolerance:
            return ConditionRecord(parameter_key=key, requested_value=requested_value, unit=unit, observed_value=round(baseline.available_bytes / MB, 1),
                                   application_status=A.FAILED, mechanism="bounded allocation",
                                   evidence={**evidence, "reason": "Device already has less free space than requested; PocketLab cannot create space."})
        self.adb.shell("mkdir", "-p", FILL_DIR, timeout=15, check=True)
        snap = baseline
        for index in range(400):
            remaining = snap.available_bytes - target_bytes
            if remaining <= self.tolerance:
                break
            size = min(self.chunk, remaining)
            path = f"{FILL_DIR}/chunk_{index:03d}.bin"
            method = self._allocate(path, size)
            self.allocated_files.append(path)
            after = self.snapshot()
            evidence["chunks"].append({"file": path, "bytes": size, "method": method, "available_after": after.available_bytes})
            if after.available_bytes < self.reserve:
                self.adb.shell("rm", "-f", path, timeout=30)
                raise UnsafeConditionError("Storage fill crossed the safety reserve; last chunk removed", details=evidence)
            if after.available_bytes >= snap.available_bytes - MB:
                raise ConditionApplicationError("Allocation did not consume space (sparse file?); stopping", details=evidence)
            snap = after
        final = self.snapshot()
        within = abs(final.available_bytes - target_bytes) <= self.tolerance
        evidence["final"] = final.as_dict()
        return ConditionRecord(parameter_key=key, requested_value=requested_value, unit=unit,
                               applied_value=round(target_bytes / MB, 1), observed_value=round(final.available_bytes / MB, 1),
                               application_status=A.APPLIED_VERIFIED if within else A.FAILED,
                               mechanism=f"bounded allocation files in {FILL_DIR}; verified with {final.method}",
                               evidence={**evidence, "tolerance_mb": self.tolerance // MB, "observed_unit": "MB free"},
                               verified_at=datetime.now(timezone.utc))

    def release(self) -> ConditionRecord | None:
        if self.baseline is None:
            return None
        result = self.adb.shell("rm", "-rf", FILL_DIR, timeout=120)
        after = self.snapshot()
        restored = after.available_bytes >= self.baseline.available_bytes - self.restore_tolerance
        leftover = self.adb.shell("ls", FILL_DIR, timeout=10)
        record = ConditionRecord(parameter_key="storage.allocation_cleanup", requested_value="remove all PocketLab allocation files",
                                 observed_value=round(after.available_bytes / MB, 1), unit="MB",
                                 application_status=A.REVERTED if restored and not leftover.ok else A.FAILED,
                                 mechanism=f"rm -rf {FILL_DIR}; verified with {after.method}",
                                 evidence={"baseline_available_mb": round(self.baseline.available_bytes / MB, 1), "after": after.as_dict(),
                                           "rm": result.evidence(), "directory_removed": not leftover.ok},
                                 verified_at=datetime.now(timezone.utc))
        self.allocated_files.clear()
        return record


def human_mb(value: int) -> str:
    return f"{value / MB:.0f} MB"


_ENOSPC = re.compile(r"(ENOSPC|No space left on device|SQLiteFullException|disk is full)", re.I)


def count_storage_errors(log_lines: list[str]) -> int:
    return sum(1 for line in log_lines if _ENOSPC.search(line))
