import hashlib, json
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database import get_db
from app.dependencies import current_user
from app.models import AppBuild, AuditLog, ConditionProfile, ConditionProfileVersion, Device, IdempotencyRecord, ImpactEstimate, MetricSample, Project, RunProfile, RunScenario, ScenarioVersion, TestRun, User
from app.schemas.api import ImpactInput, RunInput
from app.services.impact import estimate_impact
from app.services.ownership import owned_project

router=APIRouter(prefix="/api/v1/test-runs",tags=["test runs", "analytics"])

@router.post("",status_code=201)
def create_run(data:RunInput, idempotency_key:str|None=Header(None,alias="Idempotency-Key"), db:Session=Depends(get_db),user:User=Depends(current_user)):
    owned_project(db,data.project_id,user.id)
    if data.app_build_id:
        build=db.scalar(select(AppBuild).where(AppBuild.id==data.app_build_id,AppBuild.project_id==data.project_id))
        if not build:raise HTTPException(422,"Build does not belong to project")
        if build.scan_status!="approved":raise HTTPException(409,"Build is quarantined and cannot be queued until an external scanner approves it")
    actual_device=None
    if data.actual_device_id is not None:
        actual_device=db.scalar(select(Device).where(Device.id==data.actual_device_id,Device.owner_user_id==user.id))
        if not actual_device:raise HTTPException(422,"Actual device must be registered to the current user")
    digest=hashlib.sha256(data.model_dump_json().encode()).hexdigest()
    if idempotency_key:
        if len(idempotency_key)>128:raise HTTPException(422,"Idempotency-Key must be 128 characters or fewer")
        old=db.scalar(select(IdempotencyRecord).where(IdempotencyRecord.user_id==user.id,IdempotencyRecord.key==idempotency_key))
        if old:
            if old.request_hash!=digest: raise HTTPException(409,"Idempotency key was already used for a different request")
            return db.get(TestRun,int(old.resource_id))
    for pid in data.profile_version_ids:
        pv=db.get(ConditionProfileVersion,pid)
        if not pv:raise HTTPException(422,f"Unknown profile version: {pid}")
        profile=db.get(ConditionProfile,pv.profile_id)
        if profile.owner_user_id not in (None,user.id) and not profile.is_public_template:raise HTTPException(404,"Profile version not found")
    for sid in data.scenario_version_ids:
        if not db.get(ScenarioVersion,sid):raise HTTPException(422,f"Unknown scenario version: {sid}")
    run=TestRun(project_id=data.project_id,app_build_id=data.app_build_id,created_by_user_id=user.id,status="queued",execution_mode=data.execution_mode,requested_repetitions=data.requested_repetitions,idempotency_key=idempotency_key)
    db.add(run);db.flush()
    for pid in data.profile_version_ids:db.add(RunProfile(test_run_id=run.id,profile_version_id=pid,actual_device_id=actual_device.id if actual_device else None))
    for sid in data.scenario_version_ids:db.add(RunScenario(test_run_id=run.id,scenario_version_id=sid))
    if idempotency_key:db.add(IdempotencyRecord(user_id=user.id,key=idempotency_key,request_hash=digest,resource_type="test_run",resource_id=str(run.id)))
    db.add(AuditLog(actor_user_id=user.id,action="test_run.create",resource_type="test_run",resource_id=str(run.id),outcome="queued"))
    db.commit();db.refresh(run);return run

def _owned_run(db,user,run_id):
    run=db.scalar(select(TestRun).join(Project,TestRun.project_id==Project.id).where(TestRun.id==run_id,Project.owner_user_id==user.id))
    if not run:raise HTTPException(404,"Test run not found")
    return run

