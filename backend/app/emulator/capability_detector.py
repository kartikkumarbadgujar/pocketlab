"""Compute the worker's capability report from real checks. Nothing is reported as available unless verified."""
from __future__ import annotations

import logging
import os
import platform
import shutil
import sys
from pathlib import Path

from app.emulator.adb_manager import list_devices
from app.emulator.emulator_config import AndroidSdk, WorkerSettings
from app.emulator.exceptions import EmulatorError
from app.emulator.process import ProcessRunner
from app.emulator.schemas import CapabilityEntry, CapabilityReport, CapabilityStatus as C

logger = logging.getLogger("pocketlab.emulator.capabilities")


def host_resources(work_dir: Path) -> dict:
    import psutil
    vm = psutil.virtual_memory()
    work_dir.mkdir(parents=True, exist_ok=True)
    disk = shutil.disk_usage(work_dir)
    return {
        "os": platform.system().lower(),
        "os_release": platform.release(),
        "arch": platform.machine().lower(),
        "cpu_count": psutil.cpu_count(logical=True) or 1,
        "cpu_load_pct": psutil.cpu_percent(interval=0.5),
        "ram_total_mb": vm.total // (1024 * 1024),
        "ram_available_mb": vm.available // (1024 * 1024),
        "disk_free_mb": disk.free // (1024 * 1024),
    }


def build_runner(settings: WorkerSettings, sdk: AndroidSdk | None) -> ProcessRunner:
    runner = ProcessRunner(default_timeout=settings.adb_timeout_s)
    if sdk:
        runner.base_env = {"ANDROID_SDK_ROOT": str(sdk.root), "ANDROID_HOME": str(sdk.root), "ANDROID_AVD_HOME": str(settings.avd_home)}
        for name in ("adb", "emulator", "avdmanager", "sdkmanager", "aapt2", "apksigner"):
            runner.allow(name, getattr(sdk, name))
    if sys.platform.startswith("linux") and settings.netem_enabled:
        for tool in ("ip", "tc", "sudo"):
            runner.allow(tool, shutil.which(tool))
    # Give every ADB subprocess the same explicit host key. Windows worker processes launched
    # outside an interactive shell otherwise may start adb without loading the user's key and
    # leave a fresh emulator stuck in the "unauthorized" state.
    adb_vendor_keys = os.environ.get("ADB_VENDOR_KEYS")
    if not adb_vendor_keys:
        android_user_home = os.environ.get("ANDROID_USER_HOME")
        key_file = (Path(android_user_home) if android_user_home else Path.home() / ".android") / "adbkey"
        if key_file.is_file():
            adb_vendor_keys = str(key_file)
    if adb_vendor_keys:
        runner.base_env["ADB_VENDOR_KEYS"] = adb_vendor_keys
    return runner


