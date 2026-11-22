"""Preflight planning: decide, before anything starts, how each requested condition will be handled.

Pure function of (EnvironmentRequest, CapabilityReport). The API uses it to show the user an exact
capability summary before they confirm; the worker re-runs it against a fresh report before provisioning.
No capability is ever assumed: a missing report entry is treated as unavailable.
"""
from __future__ import annotations

from app.emulator.schemas import (
    CapabilityReport, CapabilityStatus as C, EnvironmentRequest, PlanItem, PreflightResult, SystemImageInfo,
)

TAG_PREFERENCE = ["google_apis", "default", "google_atd", "aosp_atd", "google_apis_playstore"]
EMULATOR_HOST_RAM_OVERHEAD_MB = 1536     # emulator process + QEMU + GPU emulation beyond guest RAM
EMULATOR_DISK_OVERHEAD_MB = 2048         # system image overlay, snapshots, logs, artifacts
DEFAULT_STORAGE_RESERVE_MB = 200


def select_system_image(req: EnvironmentRequest, report: CapabilityReport) -> SystemImageInfo | None:
    host_arch = str(report.host.get("arch", "")).lower()
    preferred_abis = ["arm64-v8a"] if host_arch in ("arm64", "aarch64") else ["x86_64", "x86"]
    candidates = [i for i in report.system_images if i.api_level == req.device.api_level]
    if req.device.system_image_tag:
        candidates = [i for i in candidates if i.tag == req.device.system_image_tag]
    if req.device.abi:
        candidates = [i for i in candidates if i.abi == req.device.abi]
    else:
        candidates = [i for i in candidates if i.abi in preferred_abis] or []
    if req.memory.pressure_enabled and any(i.rootable for i in candidates):
        candidates = [i for i in candidates if i.rootable]

    def rank(image: SystemImageInfo):
        tag_rank = TAG_PREFERENCE.index(image.tag) if image.tag in TAG_PREFERENCE else len(TAG_PREFERENCE)
        abi_rank = preferred_abis.index(image.abi) if image.abi in preferred_abis else 9
        return (tag_rank, abi_rank)

    return min(candidates, key=rank) if candidates else None


def _available(report: CapabilityReport, key: str) -> bool:
    return report.cap(key) in (C.AVAILABLE, C.SUPPORTED)


