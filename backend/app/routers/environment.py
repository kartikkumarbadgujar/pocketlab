"""User-facing execution-environment endpoints.

Nothing here executes Android tooling. Requests only record intent (prepare/execute/cancel/cleanup) and
return 202; the separately authenticated execution worker performs the work and reports back.
"""
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.dependencies import current_user
from app.models import AppBuild, AuditLog, EnvironmentEvent, EvidenceArtifact, ExecutionEnvironment, Project, RunnerLease, RunProfile, TestRun, User
from app.schemas.environment import PreflightInput
from app.services import environment as env_service

router = APIRouter(tags=["execution environment"])
ARTIFACT_INLINE_TYPES = {"image/png", "text/plain", "application/json"}


def _owned_run(db: Session, user: User, run_id: int) -> TestRun:
    run = db.scalar(select(TestRun).join(Project, TestRun.project_id == Project.id).where(TestRun.id == run_id, Project.owner_user_id == user.id))
    if not run:
        raise HTTPException(404, "Test run not found")
    return run


def _env_or_404(db: Session, run_id: int) -> ExecutionEnvironment:
    env = env_service.environment_for_run(db, run_id)
    if not env:
        raise HTTPException(404, "This run has no execution environment")
    return env


@router.get("/api/v1/environment/capabilities")
def capabilities(db: Session = Depends(get_db), user: User = Depends(current_user)):
    reports = env_service.online_capability_reports(db)
    if not reports:
        return {"status": "no_worker_online", "workers": [],
                "message": "No Android execution worker has reported in the last 3 minutes. Start it with "
                           "'python -m app.workers.emulator_runner' on a machine with the Android SDK and hardware acceleration."}
    return {"status": "available", "workers": [
        # The configurator needs image API levels and coarse capability statuses only. Keep
        # host resources, worker identity, physical-device presence and raw tool diagnostics private.
        {"reported_at": report.generated_at, "summary": report.summary(),
         "system_images": [i.model_dump() for i in report.system_images]}
        for agent, report in reports]}


