"""API-level tests of the execution-environment protocol (real FastAPI + SQLAlchemy; no Android tooling involved).

The capability report posted here is test input shaped like a worker's report; these tests prove the API's
queueing, gating, state machine, provenance and cancellation rules - not that an emulator ran.
"""
import io
from datetime import datetime, timedelta, timezone

import pytest
from tests.apk_factory import valid_apk_bytes

RUNNER = {"Authorization": "Bearer test-runner-token"}
IDENTITY = {"agent_name": "android-worker", "authentication_identity": "worker-key"}
EMU = {**IDENTITY, "environment_types": ["emulator"]}


def capability_report(*, packet_loss=False, ram_available=12000):
    supported = {"status": "supported"}
    gateway = supported if packet_loss else {"status": "requires_linux_gateway", "reason": "Windows worker"}
    caps = {k: supported for k in ("avd_creation", "guest_ram_configuration", "cpu_core_configuration", "display_configuration", "storage_configuration",
                                   "storage_free_space_control", "network_bandwidth", "network_latency", "network_jitter", "network_offline",
                                   "battery_state_simulation", "battery_saver", "memory_pressure", "perfetto", "frame_metrics", "ui_automation",
                                   "thermal_observation")}
    caps.update(network_gateway=gateway, packet_loss=gateway, physical_thermal_validation={"status": "requires_physical_device"},
                macrobenchmark={"status": "unsupported"})
    return {"host": {"os": "windows", "arch": "amd64", "cpu_count": 8, "ram_total_mb": 16000, "ram_available_mb": ram_available,
                     "disk_free_mb": 50000, "storage_reserve_mb": 200},
            "tools": {k: {"status": "available"} for k in ("android_sdk", "adb", "emulator", "avdmanager", "virtualization")},
            "capabilities": caps,
            "system_images": [{"package": "system-images;android-30;google_apis;x86_64", "api_level": 30, "tag": "google_apis", "abi": "x86_64", "rootable": True}]}


def entry_level(**overrides):
    config = {"environment_type": "emulator", "device": {"api_level": 30, "ram_mb": 2048, "data_partition_mb": 2048},
              "storage": {"free_target_mb": 400}, "network": {"download_kbps": 1500, "upload_kbps": 500, "latency_ms": 180},
              "battery": {"level_pct": 20, "charging": False, "ac_connected": False}}
    config.update(overrides)
    return config


def online_worker(client, **kwargs):
    r = client.post("/api/v1/runner/heartbeat", json={**IDENTITY, "capability_report": capability_report(**kwargs)}, headers=RUNNER)
    assert r.status_code == 200, r.text


def approved_build(client, headers, name="emu-app"):
    project = client.post("/api/v1/projects", json={"name": name}, headers=headers).json()
    apk = valid_apk_bytes("com.example.shop")
    build = client.post(f"/api/v1/projects/{project['id']}/builds",
                        files={"file": ("app.apk", apk, "application/vnd.android.package-archive")}, headers=headers).json()
    from app.models import AppBuild
    with client.app.state.testing_session() as db:
        db.get(AppBuild, build["id"]).scan_status = "approved"
        db.commit()
    return project, build, apk


def scenario_version(client, headers, name, version):
    scenarios = client.get("/api/v1/scenarios", headers=headers).json()
    sid = next(s["id"] for s in scenarios if s["name"] == name)
    return next(v["id"] for v in client.get(f"/api/v1/scenarios/{sid}", headers=headers).json()["versions"] if v["version"] == version)


def queued_run(client, headers):
    project, build, apk = approved_build(client, headers)
    sv = scenario_version(client, headers, "Cold application startup", 2)
    run = client.post("/api/v1/test-runs", json={"project_id": project["id"], "app_build_id": build["id"], "scenario_version_ids": [sv]}, headers=headers).json()
    return run, build, apk


