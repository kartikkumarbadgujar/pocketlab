from datetime import datetime, timezone
import io, zipfile
import pytest
from app.schemas.api import MeasurementInput, ProfileInput
from app.services.impact import estimate_impact
from app.collectors.measurements import unavailable


def test_registration_login_and_refresh_rotation(client):
    assert client.post("/api/v1/auth/register",json={"email":"user@example.com","password":"correct horse battery staple"}).status_code==201
    token=client.post("/api/v1/auth/login",json={"email":"user@example.com","password":"correct horse battery staple"}).json()
    assert token["access_token"] and token["refresh_token"]
    rotated=client.post("/api/v1/auth/refresh",json={"refresh_token":token["refresh_token"]})
    assert rotated.status_code==200 and rotated.json()["refresh_token"]!=token["refresh_token"]
    assert client.post("/api/v1/auth/refresh",json={"refresh_token":token["refresh_token"]}).status_code==401


def test_unauthorized_and_project_ownership(client,auth_headers):
    headers,_=auth_headers
    assert client.get("/api/v1/projects").status_code==401
    project=client.post("/api/v1/projects",json={"name":"Pocket"},headers=headers).json()
    client.post("/api/v1/auth/register",json={"email":"b@example.com","password":"correct horse battery staple"})
    b=client.post("/api/v1/auth/login",json={"email":"b@example.com","password":"correct horse battery staple"}).json()
    assert client.get(f"/api/v1/projects/{project['id']}",headers={"Authorization":f"Bearer {b['access_token']}"}).status_code==404


def test_sql_injection_is_treated_as_data(client,auth_headers):
    headers,_=auth_headers
    resp=client.post("/api/v1/projects",json={"name":"x'); DROP TABLE users;--"},headers=headers)
    assert resp.status_code==201
    assert client.get("/api/v1/projects",headers=headers).status_code==200


@pytest.mark.parametrize("value",[-1,100.01,float("nan")])
def test_invalid_packet_loss_rejected(value):
    with pytest.raises(ValueError):ProfileInput(name="bad",configuration={"network.packet_loss_pct":value})
    with pytest.raises(ValueError):MeasurementInput(parameter_key="network.packet_loss_pct",numeric_value=value,source="runner",sample_key="a",observed_at=datetime.now(timezone.utc))


@pytest.mark.parametrize("value",[-1,7,1.2])
def test_invalid_thermal_status_rejected(value):
    with pytest.raises(ValueError):MeasurementInput(parameter_key="thermal.status",numeric_value=value,source="android_api",sample_key="a",observed_at=datetime.now(timezone.utc))


def test_measurements_require_utc_timestamp_and_trusted_source():
    with pytest.raises(ValueError):MeasurementInput(parameter_key="thermal.status",numeric_value=2,source="runner",sample_key="t",observed_at=datetime.now())
    with pytest.raises(ValueError):MeasurementInput(parameter_key="network.packet_loss_pct",numeric_value=1,source="manual",sample_key="loss",observed_at=datetime.now(timezone.utc))
    with pytest.raises(ValueError):MeasurementInput(parameter_key="cpu.temperature_c",source="android_api",sample_key="empty",observed_at=datetime.now(timezone.utc))


def test_invalid_device_profile_and_unsupported_measurement(client,auth_headers):
    headers,_=auth_headers
    assert client.post("/api/v1/devices/register",json={"device_name":"bad","cpu_core_count":0},headers=headers).status_code==422
    assert client.post("/api/v1/profiles",json={"name":"bad","configuration":{"network.packet_loss_pct":-1}},headers=headers).status_code==422
    m=unavailable("thermal.headroom","android_api")
    assert m["validity_status"]=="unsupported" and m["numeric_value"] is None
    assert client.post("/api/v1/test-runs",json={"project_id":1,"execution_mode":"actual_device"},headers=headers).status_code==422


def test_seed_catalog_is_complete_and_idempotent(client):
    from app.seed import seed_database
    from app.models import MeasurementDefinition, TestScenario, ConditionProfile
    from sqlalchemy import select
    factory=client.app.state.testing_session
    with factory() as db:
        seed_database(db);seed_database(db)
        assert len(db.scalars(select(ConditionProfile)).all())==3
        # 12 original scenarios + "Cart-state preservation" and "Application restart" added for the Android worker.
        assert len(db.scalars(select(TestScenario)).all())==14
        assert db.scalar(select(MeasurementDefinition).where(MeasurementDefinition.parameter_key=="thermal.status")).maximum_value==6
        assert db.scalar(select(MeasurementDefinition).where(MeasurementDefinition.parameter_key=="network.packet_loss_pct")).is_observation_only is False


def test_duplicate_run_creation_is_idempotent(client,auth_headers):
    headers,_=auth_headers;project=client.post("/api/v1/projects",json={"name":"app"},headers=headers).json()
    headers={**headers,"Idempotency-Key":"key-1"};body={"project_id":project["id"],"scenario_version_ids":[]}
    a=client.post("/api/v1/test-runs",json=body,headers=headers);b=client.post("/api/v1/test-runs",json=body,headers=headers)
    assert a.status_code==201 and b.status_code==201 and a.json()["id"]==b.json()["id"]
    assert client.post("/api/v1/test-runs",json={**body,"requested_repetitions":2},headers=headers).status_code==409


