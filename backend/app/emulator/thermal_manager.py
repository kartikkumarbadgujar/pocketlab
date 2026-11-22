"""Thermal observation with strict separation of what each value means.

* Emulator: the thermal HAL is virtual. Thermal *status* is reported as EMULATED; physical temperatures
  (CPU, GPU, skin, battery) are UNSUPPORTED because the emulator does not model smartphone heating.
* Physical device: values come from the device's thermal HAL (dumpsys thermalservice) and BatteryService and
  are ACTUAL. CPU temperature is reported only if the HAL exposes a CPU-type sensor; battery temperature is
  never substituted for CPU temperature.
* Thermal headroom (PowerManager.getThermalHeadroom) is not reachable from adb shell: UNAVAILABLE.
"""
from __future__ import annotations

import re

from app.emulator.adb_manager import AdbManager
from app.emulator.battery_manager import parse_dumpsys_battery
from app.emulator.schemas import MeasurementRecord, MeasurementStatus as M

TYPE_NAMES = {0: "cpu", 1: "gpu", 2: "battery", 3: "skin", 4: "usb_port", 5: "power_amplifier"}
_TEMP = re.compile(r"Temperature\{mValue=(-?[\d.]+), mType=(-?\d+), mName=([^,]+), mStatus=(\d)\}")


def parse_thermalservice(text: str) -> dict:
    out: dict = {"temperatures": []}
    if m := re.search(r"Thermal Status:\s*(\d)", text):
        out["status"] = int(m.group(1))
    section = text.split("Current temperatures from HAL:", 1)
    body = section[1].split("Current cooling devices", 1)[0] if len(section) > 1 else text
    seen = set()
    for value, type_, name, status in _TEMP.findall(body):
        if name in seen:
            continue
        seen.add(name)
        out["temperatures"].append({"value_c": float(value), "type": int(type_), "type_name": TYPE_NAMES.get(int(type_), f"type_{type_}"),
                                    "name": name.strip(), "status": int(status)})
    out["hal_ready"] = bool(re.search(r"HAL Ready:\s*true", text))
    return out


def parse_cpufreq(text: str) -> dict[str, int]:
    freqs = {}
    for line in text.splitlines():
        m = re.match(r".*/(cpu\d+)/cpufreq/scaling_cur_freq\s+(\d+)", line.strip())
        if m:
            freqs[m.group(1)] = int(m.group(2))
    return freqs


class ThermalManager:
    def __init__(self, adb: AdbManager, *, emulator: bool):
        self.adb = adb
        self.emulator = emulator

    def snapshot(self) -> dict:
        thermal_raw = self.adb.shell("dumpsys", "thermalservice", timeout=30)
        battery_raw = self.adb.shell("dumpsys", "battery", timeout=20).stdout
        freq_raw = self.adb.shell_pipeline(
            "for f in /sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_cur_freq; do [ -r $f ] && echo $f $(cat $f); done", timeout=20).stdout
        return {"thermal": parse_thermalservice(thermal_raw.stdout) if thermal_raw.ok else None,
                "battery": parse_dumpsys_battery(battery_raw), "cpufreq_khz": parse_cpufreq(freq_raw)}

    def measurements(self, snap: dict, *, run_scenario_id: int | None = None) -> list[MeasurementRecord]:
        records: list[MeasurementRecord] = []
        thermal = snap.get("thermal")
        src = "android_api"

        def add(key, value, unit, status, method, reason=None, evidence=None):
            records.append(MeasurementRecord(parameter_key=key, value=value if status in (M.ACTUAL, M.EMULATED) else None, unit=unit,
                                             source=src, status=status, collection_method=method, reason=reason,
                                             run_scenario_id=run_scenario_id, evidence=evidence or {}))

        if thermal and "status" in thermal:
            add("thermal.status", thermal["status"], "Android enum (0-6)", M.EMULATED if self.emulator else M.ACTUAL, "dumpsys thermalservice",
                reason="virtual thermal HAL" if self.emulator else None)
        else:
            add("thermal.status", None, None, M.UNAVAILABLE, "dumpsys thermalservice", reason="thermal service did not report a status (API < 29?)")
        add("thermal.headroom", None, None, M.UNAVAILABLE, "PowerManager.getThermalHeadroom",
            reason="Not exposed through adb shell; requires an on-device helper app.")
        temps = (thermal or {}).get("temperatures", [])
        for key, type_ in (("cpu.temperature_c", 0), ("gpu.temperature_c", 1), ("device.skin_temperature_c", 3)):
            sensors = [t for t in temps if t["type"] == type_]
            if self.emulator:
                add(key, None, "degC", M.UNSUPPORTED, "dumpsys thermalservice", reason="Emulator does not model physical device temperatures.")
            elif sensors:
                add(key, max(t["value_c"] for t in sensors), "degC", M.ACTUAL, "thermal HAL via dumpsys thermalservice (max of sensors)",
                    evidence={"sensors": sensors})
            else:
                add(key, None, "degC", M.UNAVAILABLE, "dumpsys thermalservice", reason="Device HAL does not expose this sensor type.")
        battery = snap.get("battery") or {}
        if self.emulator:
            add("battery.temperature_c", None, "degC", M.UNSUPPORTED, "dumpsys battery", reason="Virtual battery reports a constant, not a temperature.")
        elif "temperature_tenths_c" in battery:
            add("battery.temperature_c", battery["temperature_tenths_c"] / 10, "degC", M.ACTUAL, "BatteryService via dumpsys battery")
        else:
            add("battery.temperature_c", None, "degC", M.UNAVAILABLE, "dumpsys battery")
        freqs = snap.get("cpufreq_khz") or {}
        if freqs:
            add("cpu.frequency_mhz", round(max(freqs.values()) / 1000, 1), "MHz", M.ACTUAL, "sysfs scaling_cur_freq (max across cores)",
                evidence={"per_core_khz": freqs})
        else:
            add("cpu.frequency_mhz", None, "MHz", M.UNAVAILABLE, "sysfs scaling_cur_freq", reason="cpufreq not exposed to the shell user")
        return records