def test_capabilities_report_no_worker_until_one_reports(client, auth_headers):
    headers, _ = auth_headers
    assert client.get("/api/v1/environment/capabilities", headers=headers).json()["status"] == "no_worker_online"
    online_worker(client)
    body = client.get("/api/v1/environment/capabilities", headers=headers).json()
    assert body["status"] == "available"
    assert body["workers"][0]["summary"]["packet_loss"] == "requires_linux_gateway"
    assert client.get("/api/v1/environment/capabilities").status_code == 401


def test_preflight_blocks_without_worker_and_plans_honestly(client, auth_headers):
    headers, _ = auth_headers
    blocked = client.post("/api/v1/environment/preflight", json={"configuration": entry_level()}, headers=headers).json()
    assert blocked["ok"] is False and "No PocketLab Android worker is online" in blocked["blocking_errors"][0]
    online_worker(client)
    config = entry_level(network={"latency_ms": 180, "packet_loss_pct": 3, "jitter_ms": 40}, thermal={"observe": True, "require_physical_validation": True})
    plan = client.post("/api/v1/environment/preflight", json={"configuration": config}, headers=headers).json()
    assert plan["ok"] is True
    outcomes = {i["parameter_key"]: i["outcome"] for i in plan["items"]}
    assert outcomes["device.ram_mb"] == "will_apply"
    assert outcomes["network.latency_ms"] == "will_apply"
    assert outcomes["network.packet_loss_pct"] == "requires_linux_gateway"
    assert outcomes["network.jitter_ms"] == "emulated"
    assert outcomes["thermal.throttling_validation"] == "requires_physical_device"
    assert any("physical Android device" in w for w in plan["warnings"])
    assert plan["selected_system_image"]["package"] == "system-images;android-30;google_apis;x86_64"


def test_preflight_blocks_unsafe_or_impossible_requests(client, auth_headers):
    headers, _ = auth_headers
    online_worker(client, ram_available=2500)
    plan = client.post("/api/v1/environment/preflight", json={"configuration": entry_level(storage={"free_target_mb": 50}, device={"api_level": 33, "ram_mb": 2048})},
                       headers=headers).json()
    errors = " ".join(plan["blocking_errors"])
    assert plan["ok"] is False and "safety reserve" in errors and "No installed system image for API 33" in errors and "RAM" in errors


def test_preflight_with_netem_gateway_applies_packet_loss(client, auth_headers):
    headers, _ = auth_headers
    online_worker(client, packet_loss=True)
    plan = client.post("/api/v1/environment/preflight", json={"configuration": entry_level(network={"packet_loss_pct": 3, "latency_ms": 100})}, headers=headers).json()
    item = next(i for i in plan["items"] if i["parameter_key"] == "network.packet_loss_pct")
    assert item["outcome"] == "will_apply" and "netem" in item["mechanism"]


