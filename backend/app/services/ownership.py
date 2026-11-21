from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models import Project


def owned_project(db: Session, project_id: int, user_id: int) -> Project:
    project = db.scalar(select(Project).where(Project.id == project_id, Project.owner_user_id == user_id))
    if not project:
        raise HTTPException(404, "Project not found")
    return project
