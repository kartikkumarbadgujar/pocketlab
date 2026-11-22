"""MOCKED tests of the Android orchestration logic.

Every test here replaces adb/emulator with scripted outputs (FakeAdb / fake SDK trees). They verify parsing,
safety rules, command construction, expectation logic and lifecycle/cleanup ordering. They are NOT evidence
that real emulator execution works - see tests/integration/test_real_emulator.py for that.
"""
import json
import struct
import zipfile

import pytest

from app.emulator import planner
from app.emulator.adb_manager import AdbManager
from app.emulator.apk_manifest import manifest_from_apk, parse_aapt2_badging
from app.emulator.avd_manager import AvdManager
from app.emulator.battery_manager import parse_dumpsys_battery, parse_saver
from app.emulator.console_manager import validate_console_command
from app.emulator.emulator_config import AndroidSdk, avd_name_for_run, build_avd_spec, emulator_netdelay, emulator_netspeed
from app.emulator.emulator_monitor import detect_failures
from app.emulator.exceptions import CommandNotAllowedError, EmulatorError, SdkNotFoundError, UnsafeConditionError
from app.emulator.measurement_collector import parse_am_start, parse_displayed, parse_gfxinfo, parse_proc_stat_pair
from app.emulator.memory_manager import parse_app_meminfo, parse_meminfo
from app.emulator.network_manager import NetemGateway, parse_ping
from app.emulator.process import CommandResult, ProcessRunner, device_quote
from app.emulator.schemas import (
    ALLOWED_TRANSITIONS, ApplicationStatus, CapabilityReport, EnvironmentRequest, EnvironmentStatus as S, MeasurementRecord, MeasurementStatus,
    NetworkCondition, SystemImageInfo, request_from_profile, transition_allowed,
)
from app.emulator.storage_manager import StorageManager, parse_df, parse_statfs
from app.emulator.thermal_manager import ThermalManager, parse_thermalservice

pytestmark = pytest.mark.mocked
IMAGE = SystemImageInfo(package="system-images;android-30;google_apis;x86_64", api_level=30, tag="google_apis", abi="x86_64", rootable=True)


class FakeAdb(AdbManager):
    """AdbManager whose transport is a scripted handler(args) -> stdout | (stdout, returncode)."""

    def __init__(self, handler):
        super().__init__(runner=ProcessRunner(), serial="emulator-5554")
        self.handler, self.calls = handler, []

    def adb(self, *args, timeout=None, binary=False, check=False):
        self.calls.append(args)
        out = self.handler(args)
        stdout, code = out if isinstance(out, tuple) else (out, 0)
        return CommandResult(args=list(args), returncode=code, stdout=stdout if not binary else "",
                             stdout_bytes=stdout.encode() if binary and isinstance(stdout, str) else None)


# safety
def test_process_runner_rejects_unlisted_executables_and_nul(tmp_path):
    runner = ProcessRunner()
    with pytest.raises(CommandNotAllowedError):
        runner.run(["cmd.exe", "/c", "dir"])
    exe = tmp_path / "adb.exe"
    exe.write_bytes(b"")
    runner.allow("adb", exe)
    with pytest.raises(CommandNotAllowedError):
        runner.run([str(exe), "shell", "a\x00b"])


def test_device_quote_neutralises_shell_metacharacters():
    assert device_quote("com.example.app") == "com.example.app"
    assert device_quote("x; rm -rf /") == "'x; rm -rf /'"
    assert device_quote("$(reboot)") == "'$(reboot)'"


def test_console_grammar_is_allowlisted():
    assert validate_console_command("network speed 500:1500") == "network speed 500:1500"
    assert validate_console_command("power capacity 20")
    for bad in ("network speed 1; kill", "redir add tcp:1:2", "power capacity 20\nkill", "sms send 123 hi"):
        with pytest.raises(CommandNotAllowedError):
            validate_console_command(bad)


