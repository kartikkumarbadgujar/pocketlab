import hashlib
import re
import uuid
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.config import settings
from app.database import get_db
from app.dependencies import current_user
from app.models import AppBuild, AuditLog, Device, Project, User
from app.schemas.api import DeviceInput, ProjectInput
from app.services import blob
from app.services.apk import validate_and_store_apk, validate_blob_apk
from app.services.ownership import owned_project


class UploadedBuildInput(BaseModel):
    url: str = Field(max_length=1024)
    pathname: str = Field(max_length=255)
    package_name: str = Field(min_length=1, max_length=255)
    version_name: str | None = Field(default=None, max_length=100)
    version_code: int | None = None


class BuildApprovalInput(BaseModel):
    reason: str = Field(min_length=3, max_length=300)
    # The owner must explicitly confirm they own the app or are authorized to test it.
    confirm_authorized: bool

router = APIRouter(tags=["projects", "devices", "builds"])


@router.post("/api/v1/projects/{project_id}/builds/{build_id}/approve")
def approve_own_build(project_id: int, build_id: int, data: BuildApprovalInput, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Project-owner self-approval of a quarantined build (ALLOW_OWNER_BUILD_APPROVAL). The stored file is re-checked
    against the upload's SHA-256 first; the worker verifies it again before installing."""
    if not settings.allow_owner_build_approval:
        raise HTTPException(403, "Builds on this server must be approved by an operator (scripts/approve_build.py)")
    if not data.confirm_authorized:
        raise HTTPException(422, "Confirm that you own this app or are authorized to test it")
    owned_project(db, project_id, user.id)
    build = db.scalar(select(AppBuild).where(AppBuild.id == build_id, AppBuild.project_id == project_id))
    if not build:
        raise HTTPException(404, "Build not found")
    if build.scan_status == "approved":
        return {"id": build.id, "scan_status": build.scan_status}
    if build.scan_status == "rejected":
        raise HTTPException(409, "A rejected build cannot be approved; upload it again")
    if build.apk_storage_key.startswith(blob.BLOB_PREFIX):
        digest, _ = validate_blob_apk(build.apk_storage_key[len(blob.BLOB_PREFIX):])
    else:
        path = (Path(settings.upload_dir).resolve() / build.apk_storage_key).resolve()
        if not path.is_file():
            raise HTTPException(409, "The APK file is no longer in storage; upload it again")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != build.apk_sha256:
        raise HTTPException(409, "Stored APK does not match the uploaded file's SHA-256; refusing to approve")
    build.scan_status = "approved"
    db.add(AuditLog(actor_user_id=user.id, action="build.approve", resource_type="app_build", resource_id=str(build.id), outcome=f"owner: {data.reason}"[:40]))
    db.commit()
    return {"id": build.id, "scan_status": build.scan_status}


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
    return _save_build(db, user, project_id, version_name, version_code, package_name, digest, key, size)


def _save_build(db: Session, user: User, project_id: int, version_name: str | None, version_code: int | None, package_name: str, digest: str, key: str, size: int) -> dict:
    build = AppBuild(project_id=project_id, version_name=version_name, version_code=version_code, package_name=package_name, apk_sha256=digest, apk_storage_key=key, scan_status="quarantined")
    db.add(build); db.flush();db.add(AuditLog(actor_user_id=user.id,action="build.upload",resource_type="app_build",resource_id=str(build.id),outcome="quarantined"));db.commit(); db.refresh(build)
    return {"id": build.id, "project_id": project_id, "version_name": version_name, "version_code": version_code, "package_name": package_name, "apk_sha256": digest, "size_bytes": size, "scan_status": build.scan_status}


# Direct upload (serverless hosting): the browser uploads to private Blob storage with a short-lived token, then
# registers the stored file here. Returns 404 when Blob storage is not configured, and clients fall back to multipart.
_BLOB_PATH = re.compile(r"^quarantine/p(\d+)/[0-9a-f]{32}\.apk$")


@router.post("/api/v1/projects/{project_id}/builds/upload-token")
def build_upload_token(project_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    owned_project(db, project_id, user.id)
    if not blob.enabled():
        raise HTTPException(404, "Direct APK upload is not configured on this server")
    pathname = f"quarantine/p{project_id}/{uuid.uuid4().hex}.apk"
    return {"pathname": pathname, "client_token": blob.client_upload_token(pathname, settings.max_apk_size_bytes), "maximum_size_bytes": settings.max_apk_size_bytes}


@router.post("/api/v1/projects/{project_id}/builds/from-upload", status_code=201)
def register_uploaded_build(project_id: int, data: UploadedBuildInput, db: Session = Depends(get_db), user: User = Depends(current_user)):
    project = owned_project(db, project_id, user.id)
    match = _BLOB_PATH.match(data.pathname)
    if not match or int(match.group(1)) != project_id:
        raise HTTPException(400, "Upload path does not belong to this project")
    blob.check_url(data.url, data.pathname)
    if project.application_package and project.application_package != data.package_name:
        raise HTTPException(422, "Build package does not match the project application package")
    key = blob.BLOB_PREFIX + data.url
    if db.scalar(select(AppBuild.id).where(AppBuild.apk_storage_key == key)):
        raise HTTPException(409, "This upload is already registered")
    try:
        digest, size = validate_blob_apk(data.url)
    except HTTPException as exc:
        if exc.status_code in (400, 413):
            blob.delete(data.url)
        raise
    return _save_build(db, user, project_id, data.version_name, data.version_code, data.package_name, digest, key, size)


@router.get("/api/v1/projects/{project_id}/builds")
def list_builds(project_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    owned_project(db, project_id, user.id)
    return db.scalars(select(AppBuild).where(AppBuild.project_id == project_id).order_by(AppBuild.id.desc())).all()