@router.get("")
def list_runs(project_id:int|None=None,status:str|None=None,limit:int=Query(50,ge=1,le=100),offset:int=Query(0,ge=0),db:Session=Depends(get_db),user:User=Depends(current_user)):
    stmt=select(TestRun).join(Project).where(Project.owner_user_id==user.id)
    if project_id is not None:stmt=stmt.where(TestRun.project_id==project_id)
    if status:stmt=stmt.where(TestRun.status==status)
    return db.scalars(stmt.order_by(TestRun.created_at.desc()).limit(limit).offset(offset)).all()

@router.get("/{run_id}")
def get_run(run_id:int,db:Session=Depends(get_db),user:User=Depends(current_user)):return _owned_run(db,user,run_id)

@router.post("/{run_id}/cancel")
def cancel_run(run_id:int,db:Session=Depends(get_db),user:User=Depends(current_user)):
    run=_owned_run(db,user,run_id)
    if run.status in ("completed","failed","canceled"):raise HTTPException(409,"Run is already terminal")
    run.status="canceled";run.completed_at=datetime.now(timezone.utc)
    db.add(AuditLog(actor_user_id=user.id,action="test_run.cancel",resource_type="test_run",resource_id=str(run.id),outcome="canceled"));db.commit();return run

@router.get("/{run_id}/metrics")
def run_metrics(run_id:int,limit:int=Query(500,ge=1,le=5000),offset:int=Query(0,ge=0),parameter_key:str|None=None,db:Session=Depends(get_db),user:User=Depends(current_user)):
    _owned_run(db,user,run_id);stmt=select(MetricSample).where(MetricSample.test_run_id==run_id)
    if parameter_key:stmt=stmt.where(MetricSample.parameter_key==parameter_key)
    return db.scalars(stmt.order_by(MetricSample.observed_at).limit(limit).offset(offset)).all()

@router.get("/{run_id}/findings")
def run_findings(run_id:int,db:Session=Depends(get_db),user:User=Depends(current_user)):
    from app.models import Finding
    _owned_run(db,user,run_id);return db.scalars(select(Finding).where(Finding.test_run_id==run_id).order_by(Finding.created_at)).all()

@router.get("/{run_id}/artifacts")
def run_artifacts(run_id:int,db:Session=Depends(get_db),user:User=Depends(current_user)):
    from app.models import EvidenceArtifact
    _owned_run(db,user,run_id);return db.scalars(select(EvidenceArtifact).where(EvidenceArtifact.test_run_id==run_id).order_by(EvidenceArtifact.created_at)).all()

@router.post("/{run_id}/impact-estimates",status_code=201)
def impact(run_id:int,data:ImpactInput,db:Session=Depends(get_db),user:User=Depends(current_user)):
    run=_owned_run(db,user,run_id)
    if not data.mutually_exclusive:
        raise HTTPException(422,"Condition groups must be declared mutually exclusive to avoid double-counting")
    db.scalar(select(TestRun).where(TestRun.id==run.id).with_for_update())
    existing=db.scalars(select(ImpactEstimate).where(ImpactEstimate.test_run_id==run_id)).all()
    if any(row.condition_group==data.condition_group for row in existing):raise HTTPException(409,"Condition group already has an estimate for this run")
    if sum(row.population_share for row in existing)+data.population_share>1:
        raise HTTPException(422,"Mutually exclusive condition group population shares cannot sum above 1")
    calc=estimate_impact(data.monthly_active_users,data.population_share,data.observed_failure_count,data.total_test_attempts)
    row=ImpactEstimate(test_run_id=run_id,monthly_active_users=data.monthly_active_users,condition_group=data.condition_group,population_share=data.population_share,observed_failure_count=data.observed_failure_count,total_test_attempts=data.total_test_attempts,estimated_failure_rate=calc["estimated_failure_rate"],estimated_affected_users=calc["estimated_affected_users"],uncertainty_json=calc["uncertainty"],data_source=data.data_source,assumptions=data.assumptions,is_synthetic=data.is_synthetic)
    db.add(row);db.commit();db.refresh(row);return row
