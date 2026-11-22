"""REAL Android Emulator integration test. This is the only test that proves actual execution works.

Requirements: Android SDK (platform-tools, emulator, a google_apis system image), hardware acceleration,
and an authorized test APK you own. Enable explicitly:

    $env:POCKETLAB_REAL_EMULATOR = "1"
    $env:ANDROID_SDK_ROOT = "C:\\Users\\you\\AppData\\Local\\Android\\Sdk"
    $env:POCKETLAB_TEST_APK = "C:\\path\\to\\your-demo-app.apk"
    $env:POCKETLAB_TEST_PACKAGE = "com.yourteam.demo"
    $env:POCKETLAB_TEST_API = "30"            # must match an installed google_apis image
    $env:POCKETLAB_TEST_RAM_MB = "2048"
    $env:POCKETLAB_TEST_DATA_PARTITION_MB = "4096"
    pytest -m real_emulator -s tests/integration

It runs the full lifecycle (preflight, AVD, boot, verification, install, conditions, scenario, collection,
cleanup) with a local in-memory reporter in place of the HTTP runner API.
"""
import hashlib
import os
import shutil
from pathlib import Path

import pytest

from app.emulator.capability_detector import CapabilityDetector
from app.emulator.emulator_config import WorkerSettings
from app.emulator.emulator_manager import EmulatorManager, RunJob
from app.emulator.scenario_executor import SCENARIO_DIR
from app.emulator.schemas import ApplicationStatus, EnvironmentRequest, EnvironmentStatus as S

pytestmark = pytest.mark.real_emulator
ENABLED = os.environ.get("POCKETLAB_REAL_EMULATOR") == "1"
ALL_FRONTEND_PARAMETERS = os.environ.get("POCKETLAB_TEST_ALL_FRONTEND_PARAMETERS") == "1"


class LocalReporter:
    def __init__(self, apk: Path):
        self.apk, self.statuses, self.events, self.conditions_list, self.metrics = apk, [], [], [], []
        self.findings, self.artifacts, self.scenarios = [], [], {}

    def set_status(self, status, message, **fields):
        self.statuses.append(status)
        print(f"[{status.value}] {message}")
        return {}

    def event(self, event_type, message, severity="info", data=None):
        self.events.append((event_type, severity, message, data))
        print(f"  {severity:7} {event_type}: {message}")

    def conditions(self, records):
        self.conditions_list += records
        for record in records:
            print(f"  condition {record.parameter_key}: {record.application_status.value}; requested={record.requested_value!r}; observed={record.observed_value!r}")
            if record.application_status is ApplicationStatus.FAILED:
                print(f"    evidence: {record.evidence}")

    def measurements(self, records, device_id):
        self.metrics += records

    def finding(self, **fields):
        self.findings.append(fields)

    def artifact(self, path, artifact_type, content_type):
        keep = Path(os.environ.get("POCKETLAB_TEST_ARTIFACTS", "real_emulator_artifacts"))
        keep.mkdir(exist_ok=True)
        shutil.copy2(path, keep / path.name)
        self.artifacts.append((path.name, artifact_type))
        return len(self.artifacts)

    def scenario_result(self, run_scenario_id, status, result):
        self.scenarios[run_scenario_id] = (status, result)

    def register_device(self, properties):
        return 1

    def heartbeat(self):
        return False

    def download_apk(self, destination):
        shutil.copy2(self.apk, destination)
        return destination


