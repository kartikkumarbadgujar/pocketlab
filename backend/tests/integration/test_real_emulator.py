"""REAL Android Emulator integration test. This is the only test that proves actual execution works.

Requirements: Android SDK (platform-tools, emulator, a google_apis system image), hardware acceleration,
and an authorized test APK you own. Enable explicitly:

    $env:POCKETLAB_REAL_EMULATOR = "1"
    $env:ANDROID_SDK_ROOT = "C:\\Users\\you\\AppData\\Local\\Android\\Sdk"
    $env:POCKETLAB_TEST_APK = "C:\\path\\to\\your-demo-app.apk"
    $env:POCKETLAB_TEST_PACKAGE = "com.yourteam.demo"
    $env:POCKETLAB_TEST_API = "30"            # must match an installed google_apis image
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
    definition = json.loads((SCENARIO_DIR / "cold_startup.json").read_text())
    steps = [{"step_order": i, "action_type": s["action"], "configuration": s.get("config", {}), "expected": s.get("expect", {})}
             for i, s in enumerate(definition["steps"])]
    request = EnvironmentRequest.model_validate({
        "device": {"api_level": int(os.environ.get("POCKETLAB_TEST_API", "30")), "ram_mb": 2048, "data_partition_mb": 2048},
        "storage": {"free_target_mb": 400},
        "network": {"download_kbps": 1500, "upload_kbps": 500, "latency_ms": 180},
        "battery": {"level_pct": 20, "charging": False, "ac_connected": False},
    })
    job = RunJob(run_id=990001, attempt=1, environment_id=1, environment_type="emulator", request=request, package_name=package,
                 apk_sha256=hashlib.sha256(apk.read_bytes()).hexdigest(),
                 scenarios=[{"run_scenario_id": 1, "name": definition["name"], "version": definition["version"], "steps": steps}])
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
    status, result = reporter.scenarios[1]
    assert status in ("passed", "failed")   # a real outcome, evaluated against expectations
    assert any(kind == "screenshot" for _, kind in reporter.artifacts) and any(kind == "logcat" for _, kind in reporter.artifacts)
    assert any(m.parameter_key == "performance.startup_cold_ms" and m.value for m in reporter.metrics)
    assert any(t == "cleanup" and sev == "info" for t, sev, _, _ in reporter.events), "cleanup was not verified"
    assert not list((settings.avd_home).glob("pocketlab_run_*")), "temporary AVD was not removed"
