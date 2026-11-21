from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.dependencies import current_user
from app.models import AuditLog, User
from app.schemas.api import LoginInput, RefreshInput, TokenPair, UserCreate
from app.services import auth as service

router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])


@router.post("/register", status_code=201)
def register(data: UserCreate, db: Session = Depends(get_db)):
    try:
        user = service.register(db, data.email, data.password)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    db.add(AuditLog(actor_user_id=user.id, action="user.register", resource_type="user", resource_id=str(user.id), outcome="success"))
    db.commit()
    return {"id": user.id, "email": user.email}


@router.post("/login", response_model=TokenPair)
def login(data: LoginInput, db: Session = Depends(get_db)):
    pair = service.login(db, data.email, data.password)
    if not pair:
        raise HTTPException(401, "Invalid email or password")
    return {"access_token": pair[0], "refresh_token": pair[1]}


@router.post("/refresh", response_model=TokenPair)
def refresh(data: RefreshInput, db: Session = Depends(get_db)):
    pair = service.rotate_refresh(db, data.refresh_token)
    if not pair:
        raise HTTPException(401, "Invalid, expired, or revoked refresh token")
    return {"access_token": pair[0], "refresh_token": pair[1]}


@router.post("/logout", status_code=204)
def logout(data: RefreshInput, db: Session = Depends(get_db), user: User = Depends(current_user)):
    service.revoke_refresh(db, data.refresh_token, user.id)
    return None
