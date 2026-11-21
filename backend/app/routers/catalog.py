from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database import get_db
from app.dependencies import current_user
from app.models import AuditLog, ConditionProfile, ConditionProfileVersion, ConditionProfileParameter, MeasurementDefinition, ScenarioVersion, TestScenario, User
from app.schemas.api import ProfileInput

router=APIRouter(tags=["profiles", "scenarios"])

def _store_configuration(db,version_id,configuration):
    for key,value in configuration.items():
        definition=db.scalar(select(MeasurementDefinition).where(MeasurementDefinition.parameter_key==key))
        if not definition or not definition.is_configurable:
            raise HTTPException(422,f"Unknown or observation-only profile parameter: {key}")
        fields={"numeric_value":value} if isinstance(value,(int,float)) and not isinstance(value,bool) else {"boolean_value":value} if isinstance(value,bool) else {"text_value":str(value)}
        db.add(ConditionProfileParameter(profile_version_id=version_id,parameter_definition_id=definition.id,**fields))

@router.get("/api/v1/profiles")
def profiles(db:Session=Depends(get_db), user:User=Depends(current_user)):
    return db.scalars(select(ConditionProfile).where((ConditionProfile.owner_user_id==user.id)|(ConditionProfile.is_public_template.is_(True))).order_by(ConditionProfile.id)).all()

@router.post("/api/v1/profiles",status_code=201)
def create_profile(data:ProfileInput,db:Session=Depends(get_db),user:User=Depends(current_user)):
    row=ConditionProfile(owner_user_id=user.id,name=data.name,description=data.description,profile_type=data.profile_type,is_public_template=False);db.add(row);db.flush()
    version=ConditionProfileVersion(profile_id=row.id,version=1,configuration_json=data.configuration);db.add(version);db.flush();_store_configuration(db,version.id,data.configuration);db.add(AuditLog(actor_user_id=user.id,action="profile.create",resource_type="profile",resource_id=str(row.id),outcome="success"));db.commit();db.refresh(row);return row

@router.get("/api/v1/profiles/{profile_id}")
def get_profile(profile_id:int,db:Session=Depends(get_db),user:User=Depends(current_user)):
    row=db.get(ConditionProfile,profile_id)
    if not row or (row.owner_user_id not in (None,user.id) and not row.is_public_template):raise HTTPException(404,"Profile not found")
    return {"profile":row,"versions":db.scalars(select(ConditionProfileVersion).where(ConditionProfileVersion.profile_id==row.id).order_by(ConditionProfileVersion.version)).all()}

@router.post("/api/v1/profiles/{profile_id}/versions",status_code=201)
def version_profile(profile_id:int,data:ProfileInput,db:Session=Depends(get_db),user:User=Depends(current_user)):
    row=db.get(ConditionProfile,profile_id)
    if not row or row.owner_user_id!=user.id:raise HTTPException(404,"Profile not found")
    latest=db.scalar(select(ConditionProfileVersion).where(ConditionProfileVersion.profile_id==profile_id).order_by(ConditionProfileVersion.version.desc()))
    version=ConditionProfileVersion(profile_id=profile_id,version=(latest.version if latest else 0)+1,configuration_json=data.configuration);db.add(version);db.flush();_store_configuration(db,version.id,data.configuration);db.add(AuditLog(actor_user_id=user.id,action="profile.version",resource_type="profile",resource_id=str(profile_id),outcome="success"));db.commit();db.refresh(version);return version

@router.get("/api/v1/scenarios")
def scenarios(db:Session=Depends(get_db),user:User=Depends(current_user)):
    return db.scalars(select(TestScenario).order_by(TestScenario.id)).all()

@router.get("/api/v1/scenarios/{scenario_id}")
def get_scenario(scenario_id:int,db:Session=Depends(get_db),user:User=Depends(current_user)):
    row=db.get(TestScenario,scenario_id)
    if not row:raise HTTPException(404,"Scenario not found")
    return {"scenario":row,"versions":db.scalars(select(ScenarioVersion).where(ScenarioVersion.scenario_id==scenario_id).order_by(ScenarioVersion.version)).all()}
