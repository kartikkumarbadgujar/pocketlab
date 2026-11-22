from datetime import datetime, timedelta, timezone
from pathlib import Path
import hashlib
import re
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.config import settings
from app.database import get_db
from app.dependencies import require_runner
from app.emulator.schemas import CapabilityReport, transition_allowed
from app.models import (
    AppBuild, ConditionApplication, ConditionProfileVersion, Device, EvidenceArtifact, ExecutionEnvironment, Finding, MeasurementDefinition,
    MetricSample, Project, RunProfile, RunScenario, RunnerAgent, RunnerLease, ScenarioStep, ScenarioVersion, TestRun, TestScenario,
)
from app.schemas.api import BatchMetrics, ClaimInput, FindingInput, HeartbeatInput
from app.schemas.environment import ConditionBatch, DeviceRegistration, EnvironmentUpdate, EventBatch, FailInput, RunnerIdentity, ScenarioResultInput
from app.services import environment as env_service

router=APIRouter(prefix="/api/v1/runner",tags=["runner"],dependencies=[Depends(require_runner)])
LEASE_SECONDS=90
MAX_ATTEMPTS=3
MAX_ARTIFACT_BYTES=50*1024*1024
ARTIFACT_TYPES={"screenshot":{"image/png"},"logcat":{"text/plain"},"emulator_log":{"text/plain"},"perfetto_trace":{"application/octet-stream"},
                "environment_report":{"application/json"}}

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

def _extend(lease):
    lease.heartbeat_at=datetime.now(timezone.utc);lease.lease_expires_at=lease.heartbeat_at+timedelta(seconds=LEASE_SECONDS)

def _leased_env(db,run_id,identity:RunnerIdentity):
    agent=_agent(db,identity.agent_name,identity.authentication_identity);lease,run=_owned_lease(db,run_id,agent)
    env=env_service.environment_for_run(db,run_id)
    if not env:raise HTTPException(404,"Run has no execution environment")
    return lease,run,env

def _flags(env):
    return {"cancel_requested":env.cancel_requested_at is not None,"cleanup_requested":env.cleanup_requested_at is not None,"status":env.status}

@router.post("/heartbeat")
def agent_heartbeat(data:HeartbeatInput,db:Session=Depends(get_db)):
    agent=_agent(db,data.agent_name,data.authentication_identity);agent.status=data.status;agent.last_seen_at=datetime.now(timezone.utc)
    if data.capability_report is not None:
        try:report=CapabilityReport.model_validate(data.capability_report)
        except ValueError as exc:raise HTTPException(422,f"Invalid capability report: {exc}")
        agent.capability_report_json=report.model_dump(mode="json");agent.capability_reported_at=agent.last_seen_at
    db.commit();return {"status":"ok","agent_id":agent.id}