class CapabilityDetector:
    def __init__(self, settings: WorkerSettings):
        self.settings = settings

    def detect(self) -> tuple[CapabilityReport, AndroidSdk | None, ProcessRunner]:
        s = self.settings
        report = CapabilityReport()
        report.host = host_resources(s.work_dir)
        report.host["storage_reserve_mb"] = s.storage_reserve_mb
        tools, caps = report.tools, report.capabilities
        sdk: AndroidSdk | None = None
        try:
            sdk = AndroidSdk.locate(s.sdk_root)
            tools["android_sdk"] = CapabilityEntry(status=C.AVAILABLE)
        except EmulatorError as exc:
            tools["android_sdk"] = CapabilityEntry(status=C.UNAVAILABLE, reason=exc.message)
        runner = build_runner(s, sdk)

        def probe(name: str, args: list[str], timeout: float = 30, ok_text: str | None = None, missing: str = "not installed"):
            if not sdk or not getattr(sdk, name, None):
                tools[name] = CapabilityEntry(status=C.UNAVAILABLE, reason=missing)
                return None
            try:
                result = runner.run([str(runner.path(name)), *args], timeout=timeout)
            except EmulatorError as exc:
                tools[name] = CapabilityEntry(status=C.UNAVAILABLE, reason=exc.message)
                return None
            output = (result.stdout + result.stderr).strip()
            usable = result.ok and (ok_text is None or ok_text.lower() in output.lower())
            tools[name] = CapabilityEntry(status=C.AVAILABLE if usable else C.UNAVAILABLE,
                                          reason=None if usable else (output[-400:] or f"exit code {result.returncode}"),
                                          details={"version": output.splitlines()[0][:200] if output else None})
            return result

        probe("adb", ["version"], missing="platform-tools not installed (sdkmanager \"platform-tools\")")
        probe("emulator", ["-version"], timeout=60, missing="emulator package not installed (sdkmanager \"emulator\")")
        probe("avdmanager", ["list", "device", "-c"], timeout=120, missing="cmdline-tools not installed")
        accel = None
        if sdk and sdk.emulator:
            try:
                accel = runner.run([str(runner.path("emulator")), "-accel-check"], timeout=60)
            except EmulatorError as exc:
                tools["virtualization"] = CapabilityEntry(status=C.UNAVAILABLE, reason=exc.message)
        if accel is not None:
            text = (accel.stdout + accel.stderr).strip()
            usable = accel.ok and ("usable" in text.lower() or "is installed" in text.lower())
            tools["virtualization"] = CapabilityEntry(status=C.AVAILABLE if usable else C.UNAVAILABLE, reason=None if usable else text[-400:],
                                                      details={"accel_check": text[-400:]})
        elif "virtualization" not in tools:
            tools["virtualization"] = CapabilityEntry(status=C.UNAVAILABLE, reason="emulator binary unavailable; acceleration not checked")

        report.system_images = sdk.system_images() if sdk else []
        adb_ok = tools["adb"].status is C.AVAILABLE
        emulator_ok = adb_ok and tools["emulator"].status is C.AVAILABLE and tools["virtualization"].status is C.AVAILABLE
        has_images = bool(report.system_images)

        def cap(key, ok, reason_if_not, status_if_not=C.UNSUPPORTED, **details):
            caps[key] = CapabilityEntry(status=C.SUPPORTED if ok else status_if_not, reason=None if ok else reason_if_not, details=details)

        no_emu = "Android Emulator, ADB or hardware acceleration unavailable"
        cap("avd_creation", emulator_ok and has_images, no_emu if not emulator_ok else "no system images installed",
            method="avdmanager" if tools["avdmanager"].status is C.AVAILABLE else "direct AVD config files")
        for key in ("guest_ram_configuration", "cpu_core_configuration", "display_configuration", "storage_configuration",
                    "network_bandwidth", "network_latency", "network_offline", "battery_state_simulation"):
            cap(key, emulator_ok, no_emu)
        cap("network_jitter", emulator_ok, no_emu, method="emulator delay range")
        cap("storage_free_space_control", adb_ok, "ADB unavailable")
        cap("battery_saver", adb_ok, "ADB unavailable")
        cap("frame_metrics", adb_ok, "ADB unavailable", method="dumpsys gfxinfo")
        cap("ui_automation", adb_ok, "ADB unavailable", method="UIAutomator hierarchy dump + input injection")
        cap("thermal_observation", adb_ok, "ADB unavailable", method="dumpsys thermalservice")
        cap("memory_pressure", emulator_ok and any(i.rootable for i in report.system_images),
            "needs a rootable (google_apis/default) system image", C.REQUIRES_ROOTABLE_IMAGE)
        cap("perfetto", adb_ok and (any(i.api_level >= 28 for i in report.system_images) or bool(s.physical_devices)),
            "needs Android API 28+")
        caps["macrobenchmark"] = CapabilityEntry(status=C.UNSUPPORTED,
                                                 reason="Jetpack Macrobenchmark needs a benchmark module built alongside the app; not applicable to an uploaded APK.")

        # Physical devices: only those explicitly mapped in POCKETLAB_PHYSICAL_DEVICES and authorized in adb.
        if adb_ok and s.physical_devices:
            attached = dict(list_devices(runner))
            for device_id, serial in s.physical_devices.items():
                report.physical_devices.append({"device_id": device_id, "state": attached.get(serial, "not_connected")})
        authorized = [d for d in report.physical_devices if d["state"] == "device"]
        caps["physical_thermal_validation"] = CapabilityEntry(
            status=C.SUPPORTED if authorized else C.REQUIRES_PHYSICAL_DEVICE,
            reason=None if authorized else "Emulators do not reproduce physical heating or throttling; attach an authorized physical device.")

        gateway_ok, gateway_reason = self._check_netem(runner)
        caps["network_gateway"] = CapabilityEntry(status=C.SUPPORTED if gateway_ok else C.REQUIRES_LINUX_GATEWAY, reason=gateway_reason)
        for key in ("packet_loss", "burst_packet_loss", "packet_duplication", "packet_reordering", "dns_failure"):
            caps[key] = CapabilityEntry(status=C.SUPPORTED if gateway_ok else C.REQUIRES_LINUX_GATEWAY, reason=gateway_reason)
        if gateway_ok:
            caps["network_jitter"] = CapabilityEntry(status=C.SUPPORTED, details={"method": "tc netem delay distribution"})
        return report, sdk, runner

    def _check_netem(self, runner: ProcessRunner) -> tuple[bool, str | None]:
        if not sys.platform.startswith("linux"):
            return False, f"Packet-level shaping uses Linux tc netem in an isolated network namespace; this worker runs on {platform.system()}."
        if not self.settings.netem_enabled:
            return False, "Linux netem gateway not enabled (set POCKETLAB_NETEM_ENABLED=1 and install the sudoers allowlist)."
        missing = [t for t in ("ip", "tc", "sudo") if t not in runner.allowed]
        if missing:
            return False, f"Missing tools: {', '.join(missing)}"
        try:
            check = runner.run([str(runner.path("sudo")), "-n", str(runner.path("ip")), "netns", "list"], timeout=10)
        except EmulatorError as exc:
            return False, exc.message
        if not check.ok:
            return False, f"Non-interactive sudo for 'ip netns' is not permitted: {check.stderr.strip()[:200]}"
        return True, None