def test_avd_names_and_deletion_are_confined(tmp_path):
    name = avd_name_for_run(42, 1, "AbC123zz!")
    assert name.startswith("pocketlab_run_42_a1_")
    sdk_root = tmp_path / "sdk"
    (sdk_root / "system-images/android-30/google_apis/x86_64").mkdir(parents=True)
    manager = AvdManager(ProcessRunner(), tmp_path / "avd", sdk_root)
    spec = build_avd_spec(EnvironmentRequest(), IMAGE, name, gpu_mode="swiftshader_indirect", headless=True, port=5556)
    created = manager.create(spec)
    assert created["method"] == "direct_config" and created["config"]["hw.ramSize"] == "2048"
    assert manager.list_pocketlab_avds() == [name]
    with pytest.raises(EmulatorError):
        manager.delete("Pixel_7_API_34")     # a user's Android Studio AVD is never touched
    assert manager.delete(name)["removed"] is True and manager.list_pocketlab_avds() == []


# config / planning
def test_sdk_discovery_requires_explicit_location(tmp_path, monkeypatch):
    monkeypatch.delenv("ANDROID_SDK_ROOT", raising=False)
    monkeypatch.delenv("ANDROID_HOME", raising=False)
    with pytest.raises(SdkNotFoundError):
        AndroidSdk.locate(None)
    image_dir = tmp_path / "system-images/android-30/google_apis/x86_64"
    image_dir.mkdir(parents=True)
    (image_dir / "source.properties").write_text("AndroidVersion.ApiLevel=30\nSystemImage.TagId=google_apis\nSystemImage.Abi=x86_64\n")
    play = tmp_path / "system-images/android-34/google_apis_playstore/x86_64"
    play.mkdir(parents=True)
    (play / "source.properties").write_text("AndroidVersion.ApiLevel=34\nSystemImage.TagId=google_apis_playstore\nSystemImage.Abi=x86_64\n")
    sdk = AndroidSdk.locate(tmp_path)
    assert [(i.api_level, i.tag, i.rootable) for i in sdk.system_images()] == [(30, "google_apis", True), (34, "google_apis_playstore", False)]
    assert sdk.adb is None   # nothing pretends tools exist


def test_avd_spec_carries_requested_hardware_and_network():
    req = EnvironmentRequest.model_validate({"device": {"ram_mb": 2048, "cpu_cores": 2, "vm_heap_mb": 192, "data_partition_mb": 2048},
                                             "network": {"download_kbps": 1500, "upload_kbps": 500, "latency_ms": 180, "jitter_ms": 40}})
    spec = build_avd_spec(req, IMAGE, "pocketlab_run_1_a1_x", gpu_mode="swiftshader_indirect", headless=True, port=5560)
    assert spec.config["hw.ramSize"] == "2048" and spec.config["disk.dataPartition.size"] == "2048M" and spec.config["vm.heapSize"] == "192"
    args = spec.launch_args
    assert args[args.index("-memory") + 1] == "2048" and args[args.index("-netspeed") + 1] == "500:1500" and args[args.index("-netdelay") + 1] == "140:220"
    assert "-wipe-data" in args and "-no-snapshot" in args and "-no-window" in args
    assert emulator_netspeed(NetworkCondition()) is None and emulator_netdelay(NetworkCondition(latency_ms=50)) == "50:50"


def test_profile_prefill_and_state_machine():
    req = request_from_profile({"device.target_ram_gb": 2, "storage.free_target_mb": 400, "network.latency_ms": 180, "network.packet_loss_pct": 1,
                                "battery.level_pct": 20, "battery.saver_enabled": True, "network.online_seconds": 30, "network.offline_seconds": 10})
    assert req.device.ram_mb == 2048 and req.storage.free_target_mb == 400 and req.battery.saver_enabled and len(req.network.timeline) == 6
    assert transition_allowed("QUEUED", "PREFLIGHT") and transition_allowed("READY", "INSTALLING") and transition_allowed("EXECUTING", "CANCEL_REQUESTED")
    assert not transition_allowed("EXECUTING", "BOOTING") and not transition_allowed("CLEANED", "QUEUED") and not transition_allowed("COMPLETED", "FAILED")
    assert all(S.CLEANED not in targets or state in (S.CLEANING, S.CANCEL_REQUESTED) for state, targets in ALLOWED_TRANSITIONS.items())