def test_full_environment_lifecycle_protocol(client, auth_headers):
    headers, _ = auth_headers
    online_worker(client)
    run, build, apk = queued_run(client, headers)
    prepared = client.post(f"/api/v1/test-runs/{run['id']}/environment/prepare", json={"configuration": entry_level()}, headers=headers)
    assert prepared.status_code == 202 and prepared.json()["status"] == "PREPARED"
    # Not claimable before execute, and never by a legacy runner.
    assert client.post("/api/v1/runner/jobs/claim", json=EMU, headers=RUNNER).json()["job"] is None
    assert client.post(f"/api/v1/test-runs/{run['id']}/execute", headers=headers).status_code == 202
    assert client.post("/api/v1/runner/jobs/claim", json=IDENTITY, headers=RUNNER).json()["job"] is None
    job = client.post("/api/v1/runner/jobs/claim", json=EMU, headers=RUNNER).json()["job"]
    assert job["run_id"] == run["id"] and job["environment"]["requested_configuration"]["device"]["ram_mb"] == 2048
    assert job["build"]["apk_sha256"] == build["apk_sha256"] and job["scenarios"][0]["name"] == "Cold application startup"
    assert job["scenarios"][0]["steps"][1]["action_type"] == "launch_app"
    base = f"/api/v1/runner/jobs/{run['id']}"
    downloaded = client.post(f"{base}/apk", json=IDENTITY, headers=RUNNER)
    assert downloaded.status_code == 200 and downloaded.content == apk

    def status(s, **extra):
        return client.put(f"{base}/environment", json={**IDENTITY, "status": s, "message": f"-> {s}", **extra}, headers=RUNNER)
    for s in ("PREFLIGHT", "PROVISIONING"):
        assert status(s).status_code == 200
    assert status("QUEUED").status_code == 409            # backwards transition rejected
    assert status("BOOTING", emulator_name="pocketlab_run_1_a1_x", emulator_serial="emulator-5556", android_api_level=30).status_code == 200
    assert status("READY").status_code == 200
    device_id = client.post(f"{base}/environment/device", json={**IDENTITY, "device_name": "emulator:pocketlab_run_1", "model": "sdk_gphone64_x86_64",
                                                                "api_level": 30, "total_ram_mb": 1987}, headers=RUNNER).json()["device_id"]
    cond = {"parameter_key": "device.ram_mb", "requested_value": 2048, "applied_value": 2048, "observed_value": 1987.4, "unit": "MB",
            "application_status": "applied_verified", "mechanism": "hw.ramSize; /proc/meminfo", "evidence": {"MemTotal": 2035100}}
    gateway = {"parameter_key": "network.packet_loss_pct", "requested_value": 3, "application_status": "requires_linux_gateway"}
    assert client.post(f"{base}/environment/conditions", json={**IDENTITY, "conditions": [cond, gateway]}, headers=RUNNER).status_code == 201
    now = datetime.now(timezone.utc).isoformat()
    sample = {"parameter_key": "memory.available_mb", "numeric_value": 812.5, "unit": "MB", "source": "android_api", "sample_key": "s1",
              "observed_at": now, "device_id": device_id, "measurement_status": "ACTUAL", "collection_method": "/proc/meminfo MemAvailable"}
    assert client.post(f"{base}/metrics/batch", json={**IDENTITY, "samples": [sample]}, headers=RUNNER).json() == {"inserted": 1, "duplicates": 0}
    configured = {**sample, "sample_key": "s2", "measurement_status": "CONFIGURED"}
    assert client.post(f"{base}/metrics/batch", json={**IDENTITY, "samples": [configured]}, headers=RUNNER).status_code == 422
    unavailable = {"parameter_key": "thermal.headroom", "unit": None, "source": "android_api", "sample_key": "s3", "observed_at": now, "device_id": device_id,
                   "validity_status": "unavailable", "measurement_status": "UNAVAILABLE", "collection_method": "PowerManager", "metadata": {"reason": "no helper app"}}
    assert client.post(f"{base}/metrics/batch", json={**IDENTITY, "samples": [unavailable]}, headers=RUNNER).status_code == 202
    other_device = client.post("/api/v1/devices/register", json={"device_name": "someone else"}, headers=headers).json()["id"]
    wrong = {**sample, "sample_key": "s4", "device_id": other_device}
    assert client.post(f"{base}/metrics/batch", json={**IDENTITY, "samples": [wrong]}, headers=RUNNER).status_code == 422
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    art = client.post(f"{base}/artifacts", data={**IDENTITY, "artifact_type": "screenshot"}, files={"file": ("shot.png", png, "image/png")}, headers=RUNNER)
    assert art.status_code == 201
    fake_png = client.post(f"{base}/artifacts", data={**IDENTITY, "artifact_type": "screenshot"}, files={"file": ("x.png", b"GIF89a....", "image/png")}, headers=RUNNER)
    assert fake_png.status_code == 422
    content = client.get(f"/api/v1/test-runs/{run['id']}/artifacts/{art.json()['id']}/content", headers=headers)
    assert content.status_code == 200 and content.content == png and content.headers["x-content-type-options"] == "nosniff"
    rs_id = job["scenarios"][0]["run_scenario_id"]
    assert client.post(f"{base}/scenarios/{rs_id}", json={**IDENTITY, "status": "failed", "result": {"reason": "crash"}}, headers=RUNNER).status_code == 200
    env = client.get(f"/api/v1/test-runs/{run['id']}/environment", headers=headers).json()
    assert env["status"] == "READY" and env["emulator_serial"] == "emulator-5556" and env["device_id"] == device_id
    assert {c["application_status"] for c in env["conditions"]} == {"applied_verified", "requires_linux_gateway"}
    assert env["scenarios"][0]["status"] == "failed"
    events = client.get(f"/api/v1/test-runs/{run['id']}/environment/events", headers=headers).json()
    assert [e["status"] for e in events if e["event_type"] == "status_changed"] == ["PREFLIGHT", "PROVISIONING", "BOOTING", "READY"]
    later = client.get(f"/api/v1/test-runs/{run['id']}/environment/events", params={"after_id": events[-1]["id"]}, headers=headers).json()
    assert later == []
    metrics = client.get(f"/api/v1/test-runs/{run['id']}/metrics", headers=headers).json()
    assert {m["measurement_status"] for m in metrics} == {"ACTUAL", "UNAVAILABLE"}
    # Completion requires verified cleanup first.
    assert client.post(f"{base}/complete", json=IDENTITY, headers=RUNNER).status_code == 409
    for s in ("INSTALLING", "APPLYING_CONDITIONS", "VERIFYING_CONDITIONS", "EXECUTING", "COLLECTING_METRICS", "ANALYZING", "COMPLETED", "CLEANING"):
        assert status(s).status_code == 200, s
    assert status("CLEANED", cleanup_verified=True).status_code == 200
    assert client.post(f"{base}/complete", json=IDENTITY, headers=RUNNER).json() == {"status": "completed"}
    final = client.get(f"/api/v1/test-runs/{run['id']}/environment", headers=headers).json()
    assert final["outcome"] == "completed" and final["cleanup_verified"] is True
    assert client.get(f"/api/v1/test-runs/{run['id']}", headers=headers).json()["status"] == "completed"