@router.post("/api/v1/environment/preflight")
def preflight(data: PreflightInput, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return env_service.preflight(db, user.id, data.configuration, data.actual_device_id)


@router.post("/api/v1/test-runs/{run_id}/environment/prepare", status_code=202)
def prepare(run_id: int, data: PreflightInput, db: Session = Depends(get_db), user: User = Depends(current_user)):
    run = _owned_run(db, user, run_id)
    if run.status != "queued" or db.scalar(select(RunnerLease.id).where(RunnerLease.test_run_id == run.id)):
        raise HTTPException(409, "Environment can only be prepared before a worker claims the run")
    build = db.get(AppBuild, run.app_build_id) if run.app_build_id else None
    if not build or build.scan_status != "approved":
        raise HTTPException(409, "An approved APK build is required for Android execution")
    if run.requested_repetitions != 1:
        raise HTTPException(422, "Android environment runs execute each scenario once; queue separate runs to repeat (each gets a fresh device).")
    request = data.configuration
    expected_type = "physical_device" if run.execution_mode == "actual_device" else "emulator"
    if request.environment_type != expected_type:
        raise HTTPException(422, f"Run execution mode '{run.execution_mode}' requires environment_type '{expected_type}'")
    device_id = data.actual_device_id
    if expected_type == "physical_device":
        bound = db.scalar(select(RunProfile.actual_device_id).where(RunProfile.test_run_id == run.id, RunProfile.actual_device_id.is_not(None)))
        if device_id is not None and bound is not None and device_id != bound:
            raise HTTPException(422, "Device does not match the device bound to this run")
        device_id = device_id or bound
    plan = env_service.preflight(db, user.id, request, device_id)
    if not plan.ok:
        return JSONResponse(status_code=422, content={"detail": "Preflight failed; the run was not prepared.", "preflight": plan.model_dump(mode="json")})
    env = env_service.environment_for_run(db, run.id)
    if env and env.status != "PREPARED":
        raise HTTPException(409, f"Environment is already {env.status}")
    if not env:
        env = ExecutionEnvironment(test_run_id=run.id, environment_type=expected_type, status="PREPARED", requested_configuration_json={})
        db.add(env)
        db.flush()
    env.requested_configuration_json = request.model_dump(mode="json")
    env.preflight_json = plan.model_dump(mode="json")
    env.capability_report_json = plan.capability_summary
    env_service.add_event(db, env, "prepared", "Configuration recorded and preflight passed; awaiting execute.", status="PREPARED",
                          data={"warnings": plan.warnings})
    db.add(AuditLog(actor_user_id=user.id, action="environment.prepare", resource_type="test_run", resource_id=str(run.id), outcome="prepared"))
    db.commit()
    return env_service.environment_payload(db, env)


@router.post("/api/v1/test-runs/{run_id}/execute", status_code=202)
def execute(run_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    run = _owned_run(db, user, run_id)
    env = _env_or_404(db, run.id)
    if env.status == "QUEUED":
        return env_service.environment_payload(db, env)   # idempotent
    if env.status != "PREPARED" or run.status != "queued":
        raise HTTPException(409, f"Run cannot be executed from environment state {env.status}")
    from app.emulator.schemas import EnvironmentRequest
    device_id = db.scalar(select(RunProfile.actual_device_id).where(RunProfile.test_run_id == run.id, RunProfile.actual_device_id.is_not(None)))
    plan = env_service.preflight(db, user.id, EnvironmentRequest.model_validate(env.requested_configuration_json), device_id)
    if not plan.ok:
        env.preflight_json = plan.model_dump(mode="json")
        env_service.add_event(db, env, "preflight_failed", "Capabilities changed since preparation: " + "; ".join(plan.blocking_errors), severity="error")
        db.commit()
        return JSONResponse(status_code=422, content={"detail": "Preflight failed at execute time; the run was not queued.", "preflight": plan.model_dump(mode="json")})
    env.status = "QUEUED"
    env.preflight_json = plan.model_dump(mode="json")
    env_service.add_event(db, env, "queued", "Execution requested; waiting for an Android worker to claim the run.", status="QUEUED")
    db.add(AuditLog(actor_user_id=user.id, action="environment.execute", resource_type="test_run", resource_id=str(run.id), outcome="queued"))
    db.commit()
    return env_service.environment_payload(db, env)


@router.get("/api/v1/test-runs/{run_id}/environment")
def get_environment(run_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    run = _owned_run(db, user, run_id)
    return env_service.environment_payload(db, _env_or_404(db, run.id))


@router.get("/api/v1/test-runs/{run_id}/environment/events")
def events(run_id: int, after_id: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=1000), db: Session = Depends(get_db), user: User = Depends(current_user)):
    run = _owned_run(db, user, run_id)
    env = _env_or_404(db, run.id)
    rows = db.scalars(select(EnvironmentEvent).where(EnvironmentEvent.environment_id == env.id, EnvironmentEvent.id > after_id)
                      .order_by(EnvironmentEvent.id).limit(limit)).all()
    return [{"id": e.id, "event_type": e.event_type, "status": e.status, "message": e.message, "severity": e.severity, "data": e.data_json,
             "attempt": e.attempt, "event_timestamp": e.event_timestamp} for e in rows]


def request_cancel(db: Session, run: TestRun, env: ExecutionEnvironment | None, user: User) -> str:
    """Cancel immediately if nothing is provisioned; otherwise ask the worker to stop and clean up."""
    now = datetime.now(timezone.utc)
    leased = db.scalar(select(RunnerLease.id).where(RunnerLease.test_run_id == run.id)) is not None
    if env is not None and leased:
        if env.status in ("CLEANING", "CLEANED", "CANCEL_REQUESTED"):
            return env.status
        env.status, env.cancel_requested_at = "CANCEL_REQUESTED", now
        env_service.add_event(db, env, "cancel_requested", "Cancellation requested; the worker will stop and clean up.", severity="warning", status="CANCEL_REQUESTED")
        db.add(AuditLog(actor_user_id=user.id, action="test_run.cancel_request", resource_type="test_run", resource_id=str(run.id), outcome="requested"))
        return "CANCEL_REQUESTED"
    run.status, run.completed_at = "canceled", now
    if env is not None:
        env.status, env.outcome, env.cancel_requested_at, env.stopped_at, env.cleanup_verified = "CLEANED", "canceled", now, now, True
        env_service.add_event(db, env, "canceled", "Canceled before any worker provisioned resources; nothing to clean up.", status="CLEANED")
    db.add(AuditLog(actor_user_id=user.id, action="test_run.cancel", resource_type="test_run", resource_id=str(run.id), outcome="canceled"))
    return "CANCELED"


@router.post("/api/v1/test-runs/{run_id}/environment/cancel", status_code=202)
def cancel_environment(run_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    run = _owned_run(db, user, run_id)
    env = _env_or_404(db, run.id)
    if run.status in ("completed", "failed", "canceled"):
        raise HTTPException(409, "Run is already terminal")
    result = request_cancel(db, run, env, user)
    db.commit()
    return {"result": result, "environment": env_service.environment_payload(db, env)}


@router.post("/api/v1/test-runs/{run_id}/environment/cleanup", status_code=202)
def cleanup_environment(run_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    run = _owned_run(db, user, run_id)
    env = _env_or_404(db, run.id)
    if env.status == "CLEANED":
        return {"result": "already_cleaned", "cleanup_verified": env.cleanup_verified}
    env.cleanup_requested_at = datetime.now(timezone.utc)
    leased = db.scalar(select(RunnerLease.id).where(RunnerLease.test_run_id == run.id)) is not None
    if env.status in ("PREPARED", "QUEUED") and not leased:
        env.status, env.cleanup_verified, env.stopped_at = "CLEANED", True, env.cleanup_requested_at
        env_service.add_event(db, env, "cleanup", "No resources were provisioned; environment marked clean.", status="CLEANED")
        result = "cleaned"
    else:
        env_service.add_event(db, env, "cleanup_requested", "Cleanup requested; the owning worker cleans up when its current step ends "
                              "(a restarted worker sweeps orphaned PocketLab AVDs and emulators).", severity="warning")
        result = "requested"
    db.commit()
    return {"result": result, "environment": env_service.environment_payload(db, env)}


@router.get("/api/v1/test-runs/{run_id}/artifacts/{artifact_id}/content")
def artifact_content(run_id: int, artifact_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    run = _owned_run(db, user, run_id)
    artifact = db.scalar(select(EvidenceArtifact).where(EvidenceArtifact.id == artifact_id, EvidenceArtifact.test_run_id == run.id))
    if not artifact:
        raise HTTPException(404, "Artifact not found")
    root = Path(settings.upload_dir).resolve()
    path = (root / artifact.storage_key).resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(404, "Artifact file is not available")
    media = artifact.content_type if artifact.content_type in ARTIFACT_INLINE_TYPES else "application/octet-stream"
    return FileResponse(path, media_type=media, filename=artifact.filename,
                        headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'", "Cache-Control": "private, max-age=300"})