def test_planner_physical_device_cannot_be_reconfigured_or_shaped():
    report = CapabilityReport.model_validate({"host": {"arch": "amd64"}, "tools": {"android_sdk": {"status": "available"}, "adb": {"status": "available"}},
                                              "physical_devices": [{"device_id": 3, "state": "device"}]})
    plan = planner.plan_environment(EnvironmentRequest.model_validate({"environment_type": "physical_device", "network": {"latency_ms": 100},
                                                                       "thermal": {"require_physical_validation": True}}), report)
    outcomes = {i.parameter_key: i.outcome for i in plan.items}
    assert plan.ok and outcomes["device.ram_mb"] == "unsupported" and outcomes["network.latency_ms"] == "unsupported"
    assert outcomes["thermal.throttling_validation"] == "will_apply"


def test_measurement_records_refuse_fabrication():
    with pytest.raises(ValueError):
        MeasurementRecord(parameter_key="cpu.temperature_c", value=40.0, source="android_api", status=MeasurementStatus.UNSUPPORTED, collection_method="x")
    with pytest.raises(ValueError):
        MeasurementRecord(parameter_key="network.packet_loss_pct", value=3, source="runner", status=MeasurementStatus.CONFIGURED, collection_method="x")
    with pytest.raises(ValueError):
        MeasurementRecord(parameter_key="app.pss_mb", source="android_api", status=MeasurementStatus.ACTUAL, collection_method="x")


# APK manifest
def build_axml(package: str, version_code: int, version_name: str, min_sdk: int) -> bytes:
    strings = ["versionCode", "versionName", "minSdkVersion", "package", "manifest", "uses-sdk", version_name, package]
    body = b""
    offsets = []
    for s in strings:
        offsets.append(len(body))
        body += struct.pack("<H", len(s)) + s.encode("utf-16-le") + b"\x00\x00"
    body += b"\x00" * (-len(body) % 4)
    header = 28
    pool = struct.pack("<HHIIIIII", 0x0001, header, header + 4 * len(strings) + len(body), len(strings), 0, 0, header + 4 * len(strings), 0)
    pool += struct.pack(f"<{len(strings)}I", *offsets) + body
    resmap = struct.pack("<HHI3I", 0x0180, 8, 20, 0x0101021B, 0x0101021C, 0x0101020C)

    def element(name_idx, attrs):
        payload = struct.pack("<IIHHHHHH", 0xFFFFFFFF, name_idx, 20, 20, len(attrs), 0, 0, 0)
        for a_name, raw, dtype, data in attrs:
            payload += struct.pack("<IIIHBBI", 0xFFFFFFFF, a_name, raw, 8, 0, dtype, data)
        return struct.pack("<HHIII", 0x0102, 16, 16 + len(payload), 1, 0xFFFFFFFF) + payload

    manifest = element(4, [(0, 0xFFFFFFFF, 0x10, version_code), (1, 6, 0x03, 6), (3, 7, 0x03, 7)])
    uses_sdk = element(5, [(2, 0xFFFFFFFF, 0x10, min_sdk)])
    content = pool + resmap + manifest + uses_sdk
    return struct.pack("<HHI", 0x0003, 8, 8 + len(content)) + content