@router.post("/jobs/claim")
def claim_job(data:ClaimInput,db:Session=Depends(get_db)):
    now=datetime.now(timezone.utc);agent=_agent(db,data.agent_name,data.authentication_identity)
    # Expired jobs are retried or moved to failed before taking fresh queued work.
    expired=db.scalars(select(RunnerLease).where(RunnerLease.lease_expires_at<now).with_for_update()).all()
    for lease in expired:
        run=db.get(TestRun,lease.test_run_id)
        if run and run.status in ("claimed","running"):
            env=env_service.environment_for_run(db,run.id)
            canceled=env is not None and env.cancel_requested_at is not None
            final=lease.attempt_count>=MAX_ATTEMPTS
            if canceled:run.status="canceled";run.completed_at=now
            elif final:run.status="failed";run.error_message="Runner lease expired after maximum attempts";run.completed_at=now
            else:run.status="queued"
            env_service.requeue_after_lease_loss(db,env,final=final,canceled=canceled)
        db.delete(lease)
    db.flush()
    approved_build_ids=select(AppBuild.id).where(AppBuild.scan_status=="approved")
    stmt=select(TestRun).where(TestRun.status=="queued",or_(TestRun.app_build_id.is_(None),TestRun.app_build_id.in_(approved_build_ids)))
    has_env=exists().where(ExecutionEnvironment.test_run_id==TestRun.id)
    if data.environment_types is None:
        stmt=stmt.where(~has_env)   # legacy runners never receive Android-environment runs
    else:
        stmt=stmt.join(ExecutionEnvironment,ExecutionEnvironment.test_run_id==TestRun.id).where(ExecutionEnvironment.status=="QUEUED",ExecutionEnvironment.environment_type.in_(data.environment_types))
        if "physical_device" in data.environment_types:
            served=exists().where(RunProfile.test_run_id==TestRun.id,RunProfile.actual_device_id.in_(data.physical_device_ids or [-1]))
            stmt=stmt.where(or_(ExecutionEnvironment.environment_type!="physical_device",served))
    run=db.scalar(stmt.order_by(TestRun.created_at,TestRun.id).limit(1).with_for_update(skip_locked=True))
    if not run:
        db.commit();return {"job":None}
    run.retry_count += 1
    run.status="claimed";run.started_at=run.started_at or now
    lease=RunnerLease(test_run_id=run.id,runner_agent_id=agent.id,lease_expires_at=now+timedelta(seconds=LEASE_SECONDS),heartbeat_at=now,attempt_count=run.retry_count)
    db.add(lease);agent.status="busy";agent.last_seen_at=now
    env=env_service.environment_for_run(db,run.id)
    if env:
        env.attempt=run.retry_count;env.started_at=env.started_at or now
        env_service.add_event(db,env,"claimed",f"Claimed by worker '{agent.agent_name}' (attempt {run.retry_count})",status=env.status)
    db.commit()
    profiles=db.execute(select(RunProfile,ConditionProfileVersion).join(ConditionProfileVersion,RunProfile.profile_version_id==ConditionProfileVersion.id).where(RunProfile.test_run_id==run.id)).all()
    scenarios=db.execute(select(RunScenario,ScenarioVersion,TestScenario).join(ScenarioVersion,RunScenario.scenario_version_id==ScenarioVersion.id).join(TestScenario,ScenarioVersion.scenario_id==TestScenario.id).where(RunScenario.test_run_id==run.id).order_by(RunScenario.id)).all()
    job={"run_id":run.id,"project_id":run.project_id,"app_build_id":run.app_build_id,"execution_mode":run.execution_mode,"attempt_count":lease.attempt_count,"lease_expires_at":lease.lease_expires_at,
         "profiles":[{"profile_version_id":pv.id,"configuration":pv.configuration_json,"actual_device_id":rp.actual_device_id} for rp,pv in profiles],
         "scenarios":[{"run_scenario_id":rs.id,"scenario_version_id":sv.id,"name":ts.name,"version":sv.version,"steps":[{"step_order":step.step_order,"action_type":step.action_type,"configuration":step.action_configuration_json,"expected":step.expected_result_json,"timeout_seconds":step.timeout_seconds} for step in db.scalars(select(ScenarioStep).where(ScenarioStep.scenario_version_id==sv.id).order_by(ScenarioStep.step_order)).all()]} for rs,sv,ts in scenarios]}
    if env:
        build=db.get(AppBuild,run.app_build_id) if run.app_build_id else None
        job["environment"]={"id":env.id,"environment_type":env.environment_type,"requested_configuration":env.requested_configuration_json}
        job["build"]={"package_name":build.package_name,"apk_sha256":build.apk_sha256,"version_name":build.version_name,"version_code":build.version_code} if build else None
    return {"job":job}

@router.post("/jobs/{run_id}/heartbeat")
def job_heartbeat(run_id:int,data:ClaimInput,db:Session=Depends(get_db)):
    agent=_agent(db,data.agent_name,data.authentication_identity);lease,run=_owned_lease(db,run_id,agent)
    _extend(lease);run.status="running";agent.last_seen_at=datetime.now(timezone.utc);env=env_service.environment_for_run(db,run_id);db.commit()
    return {"status":"ok","lease_expires_at":lease.lease_expires_at,**({"cancel_requested":env.cancel_requested_at is not None,"cleanup_requested":env.cleanup_requested_at is not None} if env else {})}

@router.post("/jobs/{run_id}/complete")
def complete(run_id:int,data:ClaimInput,db:Session=Depends(get_db)):
    agent=_agent(db,data.agent_name,data.authentication_identity);lease,run=_owned_lease(db,run_id,agent);now=datetime.now(timezone.utc)
    env=env_service.environment_for_run(db,run_id)
    if env:
        if env.status!="CLEANED":raise HTTPException(409,"Environment must be cleaned up before the run can complete")
        env.outcome="completed";env.stopped_at=now
    run.status="completed";run.completed_at=now;db.delete(lease);agent.status="online";agent.last_seen_at=now;db.commit();return {"status":"completed"}