def test_cancel_requests_worker_cleanup_then_acknowledges(client, auth_headers):
    headers, _ = auth_headers
    online_worker(client)
    run, _, _ = queued_run(client, headers)
    client.post(f"/api/v1/test-runs/{run['id']}/environment/prepare", json={"configuration": entry_level()}, headers=headers)
    client.post(f"/api/v1/test-runs/{run['id']}/execute", headers=headers)
    client.post("/api/v1/runner/jobs/claim", json=EMU, headers=RUNNER)
    base = f"/api/v1/runner/jobs/{run['id']}"
    client.put(f"{base}/environment", json={**IDENTITY, "status": "PREFLIGHT", "message": "x"}, headers=RUNNER)
    canceled = client.post(f"/api/v1/test-runs/{run['id']}/cancel", headers=headers).json()
    assert canceled["status"] != "canceled"     # emulator may be running: the worker must clean up first
    assert client.post(f"{base}/heartbeat", json=IDENTITY, headers=RUNNER).json()["cancel_requested"] is True
    flags = client.put(f"{base}/environment", json={**IDENTITY, "status": "PROVISIONING", "message": "x"}, headers=RUNNER).json()
    assert flags["cancel_requested"] is True and flags["status"] == "CANCEL_REQUESTED"
    for s in ("CLEANING", "CLEANED"):
        assert client.put(f"{base}/environment", json={**IDENTITY, "status": s, "message": s}, headers=RUNNER).status_code == 200
    assert client.post(f"{base}/canceled", json=IDENTITY, headers=RUNNER).json() == {"status": "canceled"}
    assert client.get(f"/api/v1/test-runs/{run['id']}", headers=headers).json()["status"] == "canceled"