def test_binary_manifest_parser(tmp_path):
    apk = tmp_path / "app.apk"
    with zipfile.ZipFile(apk, "w") as archive:
        archive.writestr("AndroidManifest.xml", build_axml("com.example.shop", 42, "1.4.2", 24))
        archive.writestr("classes.dex", b"dex\n035")
    info = manifest_from_apk(apk)
    assert (info.package, info.version_code, info.version_name, info.min_sdk) == ("com.example.shop", 42, "1.4.2", 24)
    bad = tmp_path / "bad.apk"
    with zipfile.ZipFile(bad, "w") as archive:
        archive.writestr("AndroidManifest.xml", b"<manifest/>")
    with pytest.raises(EmulatorError):
        manifest_from_apk(bad)
    aapt = parse_aapt2_badging("package: name='com.x.y' versionCode='7' versionName='2.0'\nsdkVersion:'26'\ntargetSdkVersion:'34'\n")
    assert (aapt.package, aapt.version_code, aapt.min_sdk, aapt.target_sdk) == ("com.x.y", 7, 26, 34)


# parsers
def test_device_output_parsers():
    assert parse_meminfo("MemTotal:        2035100 kB\nMemAvailable:     812345 kB\n") == {"MemTotal": 2035100, "MemAvailable": 812345}
    app = parse_app_meminfo("  Dalvik Heap     1234     1200        0        0     2000     6144     3072     3072\n   TOTAL PSS:    51200            TOTAL RSS:   102400\n")
    assert app == {"pss_mb": 50.0, "rss_mb": 100.0, "heap_alloc_mb": 3.0, "heap_size_mb": 6.0}
    gfx = parse_gfxinfo("Total frames rendered: 240\nJanky frames: 12 (5.00%)\n50th percentile: 8ms\n90th percentile: 14ms\n95th percentile: 19ms\n"
                        "99th percentile: 48ms\nHISTOGRAM: 5ms=100 16ms=120 17ms=10 32ms=8 750ms=2\n")
    assert gfx["total_frames"] == 240 and gfx["p95_ms"] == 19 and gfx["slow_frames"] == 20 and gfx["frozen_frames"] == 2
    assert parse_am_start("Status: ok\nLaunchState: COLD\nActivity: com.x/.Main\nTotalTime: 1834\nWaitTime: 1850\nComplete\n") == \
        {"status": "ok", "launch_state": "COLD", "total_time_ms": 1834, "wait_time_ms": 1850}
    assert parse_displayed(["1700000000.1 I ActivityTaskManager: Displayed com.x/.Main: +1s234ms"], "com.x") == 1234
    assert parse_displayed(["1700000000.1 I ActivityTaskManager: Fully drawn com.x/.Main: +845ms"], "com.x", "Fully drawn") == 845
    ping = parse_ping("20 packets transmitted, 19 received, 5% packet loss, time 3803ms\nrtt min/avg/max/mdev = 180.1/212.5/301.2/28.4 ms\n")
    assert ping == {"transmitted": 20, "received": 19, "loss_pct": 5.0, "rtt_min_ms": 180.1, "rtt_avg_ms": 212.5, "rtt_max_ms": 301.2, "rtt_mdev_ms": 28.4}
    assert parse_statfs("4096 524288 102400") == (4096 * 524288, 4096 * 102400)
    assert parse_df("Filesystem 1K-blocks Used Available Use% Mounted on\n/dev/block/dm-5 2031616 1500000 409600 79% /data\n") == (2031616 * 1024, 409600 * 1024)
    stat = "cpu  100 0 100 800 0 0 0\n1234 (com.x) S 1 1 0 0 -1 0 0 0 0 0 50 10 0 0\ncpu  150 0 150 900 0 0 0\n1234 (com.x) S 1 1 0 0 -1 0 0 0 0 0 70 20 0 0\n"
    assert parse_proc_stat_pair(stat) == 15.0
    battery = parse_dumpsys_battery("  AC powered: false\n  status: 3\n  health: 2\n  present: true\n  level: 20\n  temperature: 250\n")
    assert battery == {"ac_powered": False, "status": 3, "health": 2, "present": True, "level": 20, "temperature_tenths_c": 250}
    assert parse_saver("mSettingBatterySaverEnabled=true", "") is True and parse_saver("", "0") is False