@router.post("/jobs/{run_id}/fail")
def fail(run_id:int,data:FailInput,db:Session=Depends(get_db)):
    agent=_agent(db,data.agent_name,data.authentication_identity);lease,run=_owned_lease(db,run_id,agent);now=datetime.now(timezone.utc)
    final=lease.attempt_count>=MAX_ATTEMPTS or not data.retryable
    if final:run.status="failed";run.completed_at=now
    else:run.status="queued"
    run.error_message=data.error_message or "Runner reported job failure"
    env_service.requeue_after_lease_loss(db,env_service.environment_for_run(db,run_id),final=final,canceled=False)
    db.delete(lease);agent.status="online";db.commit();return {"status":run.status}

@router.post("/jobs/{run_id}/canceled")
def canceled(run_id:int,data:RunnerIdentity,db:Session=Depends(get_db)):
    lease,run,env=_leased_env(db,run_id,data);now=datetime.now(timezone.utc)
    if env.cancel_requested_at is None:raise HTTPException(409,"Cancellation was not requested for this run")
    run.status="canceled";run.completed_at=now;env.outcome="canceled";env.stopped_at=now
    env_service.add_event(db,env,"canceled","Worker stopped the run and released its resources.",status=env.status)
    db.delete(lease);db.commit();return {"status":"canceled"}

@router.post("/jobs/{run_id}/apk")
def download_apk(run_id:int,data:RunnerIdentity,db:Session=Depends(get_db)):
    agent=_agent(db,data.agent_name,data.authentication_identity);_,run=_owned_lease(db,run_id,agent)
    build=db.get(AppBuild,run.app_build_id) if run.app_build_id else None
    if not build or build.scan_status!="approved":raise HTTPException(409,"Run has no approved build")
    root=Path(settings.upload_dir).resolve();path=(root/build.apk_storage_key).resolve()
    if root not in path.parents or not path.is_file():raise HTTPException(404,"APK artifact is missing from storage")
    return FileResponse(path,media_type="application/vnd.android.package-archive",filename=f"build_{build.id}.apk",headers={"X-PocketLab-SHA256":build.apk_sha256})

@router.put("/jobs/{run_id}/environment")
def update_environment(run_id:int,data:EnvironmentUpdate,db:Session=Depends(get_db)):
    lease,run,env=_leased_env(db,run_id,data);new=data.status.value
    if env.status=="CANCEL_REQUESTED" and new not in ("CANCEL_REQUESTED","CLEANING","CLEANED","FAILED"):
        _extend(lease);db.commit();return _flags(env)   # keep the cancel request visible; worker will see the flag
    if not transition_allowed(env.status,new):raise HTTPException(409,f"Invalid environment transition {env.status} -> {new}")
    changed=env.status!=new
    env.status=new;env.status_message=data.message
    for field in ("emulator_name","emulator_serial","android_api_level","system_image","active_run_scenario_id","cleanup_verified"):
        value=getattr(data,field)
        if value is not None or field in data.model_fields_set:setattr(env,field,value)
    if data.effective_configuration is not None:env.effective_configuration_json=data.effective_configuration
    if new=="CLEANED":env.stopped_at=datetime.now(timezone.utc)
    env_service.add_event(db,env,"status_changed" if changed else "progress",data.message,status=new,severity="error" if new=="FAILED" else "info")
    _extend(lease);run.status="running";db.commit();return _flags(env)

@router.post("/jobs/{run_id}/environment/device",status_code=201)
def register_environment_device(run_id:int,data:DeviceRegistration,db:Session=Depends(get_db)):
    lease,run,env=_leased_env(db,run_id,data)
    if env.environment_type!="emulator":raise HTTPException(409,"Physical devices are registered by their owner")
    owner_id=db.scalar(select(Project.owner_user_id).where(Project.id==run.project_id))
    device=Device(owner_user_id=owner_id,**data.model_dump(exclude={"agent_name","authentication_identity"}),last_seen_at=datetime.now(timezone.utc))
    db.add(device);db.flush();env.device_id=device.id;_extend(lease);db.commit();return {"device_id":device.id}

@router.post("/jobs/{run_id}/environment/events",status_code=201)
def add_events(run_id:int,data:EventBatch,db:Session=Depends(get_db)):
    lease,run,env=_leased_env(db,run_id,data)
    for e in data.events:env_service.add_event(db,env,e.event_type,e.message,severity=e.severity,status=e.status.value if e.status else None,data=e.data,at=e.event_timestamp)
    _extend(lease);db.commit();return {"inserted":len(data.events),**_flags(env)}