@pytest.mark.skipif(not ENABLED, reason="set POCKETLAB_REAL_EMULATOR=1 with a real Android SDK to run")
def test_full_real_emulator_lifecycle(tmp_path):
    apk = Path(os.environ["POCKETLAB_TEST_APK"])
    package = os.environ["POCKETLAB_TEST_PACKAGE"]
    settings = WorkerSettings.from_env()
    settings.work_dir = tmp_path / "worker"
    report, sdk, _ = CapabilityDetector(settings).detect()
    assert report.cap("emulator").value == "available", report.tools["emulator"].reason
    assert report.cap("virtualization").value == "available", report.tools["virtualization"].reason
    import json
    api_level = int(os.environ.get("POCKETLAB_TEST_API", "30"))
    definitions = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in SCENARIO_DIR.glob("*.json")}
    selected = ["cold_startup"]
    timeline_steps = []
    request_data = {
        "device": {"api_level": api_level,
                   "ram_mb": int(os.environ.get("POCKETLAB_TEST_RAM_MB", "2048")),
                   "data_partition_mb": int(os.environ.get("POCKETLAB_TEST_DATA_PARTITION_MB", "4096"))},
        "storage": {"free_target_mb": 400},
        "network": {"download_kbps": 1500, "upload_kbps": 500, "latency_ms": 180},
        "battery": {"level_pct": 20, "charging": False, "ac_connected": False},
        "strict_conditions": os.environ.get("POCKETLAB_TEST_STRICT_CONDITIONS", "1") != "0",
    }
    if ALL_FRONTEND_PARAMETERS:
        timeline = [
            {"start_s": 0, "end_s": 3, "label": "stable", "condition": {"online": True}},
            {"start_s": 3, "end_s": 4, "label": "latency", "condition": {"online": True, "latency_ms": 500}},
            {"start_s": 4, "end_s": 6, "label": "offline", "condition": {"online": False}},
            {"start_s": 6, "end_s": 8, "label": "lossy recovery", "condition": {"online": True, "packet_loss_pct": 3}},
            {"start_s": 8, "end_s": 10, "label": "jitter", "condition": {"online": True, "latency_ms": 200, "jitter_ms": 150}},
        ]
        request_data = {
            "device": {"api_level": api_level, "ram_mb": 4096 if api_level >= 37 else 2048,
                       "cpu_cores": 4, "data_partition_mb": 6144 if api_level >= 37 else 4096,
                       "vm_heap_mb": 576, "screen": {"width_px": 1080, "height_px": 2400, "density_dpi": 420}},
            "storage": {"free_target_mb": 400},
            "memory": {"pressure_enabled": True, "target_available_mb": 512},
            "network": {"download_kbps": 1500, "upload_kbps": 500, "latency_ms": 180, "jitter_ms": 40,
                        "packet_loss_pct": 3, "dns_failure": True, "seed": 7, "timeline": timeline},
            "battery": {"level_pct": 20, "charging": False, "ac_connected": False, "saver_enabled": True},
            "thermal": {"observe": True, "require_physical_validation": True, "sustained_workload_seconds": 10},
            "scenario_bindings": {"scroll_screen": {"text_contains": "Connected devices"}},
            "grant_runtime_permissions": True,
            "strict_conditions": True,
        }
        selected += ["low_storage_handling", "battery_saver_behaviour", "memory_pressure_recovery", "sustained_thermal"]
        timeline_steps = [
            {"action_type": "launch_app", "configuration": {"cold": True}, "expected": {"launch_status": "ok"}},
            {"action_type": "network_timeline_start", "configuration": {}},
            {"action_type": "wait", "configuration": {"seconds": 11}},
            {"action_type": "network_timeline_await", "configuration": {"timeout_s": 180}},
            {"action_type": "assert_app_healthy", "expected": {"process_alive": True, "foreground": True, "no_crash": True, "no_anr": True}},
        ]
    request = EnvironmentRequest.model_validate(request_data)
    scenarios = []
    for run_scenario_id, key in enumerate(selected, 1):
        definition = definitions[key]
        steps = [{"step_order": i, "action_type": s["action"], "configuration": s.get("config", {}), "expected": s.get("expect", {})}
                 for i, s in enumerate(definition["steps"])]
        scenarios.append({"run_scenario_id": run_scenario_id, "name": definition["name"], "version": definition["version"], "steps": steps})
    if timeline_steps:
        scenarios.append({"run_scenario_id": len(scenarios) + 1, "name": "Frontend network timeline coverage", "version": 1,
                          "steps": [{"step_order": i, **step} for i, step in enumerate(timeline_steps)]})
    if ALL_FRONTEND_PARAMETERS:
        scenarios.append({"run_scenario_id": len(scenarios) + 1, "name": "Frontend scroll binding coverage", "version": 1,
                          "steps": [
                              {"step_order": 0, "action_type": "launch_app", "configuration": {"cold": True}, "expected": {"launch_status": "ok"}},
                              {"step_order": 1, "action_type": "assert_visible", "configuration": {"selector": {"binding": "scroll_screen"}}, "expected": {"visible": True}},
                              {"step_order": 2, "action_type": "assert_app_healthy", "expected": {"process_alive": True, "no_crash": True, "no_anr": True}},
                          ]})
    job = RunJob(run_id=990001, attempt=1, environment_id=1, environment_type="emulator", request=request, package_name=package,
                 apk_sha256=hashlib.sha256(apk.read_bytes()).hexdigest(),
                 scenarios=scenarios)
    reporter = LocalReporter(apk)
    outcome = EmulatorManager(settings).execute(job, reporter)

    assert outcome == "completed", [e for e in reporter.events if e[1] == "error"]
    for required in (S.PREFLIGHT, S.PROVISIONING, S.BOOTING, S.READY, S.INSTALLING, S.APPLYING_CONDITIONS, S.VERIFYING_CONDITIONS,
                     S.EXECUTING, S.COLLECTING_METRICS, S.ANALYZING, S.COMPLETED, S.CLEANING, S.CLEANED):
        assert required in reporter.statuses
    by_key = {c.parameter_key: c for c in reporter.conditions_list}
    assert by_key["device.ram_mb"].application_status is ApplicationStatus.APPLIED_VERIFIED
    assert by_key["storage.free_target_mb"].application_status is ApplicationStatus.APPLIED_VERIFIED
    assert by_key["battery.level_pct"].application_status is ApplicationStatus.EMULATED
    assert by_key["network.latency_ms"].application_status in (ApplicationStatus.APPLIED_VERIFIED, ApplicationStatus.APPLIED_UNVERIFIED)
    assert by_key["storage.allocation_cleanup"].application_status is ApplicationStatus.REVERTED
    if ALL_FRONTEND_PARAMETERS:
        assert by_key["device.data_partition_mb"].application_status is ApplicationStatus.APPLIED_VERIFIED
        assert by_key["device.api_level"].application_status is ApplicationStatus.APPLIED_VERIFIED
        assert by_key["device.cpu_cores"].application_status is ApplicationStatus.APPLIED_VERIFIED
        assert by_key["device.screen"].application_status is ApplicationStatus.APPLIED_VERIFIED
        assert by_key["device.vm_heap_mb"].application_status is ApplicationStatus.APPLIED_VERIFIED
        assert by_key["memory.target_available_mb"].application_status is ApplicationStatus.UNSUPPORTED
        assert by_key["network.bandwidth_kbps"].application_status is ApplicationStatus.APPLIED_VERIFIED
        assert by_key["network.jitter_ms"].application_status is ApplicationStatus.EMULATED
        assert by_key["network.packet_loss_pct"].application_status is ApplicationStatus.REQUIRES_LINUX_GATEWAY
        assert by_key["network.dns_failure"].application_status is ApplicationStatus.REQUIRES_LINUX_GATEWAY
        assert by_key["network.timeline"].application_status is ApplicationStatus.REQUIRES_LINUX_GATEWAY
        assert by_key["thermal.throttling_validation"].application_status is ApplicationStatus.REQUIRES_PHYSICAL_DEVICE
        assert any(c.parameter_key == "battery.saver_enabled" and c.application_status is ApplicationStatus.APPLIED_VERIFIED
                   for c in reporter.conditions_list)
        assert any(m.parameter_key == "thermal.status" and m.status.value == "EMULATED" for m in reporter.metrics)
        assert reporter.scenarios[4][0] == "blocked"  # root-only memory pressure is reported as unsupported on Play Store images
        assert reporter.scenarios[6][0] == "blocked"  # unsupported packet loss blocks only this scenario, not the whole run
        assert reporter.scenarios[7][0] == "passed"   # the live UI binding matched the app's Connected devices label
    status, result = reporter.scenarios[1]
    assert status == "passed"
    assert any(kind == "screenshot" for _, kind in reporter.artifacts) and any(kind == "logcat" for _, kind in reporter.artifacts)
    assert any(m.parameter_key == "performance.startup_cold_ms" and m.value for m in reporter.metrics)
    assert any(t == "cleanup" and sev == "info" for t, sev, _, _ in reporter.events), "cleanup was not verified"
    assert not list((settings.avd_home).glob("pocketlab_run_*")), "temporary AVD was not removed"