def test_crash_and_anr_detection():
    lines = ["1700000001.0 1 2 E AndroidRuntime: FATAL EXCEPTION: main", "1700000001.0 1 2 E AndroidRuntime: Process: com.x, PID: 99",
             "1700000001.0 1 2 E AndroidRuntime: java.lang.NullPointerException", "1700000005.0 1 2 I am_anr  : [0,99,com.x,0,Input dispatching timed out]",
             "1700000009.0 1 2 E AndroidRuntime: FATAL EXCEPTION: main", "1700000009.0 1 2 E AndroidRuntime: Process: com.other, PID: 5"]
    found = detect_failures(lines, "com.x")
    assert len(found["crashes"]) == 1 and len(found["anrs"]) == 1 and found["native_crashes"] == []


def test_thermal_is_never_faked_on_emulator():
    dump = ("Thermal Status: 0\nHAL Ready: true\nCurrent temperatures from HAL:\n\tTemperature{mValue=41.5, mType=0, mName=cpu0, mStatus=0}\n"
            "\tTemperature{mValue=33.0, mType=2, mName=battery, mStatus=0}\nCurrent cooling devices from HAL:\n")
    parsed = parse_thermalservice(dump)
    assert parsed["status"] == 0 and parsed["temperatures"][0]["type_name"] == "cpu"
    snap = {"thermal": parsed, "battery": {"temperature_tenths_c": 250}, "cpufreq_khz": {}}
    emu = {r.parameter_key: r for r in ThermalManager(None, emulator=True).measurements(snap)}
    assert emu["thermal.status"].status is MeasurementStatus.EMULATED
    assert emu["cpu.temperature_c"].status is MeasurementStatus.UNSUPPORTED and emu["cpu.temperature_c"].value is None
    assert emu["battery.temperature_c"].status is MeasurementStatus.UNSUPPORTED
    assert emu["thermal.headroom"].status is MeasurementStatus.UNAVAILABLE
    phys = {r.parameter_key: r for r in ThermalManager(None, emulator=False).measurements(snap)}
    assert phys["cpu.temperature_c"].value == 41.5 and phys["battery.temperature_c"].value == 25.0   # battery temp never reused as CPU temp
    assert phys["device.skin_temperature_c"].status is MeasurementStatus.UNAVAILABLE


def test_netem_arguments():
    cond = NetworkCondition(latency_ms=100, jitter_ms=20, packet_loss_pct=3, download_kbps=1500, upload_kbps=500, duplication_pct=1)
    assert NetemGateway.netem_args(cond, direction="egress", seed=7) == ["netem", "delay", "100ms", "20ms", "distribution", "normal", "loss", "3.0%",
                                                                          "duplicate", "1.0%", "rate", "500kbit", "seed", "7"]
    assert NetemGateway.netem_args(cond, direction="ingress", seed=None) == ["netem", "loss", "3.0%", "duplicate", "1.0%", "rate", "1500kbit"]
    burst = NetworkCondition(burst_loss_enter_pct=2, burst_loss_exit_pct=30)
    assert ["loss", "gemodel", "2.0%", "30.0%"] == NetemGateway.netem_args(burst, direction="egress", seed=None)[1:5]
    assert NetemGateway.netem_args(NetworkCondition(online=False), direction="ingress", seed=None) == ["netem", "loss", "100%"]


