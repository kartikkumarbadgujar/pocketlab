from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from app.database import get_db
from app.dependencies import require_runner
from app.models import AppBuild, ConditionProfileVersion, Device, Finding, MeasurementDefinition, MetricSample, Project, RunProfile, RunScenario, RunnerAgent, RunnerLease, ScenarioStep, ScenarioVersion, TestRun
from app.schemas.api import BatchMetrics, ClaimInput, FindingInput, HeartbeatInput

router=APIRouter(prefix="/api/v1/runner",tags=["runner"],dependencies=[Depends(require_runner)])
LEASE_SECONDS=90
MAX_ATTEMPTS=3

def _agent(db, name, identity):
    agent=db.scalar(select(RunnerAgent).where(RunnerAgent.agent_name==name,RunnerAgent.authentication_identity==identity))
    if not agent:
        agent=RunnerAgent(agent_name=name,authentication_identity=identity,status="online");db.add(agent);db.flush()
    return agent

def _owned_lease(db,run_id,agent):
    lease=db.scalar(select(RunnerLease).where(RunnerLease.test_run_id==run_id,RunnerLease.runner_agent_id==agent.id))
    if not lease:raise HTTPException(404,"Active lease not found")
    expiry=lease.lease_expires_at.replace(tzinfo=timezone.utc) if lease.lease_expires_at.tzinfo is None else lease.lease_expires_at
    if expiry < datetime.now(timezone.utc):raise HTTPException(409,"Runner lease expired")
    run=db.get(TestRun,run_id)
    if not run or run.status not in ("claimed","running"):raise HTTPException(409,"Run is not active")
    return lease,run

@router.post("/heartbeat")
def agent_heartbeat(data:HeartbeatInput,db:Session=Depends(get_db)):
    agent=_agent(db,data.agent_name,data.authentication_identity);agent.status=data.status;agent.last_seen_at=datetime.now(timezone.utc);db.commit();return {"status":"ok","agent_id":agent.id}

@router.post("/jobs/claim")
def claim_job(data:ClaimInput,db:Session=Depends(get_db)):
    now=datetime.now(timezone.utc);agent=_agent(db,data.agent_name,data.authentication_identity)
    # Expired jobs are retried or moved to failed before taking fresh queued work.
    expired=db.scalars(select(RunnerLease).where(RunnerLease.lease_expires_at<now).with_for_update()).all()
    for lease in expired:
        run=db.get(TestRun,lease.test_run_id)
        if run and run.status in ("claimed","running"):
            if lease.attempt_count>=MAX_ATTEMPTS:run.status="failed";run.error_message="Runner lease expired after maximum attempts";run.completed_at=now
            else:run.status="queued"
        db.delete(lease)
    db.flush()
    approved_build_ids=select(AppBuild.id).where(AppBuild.scan_status=="approved")
    stmt=select(TestRun).where(TestRun.status=="queued",or_(TestRun.app_build_id.is_(None),TestRun.app_build_id.in_(approved_build_ids))).order_by(TestRun.created_at,TestRun.id).limit(1).with_for_update(skip_locked=True)
    run=db.scalar(stmt)
    if not run:
        db.commit();return {"job":None}
    run.retry_count += 1
    run.status="claimed";run.started_at=run.started_at or now
    lease=RunnerLease(test_run_id=run.id,runner_agent_id=agent.id,lease_expires_at=now+timedelta(seconds=LEASE_SECONDS),heartbeat_at=now,attempt_count=run.retry_count)
    db.add(lease);agent.status="busy";agent.last_seen_at=now;db.commit()
    profiles=db.execute(select(RunProfile,ConditionProfileVersion).join(ConditionProfileVersion,RunProfile.profile_version_id==ConditionProfileVersion.id).where(RunProfile.test_run_id==run.id)).all()
    scenarios=db.execute(select(RunScenario,ScenarioVersion).join(ScenarioVersion,RunScenario.scenario_version_id==ScenarioVersion.id).where(RunScenario.test_run_id==run.id)).all()
    return {"job":{"run_id":run.id,"project_id":run.project_id,"app_build_id":run.app_build_id,"execution_mode":run.execution_mode,"attempt_count":lease.attempt_count,"lease_expires_at":lease.lease_expires_at,"profiles":[{"profile_version_id":pv.id,"configuration":pv.configuration_json,"actual_device_id":rp.actual_device_id} for rp,pv in profiles],"scenarios":[{"run_scenario_id":rs.id,"scenario_version_id":sv.id,"steps":[{"step_order":step.step_order,"action_type":step.action_type,"configuration":step.action_configuration_json,"expected":step.expected_result_json,"timeout_seconds":step.timeout_seconds} for step in db.scalars(select(ScenarioStep).where(ScenarioStep.scenario_version_id==sv.id).order_by(ScenarioStep.step_order)).all()]} for rs,sv in scenarios]}}

@router.post("/jobs/{run_id}/heartbeat")
def job_heartbeat(run_id:int,data:ClaimInput,db:Session=Depends(get_db)):
    agent=_agent(db,data.agent_name,data.authentication_identity);lease,run=_owned_lease(db,run_id,agent);now=datetime.now(timezone.utc)
    lease.heartbeat_at=now;lease.lease_expires_at=now+timedelta(seconds=LEASE_SECONDS);run.status="running";db.commit();return {"status":"ok","lease_expires_at":lease.lease_expires_at}