@router.post("/jobs/{run_id}/environment/conditions",status_code=201)
def add_conditions(run_id:int,data:ConditionBatch,db:Session=Depends(get_db)):
    lease,run,env=_leased_env(db,run_id,data)
    for c in data.conditions:
        db.add(ConditionApplication(environment_id=env.id,parameter_key=c.parameter_key,requested_value=c.requested_value,applied_value=c.applied_value,
                                    observed_value=c.observed_value,unit=c.unit,application_status=c.application_status.value,mechanism=c.mechanism,
                                    evidence_json=c.evidence,attempt=max(env.attempt,1),applied_at=c.applied_at,verified_at=c.verified_at))
    _extend(lease);db.commit();return {"inserted":len(data.conditions)}

@router.post("/jobs/{run_id}/scenarios/{run_scenario_id}")
def scenario_result(run_id:int,run_scenario_id:int,data:ScenarioResultInput,db:Session=Depends(get_db)):
    agent=_agent(db,data.agent_name,data.authentication_identity);lease,run=_owned_lease(db,run_id,agent)
    rs=db.scalar(select(RunScenario).where(RunScenario.id==run_scenario_id,RunScenario.test_run_id==run_id))
    if not rs:raise HTTPException(404,"Scenario does not belong to this run")
    now=datetime.now(timezone.utc);rs.status=data.status
    if data.status=="running":rs.started_at=now;rs.completed_at=None
    else:rs.completed_at=now;rs.result_json=data.result
    _extend(lease);db.commit();return {"status":rs.status}

@router.post("/jobs/{run_id}/artifacts",status_code=201)
async def upload_artifact(run_id:int,agent_name:str=Form(...,max_length=160),authentication_identity:str=Form(...,max_length=255),artifact_type:str=Form(...),file:UploadFile=File(...),db:Session=Depends(get_db)):
    agent=_agent(db,agent_name,authentication_identity);lease,run=_owned_lease(db,run_id,agent)
    content_type=(file.content_type or "").split(";")[0].strip()
    if artifact_type not in ARTIFACT_TYPES or content_type not in ARTIFACT_TYPES[artifact_type]:raise HTTPException(422,"Artifact type or content type not allowed")
    filename=re.sub(r"[^A-Za-z0-9._-]","_",Path(file.filename or "artifact").name)[:120] or "artifact"
    key=f"evidence/run_{run.id}/{uuid.uuid4().hex}_{filename}";root=Path(settings.upload_dir).resolve();path=(root/key).resolve()
    if root not in path.parents:raise HTTPException(422,"Invalid artifact path")
    path.parent.mkdir(parents=True,exist_ok=True);digest=hashlib.sha256();size=0;head=b""
    try:
        with path.open("wb") as target:
            while chunk:=await file.read(1024*1024):
                size+=len(chunk)
                if size>MAX_ARTIFACT_BYTES:raise HTTPException(413,"Artifact exceeds the size limit")
                if len(head)<8:head+=chunk[:8]
                digest.update(chunk);target.write(chunk)
        if content_type=="image/png" and not head.startswith(b"\x89PNG"):raise HTTPException(422,"Screenshot is not a PNG")
    except Exception:
        path.unlink(missing_ok=True);raise
    row=EvidenceArtifact(test_run_id=run.id,artifact_type=artifact_type,storage_key=key,filename=filename,sha256=digest.hexdigest(),size_bytes=size,content_type=content_type)
    db.add(row);_extend(lease);db.commit();db.refresh(row);return {"id":row.id,"sha256":row.sha256,"size_bytes":size}

@router.post("/jobs/{run_id}/metrics/batch",status_code=202)
def batch_metrics(run_id:int,data:BatchMetrics,db:Session=Depends(get_db)):
    agent=_agent(db,data.agent_name,data.authentication_identity);lease,run=_owned_lease(db,run_id,agent)
    owner_id=db.scalar(select(Project.owner_user_id).where(Project.id==run.project_id))
    env=env_service.environment_for_run(db,run_id)
    rows=[];seen=set()
    try:
        for sample in data.samples:
            if sample.device_id is None:
                raise HTTPException(422,"Every measurement must identify its registered device")
            device=db.scalar(select(Device).where(Device.id==sample.device_id,Device.owner_user_id==owner_id))
            if not device:
                raise HTTPException(422,"Measurement device must be registered to the project owner")
            if env is not None and env.device_id is not None and sample.device_id!=env.device_id:
                raise HTTPException(422,"Measurement device does not match the run's execution environment")
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
        _extend(lease);run.status="running";db.commit()
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