# storage logic
class DiskSim:
    """Simulated /data partition: 2 GiB, 1.5 GiB free; fallocate consumes space."""
    def __init__(self, free_mb=1536, total_mb=2048):
        self.total, self.free, self.files = total_mb * 1048576, free_mb * 1048576, {}
        self.baseline = self.free

    def __call__(self, args):
        cmd = args[1] if args[0] == "shell" else ""
        if cmd.startswith("stat -f"):
            return f"4096 {self.total // 4096} {self.free // 4096}"
        if cmd.startswith("fallocate -l"):
            size, path = int(cmd.split()[2]), cmd.split()[3]
            self.files[path] = size
            self.free -= size
            return ""
        if cmd.startswith("rm -rf"):
            self.free += sum(self.files.values())
            self.files.clear()
            return ""
        if cmd.startswith("ls"):
            return ("", 1)
        return ""


def test_storage_fill_reaches_target_and_is_fully_released():
    disk = DiskSim()
    storage = StorageManager(FakeAdb(disk), reserve_mb=200)
    record = storage.apply_free_target(400 * 1048576, ("storage.free_target_mb", 400, "MB"))
    assert record.application_status is ApplicationStatus.APPLIED_VERIFIED and abs(record.observed_value - 400) <= 24
    assert len(record.evidence["chunks"]) == 5        # 1136 MB in 256 MB chunks
    released = storage.release()
    assert released.application_status is ApplicationStatus.REVERTED and disk.free == disk.baseline


def test_storage_refuses_targets_below_safety_reserve():
    storage = StorageManager(FakeAdb(DiskSim()), reserve_mb=200)
    with pytest.raises(UnsafeConditionError):
        storage.apply_free_target(100 * 1048576, ("storage.free_target_mb", 100, "MB"))


def test_storage_reports_failure_when_space_cannot_be_created():
    storage = StorageManager(FakeAdb(DiskSim(free_mb=300)), reserve_mb=200)
    record = storage.apply_free_target(800 * 1048576, ("storage.free_target_mb", 800, "MB"))
    assert record.application_status is ApplicationStatus.FAILED and "cannot create space" in record.evidence["reason"]


# scenario logic
class AppSim:
    """Scripted device for scenario-executor tests."""
    def __init__(self, crash=False, ui_text=None):
        self.crash, self.ui_text, self.pid = crash, ui_text, None

    def __call__(self, args):
        cmd = args[1] if args[0] == "shell" else " ".join(args)
        if cmd.startswith("date"):
            return "1700000000"
        if cmd.startswith("am start"):
            self.launches = getattr(self, "launches", 0) + 1
            self.pid = 4320 + self.launches      # a new process gets a new pid, as on Android
            return "Status: ok\nLaunchState: COLD\nTotalTime: 1450\nWaitTime: 1500\nComplete"
        if cmd.startswith("am force-stop"):
            self.pid = None
            return ""
        if cmd.startswith("pidof"):
            return (str(self.pid), 0) if self.pid else ("", 1)
        if cmd.startswith("dumpsys activity activities"):
            return "  mResumedActivity: ActivityRecord{a1b2 u0 com.example.shop/.MainActivity t5}" if self.pid else ""
        if cmd.startswith("dumpsys meminfo"):
            return "   TOTAL PSS:    51200            TOTAL RSS:   102400\n"
        if cmd.startswith("head -1 /proc/stat"):
            return "cpu  100 0 100 800\n4321 (shop) S 1 1 0 0 -1 0 0 0 0 0 5 5 0\ncpu  150 0 150 900\n4321 (shop) S 1 1 0 0 -1 0 0 0 0 0 15 15 0\n"
        if cmd.startswith("uiautomator dump"):
            return ""
        if cmd.startswith("cat /data/local/tmp/pocketlab_ui.xml"):
            nodes = f'<node text="{self.ui_text}" resource-id="com.example.shop:id/cart_badge" content-desc="" bounds="[10,20][110,60]"/>' if self.ui_text else ""
            return f"<?xml version='1.0' encoding='UTF-8' standalone='yes' ?><hierarchy rotation=\"0\">{nodes}</hierarchy>"
        if args[:2] == ("exec-out", "screencap"):
            return "\x89PNG fake"
        return ""


