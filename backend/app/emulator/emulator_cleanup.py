"""Stopping emulators, removing disposable AVDs and run directories, and recovering abandoned resources.

Only resources PocketLab created are touched: AVDs matching the pocketlab_run_* pattern inside PocketLab's
own AVD home, and emulator processes whose command line references such an AVD.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
from pathlib import Path

from app.emulator.adb_manager import AdbManager
from app.emulator.avd_manager import AvdManager
from app.emulator.console_manager import EmulatorConsole
from app.emulator.emulator_config import AVD_PREFIX
from app.emulator.exceptions import EmulatorError
from app.emulator.process import terminate

logger = logging.getLogger("pocketlab.emulator.cleanup")


def stop_emulator(proc: subprocess.Popen | None, console: EmulatorConsole | None, adb: AdbManager | None) -> dict:
    evidence: dict = {}
    if console is not None:
        try:
            console.command("kill")
            evidence["console_kill"] = "sent"
        except EmulatorError as exc:
            evidence["console_kill_error"] = exc.message
    elif adb is not None:
        try:
            evidence["adb_emu_kill"] = adb.adb("emu", "kill", timeout=15).returncode
        except EmulatorError as exc:
            evidence["adb_emu_kill_error"] = exc.message
    if proc is not None:
        try:
            proc.wait(timeout=30)
            evidence["exit_code"] = proc.returncode
        except subprocess.TimeoutExpired:
            evidence["exit_code"] = terminate(proc, grace_s=15)
            evidence["forced"] = True
        evidence["stopped"] = proc.poll() is not None
    return evidence


def remove_run_directory(run_dir: Path, runs_root: Path) -> bool:
    run_dir, runs_root = run_dir.resolve(), runs_root.resolve()
    if runs_root not in run_dir.parents:
        raise EmulatorError("Refusing to delete a directory outside the worker runs directory")
    shutil.rmtree(run_dir, ignore_errors=True)
    return not run_dir.exists()


class StateFile:
    """Records live emulator processes so a restarted worker can recover abandoned resources."""

    def __init__(self, path: Path):
        self.path = path

    def load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(self.path)

    def register(self, run_id: int, avd: str, pid: int, port: int) -> None:
        data = self.load()
        data[str(run_id)] = {"avd": avd, "pid": pid, "port": port, "started": time.time()}
        self.save(data)

    def unregister(self, run_id: int) -> None:
        data = self.load()
        data.pop(str(run_id), None)
        self.save(data)


def sweep_orphans(state: StateFile, avd_manager: AvdManager | None, runs_root: Path, *, active_run_ids: set[int] = frozenset()) -> list[dict]:
    """Kill PocketLab emulator processes and delete PocketLab AVDs left behind by a crashed worker."""
    import psutil
    actions = []
    for run_id, entry in state.load().items():
        if int(run_id) in active_run_ids:
            continue
        try:
            proc = psutil.Process(entry["pid"])
            cmdline = " ".join(proc.cmdline())
            if AVD_PREFIX in cmdline and entry["avd"] in cmdline:   # never kill a recycled, unrelated pid
                proc.terminate()
                try:
                    proc.wait(timeout=20)
                except psutil.TimeoutExpired:
                    proc.kill()
                actions.append({"run_id": run_id, "action": "killed_orphan_emulator", "pid": entry["pid"]})
        except (psutil.NoSuchProcess, psutil.AccessDenied, KeyError):
            pass
        state.unregister(int(run_id))
    if avd_manager is not None:
        for name in avd_manager.list_pocketlab_avds():
            run_part = name[len(AVD_PREFIX):].split("_", 1)[0]
            if run_part.isdigit() and int(run_part) in active_run_ids:
                continue
            try:
                avd_manager.delete(name)
                actions.append({"action": "deleted_orphan_avd", "avd": name})
            except EmulatorError as exc:
                actions.append({"action": "orphan_avd_delete_failed", "avd": name, "error": exc.message})
    if runs_root.is_dir():
        for run_dir in runs_root.iterdir():
            run_part = run_dir.name.split("_")[1] if run_dir.name.startswith("run_") else ""
            if run_dir.is_dir() and not (run_part.isdigit() and int(run_part) in active_run_ids):
                remove_run_directory(run_dir, runs_root)
                actions.append({"action": "removed_stale_run_dir", "dir": run_dir.name})
    return actions
