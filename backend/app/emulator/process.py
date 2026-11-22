"""Allowlisted subprocess execution for Android tooling.

Rules enforced here (never bypassed elsewhere):
* argument arrays only, ``shell=False`` always;
* the executable must be one of the explicitly resolved tool paths (adb, emulator, avdmanager, ...);
* arguments are strings without NUL bytes; Windows batch wrappers get a stricter character policy;
* every call has a timeout;
* child processes inherit an environment with PocketLab secrets removed.
"""
from __future__ import annotations

import logging
import os
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Sequence

from app.emulator.exceptions import CommandFailedError, CommandNotAllowedError, CommandTimeoutError

logger = logging.getLogger("pocketlab.emulator.process")

_SECRET_ENV = re.compile(r"(TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|DATABASE_URL|API_KEY|PRIVATE)", re.I)
# cmd.exe re-parses arguments for .bat/.cmd wrappers (avdmanager.bat, apksigner.bat); forbid its metacharacters.
_BATCH_SAFE = re.compile(r"^[A-Za-z0-9 _.,;:+=@\\/\-]*$")
_DEVICE_TOKEN = re.compile(r"^[A-Za-z0-9_@%+=:,./\-]+$")


@dataclass
class CommandResult:
    args: list[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""
    duration_s: float = 0.0
    stdout_bytes: bytes | None = None

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def evidence(self, limit: int = 2000) -> dict:
        """Compact, loggable proof of what ran and what it returned."""
        return {"command": redact_args(self.args), "returncode": self.returncode,
                "stdout": self.stdout[-limit:], "stderr": self.stderr[-limit:], "duration_s": round(self.duration_s, 3)}


def redact_args(args: Sequence[str]) -> list[str]:
    out = []
    for arg in args:
        name = Path(arg).name if ("/" in arg or "\\" in arg) and len(out) == 0 else arg
        out.append(name)
    return out


def device_quote(token: str) -> str:
    """Quote one token for the device-side /system/bin/sh that ``adb shell`` hands its arguments to."""
    if "\x00" in token:
        raise CommandNotAllowedError("NUL byte in device argument")
    return token if _DEVICE_TOKEN.fullmatch(token) else shlex.quote(token)


def scrubbed_environment(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not _SECRET_ENV.search(k)}
    if extra:
        env.update(extra)
    return env


@dataclass
class ProcessRunner:
    """Runs only the executables it was constructed with."""
    allowed: dict[str, Path] = field(default_factory=dict)   # logical name -> resolved path
    base_env: dict[str, str] = field(default_factory=dict)
    default_timeout: float = 60.0

    def allow(self, name: str, path: Path | str | None) -> None:
        if path:
            self.allowed[name] = Path(path).resolve()

    def path(self, name: str) -> Path:
        if name not in self.allowed:
            raise CommandNotAllowedError(f"Tool '{name}' is not available to this worker")
        return self.allowed[name]

    def _validate(self, args: Sequence[str]) -> list[str]:
        if not args or not all(isinstance(a, str) for a in args):
            raise CommandNotAllowedError("Command must be a non-empty list of strings")
        resolved = os.path.normcase(str(Path(args[0]).resolve()))
        if resolved not in {os.path.normcase(str(p)) for p in self.allowed.values()}:
            raise CommandNotAllowedError(f"Executable not in allowlist: {Path(args[0]).name}")
        for a in args:
            if "\x00" in a:
                raise CommandNotAllowedError("NUL byte in argument")
        if sys.platform == "win32" and resolved.endswith((".bat", ".cmd")):
            for a in args[1:]:
                if not _BATCH_SAFE.fullmatch(a):
                    raise CommandNotAllowedError(f"Argument not permitted for a batch wrapper: {a!r}")
        return list(args)

    def run(self, args: Sequence[str], *, timeout: float | None = None, input: bytes | None = None,
            env: dict[str, str] | None = None, binary: bool = False, check: bool = False) -> CommandResult:
        argv = self._validate(args)
        started = time.monotonic()
        try:
            proc = subprocess.run(argv, input=input, capture_output=True, timeout=timeout or self.default_timeout,
                                  env=scrubbed_environment({**self.base_env, **(env or {})}), shell=False, check=False)
        except subprocess.TimeoutExpired as exc:
            raise CommandTimeoutError(f"{Path(argv[0]).name} timed out after {timeout or self.default_timeout}s",
                                      details={"command": redact_args(argv)}) from exc
        except OSError as exc:
            raise CommandFailedError(f"Could not start {Path(argv[0]).name}: {exc}", details={"command": redact_args(argv)}) from exc
        result = CommandResult(args=argv, returncode=proc.returncode,
                               stdout="" if binary else proc.stdout.decode("utf-8", "replace"),
                               stderr=proc.stderr.decode("utf-8", "replace"),
                               duration_s=time.monotonic() - started,
                               stdout_bytes=proc.stdout if binary else None)
        logger.debug("command", extra={"command": redact_args(argv), "returncode": proc.returncode})
        if check and not result.ok:
            raise CommandFailedError(f"{Path(argv[0]).name} exited with {proc.returncode}: {result.stderr.strip()[:500] or result.stdout.strip()[:500]}",
                                     details=result.evidence())
        return result

    def popen(self, args: Sequence[str], *, log_file: IO[bytes] | None = None, env: dict[str, str] | None = None) -> subprocess.Popen:
        """Start a long-running allowlisted process (emulator, logcat, perfetto)."""
        argv = self._validate(args)
        kwargs: dict = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        else:
            kwargs["start_new_session"] = True
        return subprocess.Popen(argv, stdout=log_file or subprocess.DEVNULL, stderr=subprocess.STDOUT if log_file else subprocess.DEVNULL,
                                stdin=subprocess.DEVNULL, env=scrubbed_environment({**self.base_env, **(env or {})}), shell=False, **kwargs)


def terminate(proc: subprocess.Popen, grace_s: float = 20.0) -> int | None:
    """Stop a child process: graceful terminate, then kill. Returns the exit code if it stopped."""
    if proc.poll() is not None:
        return proc.returncode
    try:
        proc.terminate()
        return proc.wait(timeout=grace_s)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            return proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            return None
