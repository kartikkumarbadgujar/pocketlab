"""Android Emulator console client (the authenticated telnet console on the emulator's even port).

Only an allowlisted grammar of console commands can be sent. Responses are returned verbatim so callers
can attach them as evidence and parse read-backs such as ``network status``.
"""
from __future__ import annotations

import os
import re
import socket
import time
from dataclasses import dataclass
from pathlib import Path

from app.emulator.exceptions import CommandFailedError, CommandNotAllowedError

ALLOWED_COMMANDS = [
    re.compile(r"network speed (\d{1,7}:\d{1,7}|gsm|hscsd|gprs|edge|umts|hsdpa|lte|evdo|full)"),
    re.compile(r"network delay (\d{1,5}:\d{1,5}|none|gprs|edge|umts)"),
    re.compile(r"network status"),
    re.compile(r"power (capacity \d{1,3}|status (charging|discharging|not-charging|full|unknown)|ac (on|off)"
               r"|health (good|overheat|dead|overvoltage|failure|unknown)|present (true|false)|display)"),
    re.compile(r"gsm data (on|off|home|unregistered|roaming|searching|denied)"),
    re.compile(r"gsm status"),
    re.compile(r"avd (name|status)"),
    re.compile(r"kill"),
]


def validate_console_command(command: str) -> str:
    command = command.strip()
    if not any(p.fullmatch(command) for p in ALLOWED_COMMANDS):
        raise CommandNotAllowedError(f"Emulator console command not allowed: {command!r}")
    return command


def _token_paths() -> list[Path]:
    paths = []
    for var in ("ANDROID_EMULATOR_HOME", "ANDROID_USER_HOME"):
        if os.environ.get(var):
            paths.append(Path(os.environ[var]).parent / ".emulator_console_auth_token")
    paths.append(Path.home() / ".emulator_console_auth_token")
    return paths


@dataclass
class EmulatorConsole:
    port: int
    host: str = "127.0.0.1"
    timeout_s: float = 10.0
    adb: object | None = None   # AdbManager, used as `adb emu` fallback (handles auth itself)

    def _read_until_status(self, sock: socket.socket) -> str:
        buffer = b""
        deadline = time.monotonic() + self.timeout_s
        while time.monotonic() < deadline:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buffer += chunk
            text = buffer.decode("utf-8", "replace")
            lines = [l.strip() for l in text.replace("\r", "").split("\n") if l.strip()]
            if lines and (lines[-1] == "OK" or lines[-1].startswith("KO")):
                return text
        return buffer.decode("utf-8", "replace")

    def _socket_command(self, command: str) -> str:
        with socket.create_connection((self.host, self.port), timeout=self.timeout_s) as sock:
            sock.settimeout(self.timeout_s)
            banner = self._read_until_status(sock)
            if "Authentication required" in banner:
                token = next((p.read_text().strip() for p in _token_paths() if p.is_file()), None)
                if not token:
                    raise CommandFailedError("Emulator console requires an auth token but ~/.emulator_console_auth_token was not found")
                sock.sendall(f"auth {token}\n".encode())
                if "OK" not in self._read_until_status(sock):
                    raise CommandFailedError("Emulator console authentication failed")
            sock.sendall(f"{command}\n".encode())
            return self._read_until_status(sock)

    def command(self, command: str) -> str:
        command = validate_console_command(command)
        try:
            response = self._socket_command(command)
        except (OSError, CommandFailedError):
            if self.adb is None:
                raise
            result = self.adb.adb("emu", *command.split(), timeout=self.timeout_s + 5)  # type: ignore[attr-defined]
            response = result.stdout + ("\nOK" if result.ok and "KO" not in result.stdout else "")
        lines = [l.strip() for l in response.replace("\r", "").split("\n") if l.strip()]
        if lines and lines[-1].startswith("KO"):
            raise CommandFailedError(f"Emulator console rejected '{command}': {lines[-1]}", details={"response": response[-1000:]})
        return response

    def network_status(self) -> dict:
        """Parse 'network status' into bits/s and ms values for read-back verification."""
        text = self.command("network status")
        out: dict = {"raw": text[-800:]}
        for key, pattern in (("download_bps", r"download speed:\s*(\d+)"), ("upload_bps", r"upload speed:\s*(\d+)"),
                             ("min_latency_ms", r"minimum latency:\s*(\d+)"), ("max_latency_ms", r"maximum latency:\s*(\d+)")):
            m = re.search(pattern, text, re.I)
            if m:
                out[key] = int(m.group(1))
        return out
