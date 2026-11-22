"""ADB access to one device. All commands are argument arrays routed through the allowlisted ProcessRunner.

Device-side commands are built from fixed tokens chosen by PocketLab code (never raw user input) and every
token is quoted for the device shell, so a value can never be interpreted as an extra shell command.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from app.emulator.exceptions import BootTimeoutError, CommandFailedError, CommandTimeoutError, DeviceNotReadyError
from app.emulator.process import CommandResult, ProcessRunner, device_quote

_PACKAGE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$")


def validate_package(package: str) -> str:
    if not _PACKAGE.fullmatch(package or "") or len(package) > 255:
        raise ValueError(f"Invalid Android package name: {package!r}")
    return package


def list_devices(runner: ProcessRunner, prefix: list[str] | None = None) -> list[tuple[str, str]]:
    result = runner.run([*(prefix or []), str(runner.path("adb")), "devices"], timeout=20)
    devices = []
    for line in result.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            devices.append((parts[0], parts[1]))
    return devices


@dataclass
class AdbManager:
    runner: ProcessRunner
    serial: str
    prefix: list[str] = field(default_factory=list)   # e.g. netns wrapper on a Linux gateway worker
    default_timeout: float = 60.0

    # ---------------------------------------------------------------- primitives
    def adb(self, *args: str, timeout: float | None = None, binary: bool = False, check: bool = False) -> CommandResult:
        return self.runner.run([*self.prefix, str(self.runner.path("adb")), "-s", self.serial, *args],
                               timeout=timeout or self.default_timeout, binary=binary, check=check)

    def shell(self, *tokens: str, timeout: float | None = None, check: bool = False) -> CommandResult:
        command = " ".join(device_quote(str(t)) for t in tokens)
        return self.adb("shell", command, timeout=timeout, check=check)

    def shell_pipeline(self, script: str, timeout: float | None = None, check: bool = False) -> CommandResult:
        """Run a fixed PocketLab-authored device script. Callers must only interpolate device_quote()d values."""
        return self.adb("shell", script, timeout=timeout, check=check)

    def getprop(self, name: str) -> str:
        return self.shell("getprop", name, timeout=15).stdout.strip()

    def state(self) -> str:
        try:
            return self.adb("get-state", timeout=10).stdout.strip()
        except CommandTimeoutError:
            return "timeout"

    # ---------------------------------------------------------------- boot / readiness
    def wait_for_boot(self, timeout_s: float, *, alive: Callable[[], bool] | None = None,
                      cancelled: Callable[[], bool] | None = None, on_progress: Callable[[str], None] | None = None) -> dict:
        """Poll until Android reports boot completed and the package manager answers."""
        deadline = time.monotonic() + timeout_s
        milestones: dict[str, float] = {}
        start = time.monotonic()
        while time.monotonic() < deadline:
            if alive and not alive():
                raise DeviceNotReadyError("Emulator process exited during boot")
            if cancelled and cancelled():
                raise DeviceNotReadyError("Cancelled during boot")
            if "adb_device" not in milestones:
                if self.state() == "device":
                    milestones["adb_device"] = time.monotonic() - start
                    on_progress and on_progress("ADB reports the device as online")
            elif "boot_completed" not in milestones:
                if self.getprop("sys.boot_completed") == "1":
                    milestones["boot_completed"] = time.monotonic() - start
                    on_progress and on_progress("sys.boot_completed=1")
            else:
                pm = self.shell("pm", "path", "android", timeout=20)
                if pm.ok and "package:" in pm.stdout:
                    milestones["package_manager_ready"] = time.monotonic() - start
                    return {k: round(v, 1) for k, v in milestones.items()}
            time.sleep(2)
        raise BootTimeoutError(f"Android did not finish booting within {timeout_s}s", details={"milestones": milestones})

    def try_root(self) -> bool:
        """Restart adbd as root (userdebug images only). Returns True only if uid 0 is confirmed."""
        self.adb("root", timeout=30)
        time.sleep(2)
        for _ in range(15):
            if self.state() == "device":
                break
            time.sleep(1)
        return self.shell("id", "-u", timeout=10).stdout.strip() == "0"

    # ---------------------------------------------------------------- packages
    def install(self, apk: Path, *, grant_permissions: bool, timeout: float) -> CommandResult:
        args = ["install", "-r", "-t"] + (["-g"] if grant_permissions else []) + [str(apk)]
        return self.adb(*args, timeout=timeout)

    def package_installed(self, package: str) -> bool:
        result = self.shell("pm", "path", validate_package(package), timeout=20)
        return result.ok and "package:" in result.stdout

    def uninstall(self, package: str) -> CommandResult:
        return self.adb("uninstall", validate_package(package), timeout=60)

    def launcher_component(self, package: str) -> str | None:
        result = self.shell("cmd", "package", "resolve-activity", "--brief", "-a", "android.intent.action.MAIN",
                            "-c", "android.intent.category.LAUNCHER", validate_package(package), timeout=20)
        lines = [l.strip() for l in result.stdout.splitlines() if "/" in l]
        return lines[-1] if lines else None

    def pidof(self, package: str) -> int | None:
        out = self.shell("pidof", validate_package(package), timeout=10).stdout.split()
        return int(out[0]) if out and out[0].isdigit() else None

    def force_stop(self, package: str) -> CommandResult:
        return self.shell("am", "force-stop", validate_package(package), timeout=20)

    # ---------------------------------------------------------------- evidence
    def device_epoch(self) -> float:
        out = self.shell("date", "+%s", timeout=10).stdout.strip()
        return float(out) if out.isdigit() else time.time()

    def screencap(self, destination: Path) -> Path:
        result = self.adb("exec-out", "screencap", "-p", timeout=30, binary=True)
        data = result.stdout_bytes or b""
        if not result.ok or not data.startswith(b"\x89PNG"):
            raise CommandFailedError("screencap did not return a PNG", details=result.evidence())
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        return destination

    def logcat_dump(self, buffers: str = "main,system,crash,events", *, lines: int | None = None) -> str:
        args = ["logcat", "-d", "-v", "epoch", "-b", buffers]
        if lines:
            args += ["-t", str(int(lines))]
        return self.shell(*args, timeout=60).stdout

    def pull(self, remote: str, local: Path, timeout: float = 120) -> CommandResult:
        local.parent.mkdir(parents=True, exist_ok=True)
        return self.adb("pull", remote, str(local), timeout=timeout)

    def input_keyevent(self, key: str) -> CommandResult:
        allowed = {"KEYCODE_HOME", "KEYCODE_BACK", "KEYCODE_APP_SWITCH", "KEYCODE_ENTER", "KEYCODE_WAKEUP", "KEYCODE_MENU"}
        if key not in allowed:
            raise ValueError(f"Key event not allowed: {key}")
        return self.shell("input", "keyevent", key, timeout=15)
