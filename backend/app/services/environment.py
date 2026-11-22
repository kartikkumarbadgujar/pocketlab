"""Database-side logic for execution environments (no subprocesses; the API never runs adb or the emulator)."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.emulator.planner import plan_environment
from app.emulator.schemas import CapabilityReport, EnvironmentRequest, PreflightResult
from app.models import ConditionApplication, Device, EnvironmentEvent, ExecutionEnvironment, RunScenario, RunnerAgent, ScenarioVersion, TestScenario

RUNNER_ONLINE_WINDOW = timedelta(seconds=180)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def online_capability_reports(db: Session) -> list[tuple[RunnerAgent, CapabilityReport]]:
    cutoff = datetime.now(timezone.utc) - RUNNER_ONLINE_WINDOW
    rows = db.scalars(select(RunnerAgent).where(RunnerAgent.capability_report_json.is_not(None)).order_by(RunnerAgent.capability_reported_at.desc())).all()
    out = []
    for agent in rows:
        seen = _aware(agent.last_seen_at)
        if seen and seen >= cutoff:
            try:
                out.append((agent, CapabilityReport.model_validate(agent.capability_report_json)))
            except ValueError:
                continue
    return out


def preflight(db: Session, user_id: int, request: EnvironmentRequest, actual_device_id: int | None) -> PreflightResult:
    reports = online_capability_reports(db)
    report = reports[0][1] if reports else None
    if request.environment_type == "physical_device" and report is not None:
        # Prefer a worker that has this specific device attached.
        report = next((r for _, r in reports if any(d["device_id"] == actual_device_id for d in r.physical_devices)), report)
    result = plan_environment(request, report)
    if request.environment_type == "physical_device":
        if actual_device_id is None:
            result.blocking_errors.append("Select the registered physical device to test on.")
        elif not db.scalar(select(Device.id).where(Device.id == actual_device_id, Device.owner_user_id == user_id)):
            result.blocking_errors.append("The selected device is not registered to you.")
        elif report is not None and not any(d["device_id"] == actual_device_id and d["state"] == "device" for d in report.physical_devices):
            result.blocking_errors.append("The selected device is not attached and authorized on any online worker.")
        result.ok = not result.blocking_errors
    return result


def add_event(db: Session, env: ExecutionEnvironment, event_type: str, message: str, *, severity: str = "info", status: str | None = None,
              data: dict | None = None, at: datetime | None = None) -> None:
    db.add(EnvironmentEvent(environment_id=env.id, event_type=event_type, message=message[:4000], severity=severity, status=status,
                            data_json=data or {}, attempt=max(env.attempt, 1), event_timestamp=at or datetime.now(timezone.utc)))


def environment_for_run(db: Session, run_id: int) -> ExecutionEnvironment | None:
    return db.scalar(select(ExecutionEnvironment).where(ExecutionEnvironment.test_run_id == run_id))


def requeue_after_lease_loss(db: Session, env: ExecutionEnvironment | None, *, final: bool, canceled: bool) -> None:
    """Called when a worker lease expires or a worker reports a retryable failure."""
    if env is None:
        return
    now = datetime.now(timezone.utc)
    if canceled:
        add_event(db, env, "worker_lost", "Worker stopped responding after cancellation; its orphan sweep removes leftover resources on restart.",
                  severity="warning")
        env.outcome = "canceled"
        env.stopped_at = now
    elif final:
        if env.status not in ("CLEANED",):
            env.status = "FAILED"
        env.outcome = "failed"
        env.stopped_at = now
        add_event(db, env, "attempts_exhausted", "Run failed after the maximum number of worker attempts.", severity="error", status=env.status)
    else:
        env.status = "QUEUED"
        env.outcome = None
        env.effective_configuration_json = None
        env.active_run_scenario_id = None
        env.cleanup_verified = None
        add_event(db, env, "requeued", "Run returned to the queue for another worker attempt.", severity="warning", status="QUEUED")


def environment_payload(db: Session, env: ExecutionEnvironment) -> dict:
    conditions = db.scalars(select(ConditionApplication).where(ConditionApplication.environment_id == env.id).order_by(ConditionApplication.id)).all()
    scenarios = db.execute(
        select(RunScenario, ScenarioVersion, TestScenario).join(ScenarioVersion, RunScenario.scenario_version_id == ScenarioVersion.id)
        .join(TestScenario, ScenarioVersion.scenario_id == TestScenario.id).where(RunScenario.test_run_id == env.test_run_id).order_by(RunScenario.id)).all()
    return {
        "id": env.id, "test_run_id": env.test_run_id, "environment_type": env.environment_type, "status": env.status, "status_message": env.status_message,
        "outcome": env.outcome, "attempt": env.attempt, "emulator_name": env.emulator_name, "emulator_serial": env.emulator_serial,
        "android_api_level": env.android_api_level, "system_image": env.system_image, "device_id": env.device_id,
        "requested_configuration": env.requested_configuration_json, "preflight": env.preflight_json,
        "effective_configuration": env.effective_configuration_json, "capability_report": env.capability_report_json,
        "active_run_scenario_id": env.active_run_scenario_id, "cancel_requested_at": env.cancel_requested_at,
        "cleanup_requested_at": env.cleanup_requested_at, "cleanup_verified": env.cleanup_verified,
        "created_at": env.created_at, "started_at": env.started_at, "stopped_at": env.stopped_at, "updated_at": env.updated_at,
        "conditions": [{"id": c.id, "parameter_key": c.parameter_key, "requested_value": c.requested_value, "applied_value": c.applied_value,
                        "observed_value": c.observed_value, "unit": c.unit, "application_status": c.application_status, "mechanism": c.mechanism,
                        "evidence": c.evidence_json, "attempt": c.attempt, "applied_at": c.applied_at, "verified_at": c.verified_at} for c in conditions],
        "scenarios": [{"run_scenario_id": rs.id, "scenario_version_id": sv.id, "version": sv.version, "name": ts.name, "category": ts.scenario_category,
                       "status": rs.status, "started_at": rs.started_at, "completed_at": rs.completed_at, "result": rs.result_json} for rs, sv, ts in scenarios],
    }
