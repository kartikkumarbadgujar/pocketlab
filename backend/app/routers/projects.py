from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database import get_db
from app.dependencies import current_user
from app.models import AppBuild, AuditLog, Device, Project, User
from app.schemas.api import DeviceInput, ProjectInput
from app.services.apk import validate_and_store_apk
from app.services.ownership import owned_project

router = APIRouter(tags=["projects", "devices", "builds"])


@router.get("/api/v1/projects")
def list_projects(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0), db: Session = Depends(get_db), user: User = Depends(current_user)):
    rows = db.scalars(select(Project).where(Project.owner_user_id == user.id).order_by(Project.id).limit(limit).offset(offset)).all()
    return rows


@router.post("/api/v1/projects", status_code=201)
def create_project(data: ProjectInput, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if db.scalar(select(Project.id).where(Project.owner_user_id==user.id,Project.name==data.name)):
        raise HTTPException(409,"A project with this name already exists")
    project = Project(owner_user_id=user.id, **data.model_dump())
    db.add(project); db.flush()
    db.add(AuditLog(actor_user_id=user.id, action="project.create", resource_type="project", resource_id=str(project.id), outcome="success")); db.commit(); db.refresh(project)
    return project


@router.get("/api/v1/projects/{project_id}")
def get_project(project_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return owned_project(db, project_id, user.id)


@router.put("/api/v1/projects/{project_id}")
def update_project(project_id: int, data: ProjectInput, db: Session = Depends(get_db), user: User = Depends(current_user)):
    project = owned_project(db, project_id, user.id)
    for key, value in data.model_dump().items(): setattr(project, key, value)
    db.commit(); db.refresh(project); return project


@router.delete("/api/v1/projects/{project_id}", status_code=204)
def delete_project(project_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    project = owned_project(db, project_id, user.id); db.delete(project); db.commit(); return None


@router.get("/api/v1/devices")
def list_devices(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return db.scalars(select(Device).where(Device.owner_user_id == user.id).order_by(Device.id)).all()


@router.post("/api/v1/devices/register", status_code=201)
def register_device(data: DeviceInput, db: Session = Depends(get_db), user: User = Depends(current_user)):
    device = Device(owner_user_id=user.id, **data.model_dump()); db.add(device); db.flush()
    db.add(AuditLog(actor_user_id=user.id,action="device.register",resource_type="device",resource_id=str(device.id),outcome="success"));db.commit(); db.refresh(device); return device


@router.get("/api/v1/devices/{device_id}")
def get_device(device_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    device = db.scalar(select(Device).where(Device.id == device_id, Device.owner_user_id == user.id))
    if not device: raise HTTPException(404, "Device not found")
    return device


@router.post("/api/v1/projects/{project_id}/builds", status_code=201)
async def upload_build(project_id: int, version_name: str | None = Form(None), version_code: int | None = Form(None), package_name: str = Form(...), file: UploadFile = File(...), db: Session = Depends(get_db), user: User = Depends(current_user)):
    project = owned_project(db, project_id, user.id)
    if project.application_package and project.application_package != package_name:
        raise HTTPException(422, "Build package does not match the project application package")
    key, digest, size = await validate_and_store_apk(file)
    build = AppBuild(project_id=project_id, version_name=version_name, version_code=version_code, package_name=package_name, apk_sha256=digest, apk_storage_key=key, scan_status="quarantined")
    db.add(build); db.flush();db.add(AuditLog(actor_user_id=user.id,action="build.upload",resource_type="app_build",resource_id=str(build.id),outcome="quarantined"));db.commit(); db.refresh(build)
    return {"id": build.id, "project_id": project_id, "version_name": version_name, "version_code": version_code, "package_name": package_name, "apk_sha256": digest, "size_bytes": size, "scan_status": build.scan_status}


@router.get("/api/v1/projects/{project_id}/builds")
def list_builds(project_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    owned_project(db, project_id, user.id)
    return db.scalars(select(AppBuild).where(AppBuild.project_id == project_id).order_by(AppBuild.id.desc())).all()