@router.post("/jobs/{run_id}/complete")
def complete(run_id:int,data:ClaimInput,db:Session=Depends(get_db)):
    agent=_agent(db,data.agent_name,data.authentication_identity);lease,run=_owned_lease(db,run_id,agent);now=datetime.now(timezone.utc)
    run.status="completed";run.completed_at=now;db.delete(lease);agent.status="online";agent.last_seen_at=now;db.commit();return {"status":"completed"}

@router.post("/jobs/{run_id}/fail")
def fail(run_id:int,data:ClaimInput,db:Session=Depends(get_db)):
    agent=_agent(db,data.agent_name,data.authentication_identity);lease,run=_owned_lease(db,run_id,agent);now=datetime.now(timezone.utc)
    if lease.attempt_count>=MAX_ATTEMPTS:run.status="failed";run.completed_at=now
    else:run.status="queued"
    run.error_message="Runner reported job failure";db.delete(lease);agent.status="online";db.commit();return {"status":run.status}

@router.post("/jobs/{run_id}/metrics/batch",status_code=202)
def batch_metrics(run_id:int,data:BatchMetrics,db:Session=Depends(get_db)):
    agent=_agent(db,data.agent_name,data.authentication_identity);lease,run=_owned_lease(db,run_id,agent)
    owner_id=db.scalar(select(Project.owner_user_id).where(Project.id==run.project_id))
    rows=[];seen=set()
    try:
        for sample in data.samples:
            if sample.device_id is None:
                raise HTTPException(422,"Every measurement must identify its registered device")
            device=db.scalar(select(Device).where(Device.id==sample.device_id,Device.owner_user_id==owner_id))
            if not device:
                raise HTTPException(422,"Measurement device must be registered to the project owner")
            if sample.run_scenario_id is not None and not db.scalar(select(RunScenario.id).where(RunScenario.id==sample.run_scenario_id,RunScenario.test_run_id==run_id)):
                raise HTTPException(422,"Measurement scenario does not belong to this run")
            if run.execution_mode=="actual_device" and not db.scalar(select(RunProfile.id).where(RunProfile.test_run_id==run_id,RunProfile.actual_device_id==sample.device_id)):
                raise HTTPException(422,"Measurement device does not match the actual-device run")
            definition=db.scalar(select(MeasurementDefinition).where(MeasurementDefinition.parameter_key==sample.parameter_key))
            if not definition:
                raise HTTPException(422,f"Unknown measurement parameter: {sample.parameter_key}")
            if sample.source not in definition.supported_sources:
                raise HTTPException(422,f"Unsupported measurement source for {sample.parameter_key}")
            if sample.validity_status=="valid":
                typed={"number":sample.numeric_value is not None,"boolean":sample.boolean_value is not None,"text":sample.text_value is not None}
                if not typed.get(definition.data_type,False):raise HTTPException(422,f"Value type does not match {sample.parameter_key}")
                if sample.numeric_value is not None and ((definition.minimum_value is not None and sample.numeric_value<definition.minimum_value) or (definition.maximum_value is not None and sample.numeric_value>definition.maximum_value)):
                    raise HTTPException(422,f"Measurement value outside definition range for {sample.parameter_key}")
            if sample.sample_key in seen:continue
            seen.add(sample.sample_key)
            payload=sample.model_dump(exclude={"metadata"});payload["metadata_json"]=sample.metadata
            rows.append({"test_run_id":run_id,**payload})
        dialect=db.get_bind().dialect.name
        if dialect=="postgresql":stmt=pg_insert(MetricSample).values(rows).on_conflict_do_nothing(index_elements=["test_run_id","sample_key"])
        elif dialect=="sqlite":stmt=sqlite_insert(MetricSample).values(rows).on_conflict_do_nothing(index_elements=["test_run_id","sample_key"])
        else:
            stmt=None
            for values in rows:
                if db.scalar(select(MetricSample.id).where(MetricSample.test_run_id==run_id,MetricSample.sample_key==values["sample_key"])) is None:db.add(MetricSample(**values))
        inserted=db.execute(stmt).rowcount if stmt is not None and rows else 0
        duplicates=len(data.samples)-inserted
        lease.heartbeat_at=datetime.now(timezone.utc);lease.lease_expires_at=lease.heartbeat_at+timedelta(seconds=LEASE_SECONDS);run.status="running";db.commit()
    except Exception:
        db.rollback();raise
    return {"inserted":inserted,"duplicates":duplicates}

@router.post("/jobs/{run_id}/findings",status_code=201)
def add_finding(run_id:int,data:FindingInput,db:Session=Depends(get_db)):
    agent=_agent(db,data.agent_name,data.authentication_identity);_owned_lease(db,run_id,agent)
    if data.run_scenario_id is not None and not db.scalar(select(RunScenario.id).where(RunScenario.id==data.run_scenario_id,RunScenario.test_run_id==run_id)):
        raise HTTPException(422,"Finding scenario does not belong to this run")
    finding=Finding(test_run_id=run_id,run_scenario_id=data.run_scenario_id,finding_type=data.finding_type,title=data.title,description=data.description,severity=data.severity,confidence=data.confidence,expected_behavior=data.expected_behavior,actual_behavior=data.actual_behavior,reproduction_steps_json=data.reproduction_steps,evidence_reference=data.evidence_reference)
    db.add(finding);db.commit();db.refresh(finding);return {"id":finding.id,"title":finding.title}
