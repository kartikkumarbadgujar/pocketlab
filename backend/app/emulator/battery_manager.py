"""Battery state simulation and battery saver.

Emulator: the console 'power' commands drive the goldfish virtual battery. Values read back from
dumpsys battery are EMULATED state; no energy is consumed and nothing about a physical battery is implied.
Physical device: 'dumpsys battery set/unplug' overrides what the OS *reports* (also EMULATED) and is reset
afterwards. Battery saver is a genuine OS power mode in both cases and is verified from dumpsys power.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from app.emulator.adb_manager import AdbManager
from app.emulator.console_manager import EmulatorConsole
from app.emulator.schemas import ApplicationStatus as A, BatterySpec, ConditionRecord

STATUS_CODES = {"unknown": 1, "charging": 2, "discharging": 3, "not-charging": 4, "full": 5}
HEALTH_CODES = {"unknown": 1, "good": 2, "overheat": 3, "dead": 4, "overvoltage": 5, "failure": 6}


def parse_dumpsys_battery(text: str) -> dict:
    out: dict = {}
    for key, pattern, cast in (("ac_powered", r"AC powered:\s*(\w+)", lambda v: v == "true"),
                               ("usb_powered", r"USB powered:\s*(\w+)", lambda v: v == "true"),
                               ("status", r"^\s*status:\s*(\d+)", int), ("health", r"^\s*health:\s*(\d+)", int),
                               ("present", r"^\s*present:\s*(\w+)", lambda v: v == "true"), ("level", r"^\s*level:\s*(\d+)", int),
                               ("temperature_tenths_c", r"^\s*temperature:\s*(-?\d+)", int), ("voltage_mv", r"^\s*voltage:\s*(\d+)", int)):
        m = re.search(pattern, text, re.M)
        if m:
            out[key] = cast(m.group(1))
    return out


def parse_saver(power_dump: str, setting: str) -> bool | None:
    m = re.search(r"(?:mLowPowerModeEnabled|mBatterySaverEnabled|mSettingBatterySaverEnabled)=(true|false)", power_dump)
    if m:
        return m.group(1) == "true"
    setting = setting.strip()
    return setting == "1" if setting in ("0", "1") else None


class BatteryManager:
    def __init__(self, adb: AdbManager, console: EmulatorConsole | None, *, emulator: bool):
        self.adb = adb
        self.console = console
        self.emulator = emulator
        self.overridden = False
        self.saver_changed = False

    def read(self) -> tuple[dict, str]:
        text = self.adb.shell("dumpsys", "battery", timeout=20).stdout
        return parse_dumpsys_battery(text), text

    def apply(self, spec: BatterySpec) -> list[ConditionRecord]:
        commands: list[str] = []
        charging = spec.charging
        ac = spec.ac_connected if spec.ac_connected is not None else charging
        responses: list[str] = []
        if self.emulator:
            if self.console is None:
                return [ConditionRecord(parameter_key="battery.state", application_status=A.UNSUPPORTED, evidence={"reason": "emulator console unavailable"})]
            if spec.present is not None:
                commands.append(f"power present {'true' if spec.present else 'false'}")
            if ac is not None:
                commands.append(f"power ac {'on' if ac else 'off'}")
            if charging is not None:
                commands.append(f"power status {'charging' if charging else 'discharging'}")
            if spec.level_pct is not None:
                commands.append(f"power capacity {spec.level_pct}")
            if spec.health is not None:
                commands.append(f"power health {spec.health}")
            for command in commands:
                responses.append(self.console.command(command).strip()[-200:])
            mechanism = "emulator console power commands"
        else:
            tokens: list[list[str]] = []
            if ac is not None:
                tokens.append(["dumpsys", "battery", "set", "ac", "1" if ac else "0"])
                if not ac:
                    tokens.append(["dumpsys", "battery", "unplug"])
            if charging is not None:
                tokens.append(["dumpsys", "battery", "set", "status", str(STATUS_CODES["charging" if charging else "discharging"])])
            if spec.level_pct is not None:
                tokens.append(["dumpsys", "battery", "set", "level", str(spec.level_pct)])
            if spec.present is not None:
                tokens.append(["dumpsys", "battery", "set", "present", "1" if spec.present else "0"])
            for t in tokens:
                responses.append(self.adb.shell(*t, timeout=20).stderr.strip()[-200:])
                commands.append(" ".join(t))
            self.overridden = bool(tokens)
            mechanism = "dumpsys battery set (OS-reported state override; reset after run)"
        state, raw = self.read()
        evidence = {"commands": commands, "responses": responses, "dumpsys_battery": state,
                    "note": "Simulated state: no energy is consumed and no physical battery is affected."}
        records = []

        def rec(key, requested, observed, ok):
            records.append(ConditionRecord(parameter_key=key, requested_value=requested, applied_value=requested, observed_value=observed,
                                           unit="%" if key == "battery.level_pct" else None,
                                           application_status=A.EMULATED if ok else A.FAILED, mechanism=mechanism,
                                           evidence=evidence, verified_at=datetime.now(timezone.utc)))

        if spec.level_pct is not None:
            rec("battery.level_pct", spec.level_pct, state.get("level"), state.get("level") == spec.level_pct)
        if ac is not None:
            rec("battery.ac_connected", ac, state.get("ac_powered"), state.get("ac_powered") == ac)
        if charging is not None:
            expected = STATUS_CODES["charging"] if charging else STATUS_CODES["discharging"]
            observed = state.get("status")
            rec("battery.charging", charging, observed, observed == expected or (not charging and observed == STATUS_CODES["not-charging"]))
        if spec.health is not None:
            rec("battery.health", spec.health, state.get("health"), state.get("health") == HEALTH_CODES[spec.health] or not self.emulator)
        if spec.present is not None:
            rec("battery.present", spec.present, state.get("present"), state.get("present") == spec.present)
        return records

    def set_saver(self, enabled: bool) -> ConditionRecord:
        mode = "1" if enabled else "0"
        attempt = self.adb.shell("cmd", "power", "set-mode", mode, timeout=20)
        used = "cmd power set-mode"
        if not attempt.ok or "Unknown command" in attempt.stdout + attempt.stderr:
            self.adb.shell("settings", "put", "global", "low_power", mode, timeout=20)
            used = "settings put global low_power"
        self.saver_changed = True
        dump = self.adb.shell("dumpsys", "power", timeout=30).stdout
        setting = self.adb.shell("settings", "get", "global", "low_power", timeout=15).stdout
        observed = parse_saver(dump, setting)
        return ConditionRecord(parameter_key="battery.saver_enabled", requested_value=enabled, applied_value=enabled, observed_value=observed,
                               application_status=A.APPLIED_VERIFIED if observed == enabled else A.FAILED,
                               mechanism=f"{used}; read back from dumpsys power",
                               evidence={"set_mode_result": attempt.evidence(500), "low_power_setting": setting.strip(),
                                         "note": "Battery saver is a real OS power-saving mode in the guest."},
                               verified_at=datetime.now(timezone.utc))

    def revert(self) -> list[ConditionRecord]:
        records = []
        if self.saver_changed:
            rec = self.set_saver(False)
            rec.parameter_key = "battery.saver_reset"
            rec.application_status = A.REVERTED if rec.observed_value is False else A.FAILED
            records.append(rec)
        if self.overridden and not self.emulator:
            result = self.adb.shell("dumpsys", "battery", "reset", timeout=20)
            records.append(ConditionRecord(parameter_key="battery.override_reset", requested_value="dumpsys battery reset",
                                           application_status=A.REVERTED if result.ok else A.FAILED, evidence=result.evidence(300)))
        return records