def test_duplicate_metric_upload_prevented(client,auth_headers):
    headers,_=auth_headers;project=client.post("/api/v1/projects",json={"name":"app"},headers=headers).json()
    run=client.post("/api/v1/test-runs",json={"project_id":project["id"]},headers=headers).json()
    device=client.post("/api/v1/devices/register",json={"device_name":"lab emulator"},headers=headers).json()
    runner_headers={"Authorization":"Bearer test-runner-token"};identity={"agent_name":"runner-a","authentication_identity":"runner-key-a"}
    claim=client.post("/api/v1/runner/jobs/claim",json=identity,headers=runner_headers).json()["job"]
    assert claim["run_id"]==run["id"]
    body={"samples":[{"parameter_key":"memory.available_mb","numeric_value":250,"unit":"MB","source":"android_api","sample_key":"sample-1","device_id":device["id"],"observed_at":datetime.now(timezone.utc).isoformat()}],**identity}
    a=client.post(f"/api/v1/runner/jobs/{run['id']}/metrics/batch",json=body,headers=runner_headers).json()
    b=client.post(f"/api/v1/runner/jobs/{run['id']}/metrics/batch",json=body,headers=runner_headers).json()
    assert a=={"inserted":1,"duplicates":0} and b=={"inserted":0,"duplicates":1}


def test_lease_retry_and_job_states(client,auth_headers):
    headers,_=auth_headers;project=client.post("/api/v1/projects",json={"name":"app"},headers=headers).json()
    run=client.post("/api/v1/test-runs",json={"project_id":project["id"]},headers=headers).json();rh={"Authorization":"Bearer test-runner-token"}
    identity={"agent_name":"runner","authentication_identity":"runner-id"}
    claim=client.post("/api/v1/runner/jobs/claim",json=identity,headers=rh).json()["job"]
    assert claim["attempt_count"]==1
    failed=client.post(f"/api/v1/runner/jobs/{run['id']}/fail",json=identity,headers=rh)
    assert failed.json()["status"]=="queued"
    claim2=client.post("/api/v1/runner/jobs/claim",json=identity,headers=rh).json()["job"]
    assert claim2["attempt_count"]==2


def test_expired_job_lease_is_recovered(client,auth_headers):
    from datetime import timedelta
    from app.models import RunnerLease
    headers,_=auth_headers;project=client.post("/api/v1/projects",json={"name":"expiry"},headers=headers).json()
    run=client.post("/api/v1/test-runs",json={"project_id":project["id"]},headers=headers).json();rh={"Authorization":"Bearer test-runner-token"}
    identity={"agent_name":"expiry-runner","authentication_identity":"expiry-key"}
    client.post("/api/v1/runner/jobs/claim",json=identity,headers=rh)
    with client.app.state.testing_session() as db:
        lease=db.query(RunnerLease).filter_by(test_run_id=run["id"]).one();lease.lease_expires_at=datetime.now(timezone.utc)-timedelta(seconds=1);db.commit()
    next_job=client.post("/api/v1/runner/jobs/claim",json=identity,headers=rh).json()["job"]
    assert next_job["run_id"]==run["id"] and next_job["attempt_count"]==2


def test_apk_validation_rejects_non_zip(client,auth_headers):
    headers,_=auth_headers;project=client.post("/api/v1/projects",json={"name":"apk"},headers=headers).json()
    response=client.post(f"/api/v1/projects/{project['id']}/builds",data={"package_name":"com.example.app"},files={"file":("bad.apk",b"not apk","application/vnd.android.package-archive")},headers=headers)
    assert response.status_code==400


def test_valid_apk_is_hashed_and_quarantined(client,auth_headers):
    headers,_=auth_headers;project=client.post("/api/v1/projects",json={"name":"apk-valid"},headers=headers).json()
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,"w") as archive:archive.writestr("AndroidManifest.xml",b"placeholder manifest")
    response=client.post(f"/api/v1/projects/{project['id']}/builds",data={"package_name":"com.example.app"},files={"file":("app.apk",stream.getvalue(),"application/vnd.android.package-archive")},headers=headers)
    assert response.status_code==201
    assert response.json()["scan_status"]=="quarantined" and len(response.json()["apk_sha256"])==64
    queued=client.post("/api/v1/test-runs",json={"project_id":project["id"],"app_build_id":response.json()["id"]},headers=headers)
    assert queued.status_code==409


def test_impact_estimation_formula_and_uncertainty():
    result=estimate_impact(10000,.2,10,100)
    assert result["estimated_affected_users"]==200
    assert result["uncertainty"]["failure_rate_95ci"][0] < .1 < result["uncertainty"]["failure_rate_95ci"][1]


def test_transaction_rollback_on_failed_database_constraint(client,auth_headers):
    from sqlalchemy import select, func
    from sqlalchemy.exc import IntegrityError
    from app.models import Project, User
    headers,_=auth_headers
    assert client.post("/api/v1/projects",json={"name":"same"},headers=headers).status_code==201
    assert client.post("/api/v1/projects",json={"name":"same"},headers=headers).status_code==409
    with client.app.state.testing_session() as db:
        user=db.scalar(select(User).where(User.email=="a@example.com"))
        db.add(Project(owner_user_id=user.id,name="same"))
        with pytest.raises(IntegrityError):db.commit()
        db.rollback()
        assert db.scalar(select(func.count(Project.id)).where(Project.owner_user_id==user.id))==1