class FakeLogcat:
    def __init__(self, lines):
        self.lines = lines

    def lines_since(self, since, until=None):
        return self.lines


def make_executor(tmp_path, sim, *, bindings=None, crash_lines=None):
    from app.emulator.measurement_collector import MeasurementCollector
    from app.emulator.memory_manager import MemoryManager
    from app.emulator.scenario_executor import ScenarioContext, ScenarioExecutor
    adb = FakeAdb(sim)
    adb.screencap = lambda dest: (dest.parent.mkdir(parents=True, exist_ok=True), dest.write_bytes(b"\x89PNG"), dest)[2]
    memory = MemoryManager(adb, floor_mb=200, rooted=False)
    storage = StorageManager(adb, reserve_mb=200)
    request = EnvironmentRequest.model_validate({"scenario_bindings": bindings or {}})
    conditions = []
    ctx = ScenarioContext(adb=adb, package="com.example.shop", component="com.example.shop/.MainActivity", request=request,
                          collector=MeasurementCollector(adb, "com.example.shop", emulator=True, memory=memory, storage=storage),
                          memory=memory, battery=None, thermal=None, network=None, logcat=FakeLogcat(crash_lines or []),
                          artifacts_dir=tmp_path / "artifacts", emulator=True, cancelled=lambda: False,
                          emit_event=lambda *a: None, record_condition=conditions.append)
    return ScenarioExecutor(ctx)


def steps_of(key):
    from app.emulator.scenario_executor import SCENARIO_DIR
    definition = json.loads((SCENARIO_DIR / f"{key}.json").read_text())
    return [{"step_order": i, "action_type": s["action"], "configuration": s.get("config", {}), "expected": s.get("expect", {})}
            for i, s in enumerate(definition["steps"])]