def test_cancel_before_claim_is_immediate(client, auth_headers):
    headers, _ = auth_headers
    online_worker(client)
    run, _, _ = queued_run(client, headers)
    client.post(f"/api/v1/test-runs/{run['id']}/environment/prepare", json={"configuration": entry_level()}, headers=headers)
    r = client.post(f"/api/v1/test-runs/{run['id']}/environment/cancel", headers=headers)
    assert r.status_code == 202 and r.json()["result"] == "CANCELED" and r.json()["environment"]["status"] == "CLEANED"


def test_lease_expiry_requeues_environment(client, auth_headers):
    from app.models import RunnerLease
    headers, _ = auth_headers
    online_worker(client)
    run, _, _ = queued_run(client, headers)
    client.post(f"/api/v1/test-runs/{run['id']}/environment/prepare", json={"configuration": entry_level()}, headers=headers)
    client.post(f"/api/v1/test-runs/{run['id']}/execute", headers=headers)
    client.post("/api/v1/runner/jobs/claim", json=EMU, headers=RUNNER)
    client.put(f"/api/v1/runner/jobs/{run['id']}/environment", json={**IDENTITY, "status": "PREFLIGHT", "message": "x"}, headers=RUNNER)
    with client.app.state.testing_session() as db:
        lease = db.query(RunnerLease).filter_by(test_run_id=run["id"]).one()
        lease.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    job = client.post("/api/v1/runner/jobs/claim", json=EMU, headers=RUNNER).json()["job"]
    assert job["run_id"] == run["id"] and job["attempt_count"] == 2
    events = client.get(f"/api/v1/test-runs/{run['id']}/environment/events", headers=headers).json()
    assert any(e["event_type"] == "requeued" for e in events)


def test_non_retryable_failure_fails_run(client, auth_headers):
    headers, _ = auth_headers
    online_worker(client)
    run, _, _ = queued_run(client, headers)
    client.post(f"/api/v1/test-runs/{run['id']}/environment/prepare", json={"configuration": entry_level()}, headers=headers)
    client.post(f"/api/v1/test-runs/{run['id']}/execute", headers=headers)
    client.post("/api/v1/runner/jobs/claim", json=EMU, headers=RUNNER)
    r = client.post(f"/api/v1/runner/jobs/{run['id']}/fail", json={**IDENTITY, "error_message": "android_sdk_not_found", "retryable": False}, headers=RUNNER)
    assert r.json() == {"status": "failed"}
    assert client.get(f"/api/v1/test-runs/{run['id']}", headers=headers).json()["error_message"] == "android_sdk_not_found"


def test_prepare_rejects_unapproved_build_and_wrong_type(client, auth_headers):
    headers, _ = auth_headers
    online_worker(client)
    project = client.post("/api/v1/projects", json={"name": "no-build"}, headers=headers).json()
    run = client.post("/api/v1/test-runs", json={"project_id": project["id"]}, headers=headers).json()
    assert client.post(f"/api/v1/test-runs/{run['id']}/environment/prepare", json={"configuration": entry_level()}, headers=headers).status_code == 409
    run2, _, _ = queued_run(client, headers)
    wrong = client.post(f"/api/v1/test-runs/{run2['id']}/environment/prepare", json={"configuration": entry_level(environment_type="physical_device")}, headers=headers)
    assert wrong.status_code == 422


@pytest.mark.parametrize("bad", [{"device": {"ram_mb": 100}}, {"network": {"packet_loss_pct": 101}}, {"battery": {"saver_enabled": True, "charging": True}},
                                 {"scenario_bindings": {"Bad Name": {"text": "x"}}}, {"device": {"ram_mb": 2048, "unknown": 1}}])
def test_environment_request_validation(client, auth_headers, bad):
    headers, _ = auth_headers
    assert client.post("/api/v1/environment/preflight", json={"configuration": {**entry_level(), **bad}}, headers=headers).status_code == 422