def plan_environment(req: EnvironmentRequest, report: CapabilityReport | None) -> PreflightResult:
    result = PreflightResult(ok=False, environment_type=req.environment_type)
    if report is None:
        result.blocking_errors.append(
            "No PocketLab Android worker is online. Start the worker (python -m app.workers.emulator_runner) "
            "on a machine with the Android SDK and hardware acceleration; actual execution cannot be simulated.")
        return result
    result.capability_summary = report.summary()
    result.capability_report_at = report.generated_at
    items = result.items
    emulator = req.environment_type == "emulator"
    image: SystemImageInfo | None = None

    # host / tooling
    # avdmanager is optional: AvdManager has a safe direct-config path scoped to PocketLab's
    # own AVD directory, so SDKs without command-line tools remain executable.
    required_tools = ["android_sdk", "adb"] + (["emulator", "virtualization"] if emulator else [])
    for tool in required_tools:
        if not _available(report, tool):
            entry = report.tools.get(tool) or report.capabilities.get(tool)
            reason = entry.reason if entry and entry.reason else "not detected on the worker"
            result.blocking_errors.append(f"{tool.replace('_', ' ')} unavailable: {reason}")

    if emulator:
        if not _available(report, "avdmanager"):
            result.warnings.append("Android command-line tools are unavailable; the worker will create the disposable AVD from its validated config files.")
        image = select_system_image(req, report)
        result.selected_system_image = image
        if image is None:
            tag = req.device.system_image_tag or "google_apis"
            result.blocking_errors.append(
                f"No installed system image for API {req.device.api_level} ({tag}). Install it on the worker with: "
                f"sdkmanager \"system-images;android-{req.device.api_level};{tag};x86_64\"")
        if req.device.api_level >= 37 and req.device.ram_mb < 4096:
            result.blocking_errors.append(
                f"Android API 37 and newer requires at least 4096 MB guest RAM; {req.device.ram_mb} MB would be silently raised by the emulator. Select 4 GB or more.")
        if req.device.api_level >= 37 and req.device.data_partition_mb < 6144:
            result.blocking_errors.append(
                f"This API 37 phone image provisions a 6 GB data filesystem; requesting {req.device.data_partition_mb} MB would fail read-back verification. Select 6 GB or more.")
        ram_free = report.host.get("ram_available_mb")
        needed = req.device.ram_mb + EMULATOR_HOST_RAM_OVERHEAD_MB
        if ram_free is None:
            result.blocking_errors.append("Worker did not report available host RAM; cannot reserve resources safely.")
        elif ram_free < needed:
            result.blocking_errors.append(f"Worker host has {ram_free} MB RAM available; {needed} MB is needed for a {req.device.ram_mb} MB guest.")
        disk_free = report.host.get("disk_free_mb")
        disk_needed = req.device.data_partition_mb + req.device.sdcard_mb + EMULATOR_DISK_OVERHEAD_MB
        if disk_free is None or disk_free < disk_needed:
            result.blocking_errors.append(f"Worker work directory needs {disk_needed} MB free disk; {disk_free} MB reported.")
        items += [
            PlanItem(parameter_key="device.api_level", requested_value=req.device.api_level, outcome="will_apply" if image else "unsupported",
                     mechanism=f"AVD system image {image.package}" if image else None),
            PlanItem(parameter_key="device.ram_mb", requested_value=req.device.ram_mb, unit="MB", outcome="will_apply",
                     mechanism="AVD hw.ramSize + emulator -memory; verified from guest /proc/meminfo MemTotal"),
            PlanItem(parameter_key="device.cpu_cores", requested_value=req.device.cpu_cores, outcome="will_apply",
                     mechanism="AVD hw.cpu.ncore + emulator -cores; verified from guest /sys/devices/system/cpu/online"),
            PlanItem(parameter_key="device.data_partition_mb", requested_value=req.device.data_partition_mb, unit="MB", outcome="will_apply",
                     mechanism="AVD disk.dataPartition.size on newly created userdata; verified with statfs /data"),
            PlanItem(parameter_key="device.screen", requested_value=req.device.screen.model_dump(), outcome="will_apply",
                     mechanism="AVD hw.lcd.width/height/density; verified with wm size / wm density"),
        ]
        if req.device.vm_heap_mb:
            items.append(PlanItem(parameter_key="device.vm_heap_mb", requested_value=req.device.vm_heap_mb, unit="MB", outcome="will_apply",
                                  mechanism="AVD vm.heapSize; verified from getprop dalvik.vm.heapsize"))
        if req.device.sdcard_mb:
            items.append(PlanItem(parameter_key="device.sdcard_mb", requested_value=req.device.sdcard_mb, unit="MB", outcome="will_apply",
                                  mechanism="avdmanager --sdcard"))
    else:
        if not report.physical_devices:
            result.blocking_errors.append("No authorized physical Android device is attached to the worker.")
        for key, value in (("device.ram_mb", req.device.ram_mb), ("device.cpu_cores", req.device.cpu_cores), ("device.api_level", req.device.api_level)):
            items.append(PlanItem(parameter_key=key, requested_value=value, outcome="unsupported",
                                  reason="A physical device's hardware cannot be changed; the actual value will be observed and reported."))

    # storage
    reserve = int(report.host.get("storage_reserve_mb", DEFAULT_STORAGE_RESERVE_MB))
    st = req.storage
    if st.free_target_mb is not None or st.free_target_pct is not None:
        if st.free_target_mb is not None and st.free_target_mb < reserve:
            result.blocking_errors.append(f"Requested free storage {st.free_target_mb} MB is below the {reserve} MB safety reserve Android needs to keep running.")
        if emulator and st.free_target_mb is not None and st.free_target_mb >= req.device.data_partition_mb:
            result.blocking_errors.append("Requested free storage must be smaller than the data partition size.")
        items.append(PlanItem(parameter_key="storage.free_target_mb" if st.free_target_mb is not None else "storage.free_target_pct",
                              requested_value=st.free_target_mb if st.free_target_mb is not None else st.free_target_pct,
                              unit="MB" if st.free_target_mb is not None else "%",
                              outcome="will_apply" if _available(report, "storage_free_space_control") else "unsupported",
                              mechanism="bounded allocation files in /data/local/tmp/pocketlab_fill, verified with statfs /data; removed after the run"))
    if st.cache_limit_mb is not None:
        items.append(PlanItem(parameter_key="storage.cache_limit_mb", requested_value=st.cache_limit_mb, unit="MB", outcome="unsupported",
                              reason="Android exposes no per-app cache quota control over ADB; cache size is observed instead."))
    if st.app_storage_limit_mb is not None:
        items.append(PlanItem(parameter_key="storage.app_storage_limit_mb", requested_value=st.app_storage_limit_mb, unit="MB", outcome="unsupported",
                              reason="Android has no per-app storage quota control; constrain free space instead."))

    # memory
    if req.memory.pressure_enabled:
        target = req.memory.target_available_mb or 0
        if emulator and target >= req.device.ram_mb:
            result.blocking_errors.append("Memory-pressure target must be below the guest RAM size.")
        if emulator and image is not None and not image.rootable:
            items.append(PlanItem(parameter_key="memory.target_available_mb", requested_value=target, unit="MB", outcome="requires_rootable_image",
                                  reason=f"Bounded in-guest memory pressure needs adb root; '{image.tag}' images are not rootable. Use a google_apis image."))
        elif not emulator:
            items.append(PlanItem(parameter_key="memory.target_available_mb", requested_value=target, unit="MB", outcome="unsupported",
                                  reason="In-guest memory pressure needs root, which production physical devices do not grant."))
        else:
            items.append(PlanItem(parameter_key="memory.target_available_mb", requested_value=target, unit="MB",
                                  outcome="will_apply" if _available(report, "memory_pressure") else "unsupported",
                                  mechanism="size-capped tmpfs filled with incompressible data as root; MemAvailable watchdog; released after the scenario"))

    # network
    net = req.network
    gateway = emulator and _available(report, "network_gateway")
    console_ok = emulator and _available(report, "network_bandwidth")

    def shaping_item(key, value, unit, emulator_mechanism=None):
        if not emulator:
            items.append(PlanItem(parameter_key=key, requested_value=value, unit=unit, outcome="unsupported",
                                  reason="A physical device's traffic does not pass through the worker; shape it with a dedicated test Wi-Fi access point."))
        elif gateway:
            items.append(PlanItem(parameter_key=key, requested_value=value, unit=unit, outcome="will_apply",
                                  mechanism="Linux tc netem on the per-run network namespace gateway"))
        elif emulator_mechanism and console_ok:
            items.append(PlanItem(parameter_key=key, requested_value=value, unit=unit, outcome="will_apply", mechanism=emulator_mechanism))
        else:
            items.append(PlanItem(parameter_key=key, requested_value=value, unit=unit, outcome="requires_linux_gateway",
                                  reason="Needs the isolated Linux netem gateway worker; this worker cannot shape it without affecting the host."))

    if net.download_kbps or net.upload_kbps:
        shaping_item("network.bandwidth_kbps", {"down": net.download_kbps, "up": net.upload_kbps}, "kbps",
                     "emulator -netspeed / console 'network speed up:down'; read back with 'network status'")
    if net.latency_ms:
        shaping_item("network.latency_ms", net.latency_ms, "ms", "emulator -netdelay / console 'network delay'; read back with 'network status'")
    if net.jitter_ms:
        if gateway:
            shaping_item("network.jitter_ms", net.jitter_ms, "ms")
        elif console_ok:
            items.append(PlanItem(parameter_key="network.jitter_ms", requested_value=net.jitter_ms, unit="ms", outcome="emulated",
                                  mechanism="emulator delay range (latency±jitter as min:max)",
                                  reason="The emulator applies a delay range, not a netem distribution; observed RTT spread is measured separately."))
        else:
            shaping_item("network.jitter_ms", net.jitter_ms, "ms")
    for key in net.needs_gateway():
        value = {"network.packet_loss_pct": net.packet_loss_pct, "network.burst_loss": {"enter_pct": net.burst_loss_enter_pct, "exit_pct": net.burst_loss_exit_pct},
                 "network.packet_duplication_pct": net.duplication_pct, "network.packet_reordering_pct": net.reorder_pct, "network.dns_failure": True}[key]
        shaping_item(key, value, "%" if key.endswith("_pct") else None)
    if not net.online:
        items.append(PlanItem(parameter_key="network.online", requested_value=False,
                              outcome="will_apply" if (emulator or gateway) else "unsupported",
                              mechanism="svc wifi/data disable + emulator 'gsm data off'; verified via dumpsys connectivity"))
    if net.timeline:
        missing = sorted({k for stage in net.timeline for k in stage.condition.needs_gateway()}) if not gateway else []
        items.append(PlanItem(parameter_key="network.timeline", requested_value=[s.model_dump() for s in net.timeline], outcome="will_apply",
                              mechanism="timed replay during scenario execution; every transition verified and timestamped",
                              reason=(f"Stage parameters {', '.join(missing)} need the Linux gateway and will be recorded as not applied." if missing else None)))
        if missing:
            result.warnings.append("Some timeline stages request packet-level effects this worker cannot apply: " + ", ".join(missing))

    # battery
    b = req.battery
    for key, value in (("battery.level_pct", b.level_pct), ("battery.charging", b.charging), ("battery.ac_connected", b.ac_connected),
                       ("battery.health", b.health), ("battery.present", b.present)):
        if value is None:
            continue
        if emulator:
            items.append(PlanItem(parameter_key=key, requested_value=value, outcome="emulated" if _available(report, "battery_state_simulation") else "unsupported",
                                  mechanism="emulator console 'power' commands; read back from dumpsys battery",
                                  reason="Simulated battery state only: no energy is consumed and no physical battery exists."))
        else:
            items.append(PlanItem(parameter_key=key, requested_value=value, outcome="emulated", mechanism="dumpsys battery set/unplug (reset after run)",
                                  reason="Overrides the OS-reported battery state; the physical battery is unchanged."))
    if b.saver_enabled is not None:
        items.append(PlanItem(parameter_key="battery.saver_enabled", requested_value=b.saver_enabled, outcome="will_apply",
                              mechanism="cmd power set-mode / settings global low_power; verified from dumpsys power"))

    # thermal
    t = req.thermal
    if t.observe:
        items.append(PlanItem(parameter_key="thermal.observation", requested_value=True,
                              outcome="emulated" if emulator else "will_apply",
                              mechanism="dumpsys thermalservice; battery temperature from dumpsys battery",
                              reason="Emulator thermal HAL is virtual; physical temperatures are not reported for emulators." if emulator else None))
    if t.require_physical_validation:
        if emulator:
            items.append(PlanItem(parameter_key="thermal.throttling_validation", requested_value=True, outcome="requires_physical_device",
                                  reason="Android Emulator does not reproduce smartphone heating or thermal throttling."))
            result.warnings.append("Thermal throttling validation requires a physical Android device; the emulator run will not validate it.")
        else:
            items.append(PlanItem(parameter_key="thermal.throttling_validation", requested_value=True, outcome="will_apply",
                                  mechanism="sustained workload with thermal status, HAL temperatures and CPU frequency sampled over time"))
    if t.sustained_workload_seconds and not emulator:
        items.append(PlanItem(parameter_key="thermal.sustained_workload_seconds", requested_value=t.sustained_workload_seconds, unit="s", outcome="will_apply"))

    # measurement tooling notes
    if emulator and image is not None and image.api_level < 28:
        result.warnings.append("Perfetto tracing needs API 28+; frame metrics will use dumpsys gfxinfo only.")

    result.ok = not result.blocking_errors
    return result