def test_cold_startup_passes_only_with_evaluated_expectations(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    result = make_executor(tmp_path, AppSim()).run(1, "Cold application startup", 2, steps_of("cold_startup"))
    assert result.status == "passed", result.reason
    keys = {m.parameter_key: m for m in result.measurements}
    assert keys["performance.startup_cold_ms"].value == 1450 and keys["performance.startup_cold_ms"].status is MeasurementStatus.ACTUAL
    assert keys["performance.ttfd_ms"].status is MeasurementStatus.UNAVAILABLE      # app did not report fully drawn
    assert keys["app.pss_mb"].value == 50.0 and keys["performance.cpu_utilization_pct"].value == 10.0
    assert any(kind == "screenshot" for _, kind, _ in result.artifacts)


def test_crash_in_window_fails_scenario_even_if_steps_finish(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    crash = ["1700000001.0 1 2 E AndroidRuntime: FATAL EXCEPTION: main", "1700000001.0 1 2 E AndroidRuntime: Process: com.example.shop, PID: 4321"]
    result = make_executor(tmp_path, AppSim(), crash_lines=crash).run(1, "Cold application startup", 2, steps_of("cold_startup"))
    assert result.status == "failed" and result.failures["crashes"]


def test_missing_binding_blocks_instead_of_passing(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    result = make_executor(tmp_path, AppSim()).run(1, "Cart-state preservation", 1, steps_of("cart_state_preservation"))
    assert result.status == "blocked" and "add_to_cart" in result.reason


def test_cart_state_passes_when_bound_ui_survives_process_death(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    bindings = {"add_to_cart": {"text": "Add to cart"}, "cart_indicator": {"resource_id": "cart_badge"}}
    result = make_executor(tmp_path, AppSim(ui_text="Add to cart"), bindings=bindings).run(1, "Cart-state preservation", 1, steps_of("cart_state_preservation"))
    assert result.status == "passed", result.reason
    evaluated = {e["name"]: e["passed"] for s in result.steps for e in s.expectations}
    assert evaluated["killed"] and evaluated["pid_changed"] and evaluated["visible"]


def test_cart_state_fails_when_indicator_never_appears(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setattr("app.emulator.scenario_executor.time.monotonic", iter(range(0, 10**7, 50)).__next__)
    bindings = {"add_to_cart": {"text": "Add to cart"}, "cart_indicator": {"text": "Cart (1)"}}
    result = make_executor(tmp_path, AppSim(ui_text="Add to cart"), bindings=bindings).run(1, "Cart-state preservation", 1, steps_of("cart_state_preservation"))
    assert result.status == "failed" and "visible" in result.reason


def test_v1_abstract_steps_are_blocked(tmp_path):
    result = make_executor(tmp_path, AppSim()).run(1, "Cold application startup", 1, [{"step_order": 0, "action_type": "observe_startup"}])
    assert result.status == "blocked" and "non-executable" in result.reason


def test_low_storage_scenario_requires_condition(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    result = make_executor(tmp_path, AppSim()).run(1, "Low-storage", 2, steps_of("low_storage_handling"))
    assert result.status == "blocked" and "storage_constraint" in result.reason


# lifecycle
class RecordingReporter:
    def __init__(self, cancel_at=None):
        self.statuses, self.events, self.cancel_at = [], [], cancel_at

    def set_status(self, status, message, **fields):
        self.statuses.append(status)
        return {"cancel_requested": self.cancel_at is not None and status == self.cancel_at}

    def event(self, event_type, message, severity="info", data=None):
        self.events.append((event_type, severity, message))

    def heartbeat(self):
        return False

    def __getattr__(self, name):
        return lambda *a, **k: None


def _job():
    from app.emulator.emulator_manager import RunJob
    return RunJob(run_id=7, attempt=1, environment_id=1, environment_type="emulator", request=EnvironmentRequest(), package_name="com.example.shop",
                  apk_sha256="0" * 64, scenarios=[])


def test_lifecycle_without_sdk_fails_in_preflight_and_still_cleans(tmp_path, monkeypatch):
    from app.emulator.emulator_config import WorkerSettings
    from app.emulator.emulator_manager import EmulatorManager
    monkeypatch.delenv("ANDROID_SDK_ROOT", raising=False)
    monkeypatch.delenv("ANDROID_HOME", raising=False)
    manager = EmulatorManager(WorkerSettings(work_dir=tmp_path, heartbeat_interval_s=3600))
    reporter = RecordingReporter()
    assert manager.execute(_job(), reporter) == "failed"
    assert reporter.statuses == [S.PREFLIGHT, S.FAILED, S.CLEANING, S.CLEANED]
    assert any(t == "failure" and "preflight_failed" in m and "android sdk" in m.lower() for t, _, m in reporter.events)
    assert not (tmp_path / "runs" / "run_7_a1").exists()


def test_lifecycle_cancellation_goes_through_cleanup(tmp_path, monkeypatch):
    from app.emulator.emulator_config import WorkerSettings
    from app.emulator.emulator_manager import EmulatorManager
    manager = EmulatorManager(WorkerSettings(work_dir=tmp_path, heartbeat_interval_s=3600))
    reporter = RecordingReporter(cancel_at=S.PREFLIGHT)
    assert manager.execute(_job(), reporter) == "canceled"
    assert reporter.statuses[-3:] == [S.CANCEL_REQUESTED, S.CLEANING, S.CLEANED]


def test_host_lock_prevents_concurrent_emulator_jobs(tmp_path):
    from app.emulator.emulator_manager import HostLock
    from app.emulator.exceptions import ResourceUnavailableError
    first, second = HostLock(tmp_path / "emulator.lock"), HostLock(tmp_path / "emulator.lock")
    first.acquire()
    try:
        with pytest.raises(ResourceUnavailableError):
            second.acquire()
    finally:
        first.release()
    second.acquire()
    second.release()
