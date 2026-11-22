"""Continuous evidence capture while a run is active.

* LogcatCapture streams main/system/crash/events buffers (epoch timestamps) to a per-run file.
* ProcessWatch samples the app pid to detect process deaths and restarts with timestamps.
* detect_failures() finds crashes (Java + native) and ANRs for one package within a device-time window.
"""
from __future__ import annotations

import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from app.emulator.adb_manager import AdbManager
from app.emulator.exceptions import EmulatorError
from app.emulator.process import terminate

_EPOCH = re.compile(r"^\s*(\d{9,11}\.\d+)\s")


class LogcatCapture:
    def __init__(self, adb: AdbManager, destination: Path):
        self.adb = adb
        self.path = destination
        self.proc: subprocess.Popen | None = None
        self._handle = None

    def start(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("ab")
        argv = [*self.adb.prefix, str(self.adb.runner.path("adb")), "-s", self.adb.serial, "logcat", "-v", "epoch", "-b", "main,system,crash,events"]
        self.proc = self.adb.runner.popen(argv, log_file=self._handle)

    def stop(self) -> Path:
        if self.proc:
            terminate(self.proc, grace_s=5)
        if self._handle:
            self._handle.close()
        return self.path

    def lines_since(self, since_epoch: float, until_epoch: float | None = None) -> list[str]:
        if not self.path.exists():
            return []
        out = []
        with self.path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                m = _EPOCH.match(line)
                if not m:
                    if out:
                        out.append(line.rstrip("\n"))   # continuation of a multi-line entry
                    continue
                ts = float(m.group(1))
                if ts >= since_epoch and (until_epoch is None or ts <= until_epoch):
                    out.append(line.rstrip("\n"))
        return out


def detect_failures(lines: list[str], package: str) -> dict[str, list[dict]]:
    crashes, anrs, native = [], [], []
    for i, line in enumerate(lines):
        if "FATAL EXCEPTION" in line:
            window = lines[i:i + 6]
            if any(f"Process: {package}" in l for l in window):
                crashes.append({"line": line.strip()[:300], "stack": [l.strip()[:300] for l in lines[i:i + 25]]})
        elif ("am_anr" in line and package in line) or f"ANR in {package}" in line:
            anrs.append({"line": line.strip()[:300], "detail": [l.strip()[:300] for l in lines[i:i + 10]]})
        elif f">>> {package} <<<" in line:
            native.append({"line": line.strip()[:300], "detail": [l.strip()[:300] for l in lines[max(0, i - 3):i + 15]]})
    return {"crashes": crashes, "anrs": anrs, "native_crashes": native}


@dataclass
class ProcessWatch:
    adb: AdbManager
    package: str
    interval_s: float = 2.0
    events: list[dict] = field(default_factory=list)
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None
    last_pid: int | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="pocketlab-process-watch", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                pid = self.adb.pidof(self.package)
            except EmulatorError:
                pid = None
            if pid != self.last_pid:
                self.events.append({"at": time.time(), "from_pid": self.last_pid, "to_pid": pid,
                                    "kind": "started" if self.last_pid is None else ("died" if pid is None else "restarted")})
                self.last_pid = pid
            self._stop.wait(self.interval_s)

    def stop(self) -> list[dict]:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10)
        return self.events

    def restart_count(self) -> int:
        return sum(1 for e in self.events if e["kind"] in ("started", "restarted")) - 1 if self.events else 0

    def death_count(self) -> int:
        return sum(1 for e in self.events if e["kind"] in ("died", "restarted"))


class Heartbeat(threading.Thread):
    """Calls ``beat`` periodically; a True return value means cancellation was requested."""

    def __init__(self, beat: Callable[[], bool], interval_s: float, on_cancel: Callable[[], None]):
        super().__init__(name="pocketlab-heartbeat", daemon=True)
        self.beat, self.interval_s, self.on_cancel = beat, interval_s, on_cancel
        self._stop = threading.Event()
        self.failures = 0

    def run(self) -> None:
        while not self._stop.wait(self.interval_s):
            try:
                if self.beat():
                    self.on_cancel()
                self.failures = 0
            except Exception:   # network blips must not kill the run; the lease tolerates missed beats
                self.failures += 1

    def stop(self) -> None:
        self._stop.set()
