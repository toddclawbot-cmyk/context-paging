"""User management HTTP endpoints."""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session
from .auth import hash_password, verify_password, issue_access_token, issue_refresh_token, hash_refresh_token, decode_access_token
from .settings import Settings, load_settings
from .models import User
from .deps import get_db, get_settings, get_current_user

router = APIRouter(prefix="/users", tags=["users"])


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


@router.post("/register", response_model=TokenResponse, status_code=201)
def register(req: RegisterRequest, db: Session = Depends(get_db),
             settings: Settings = Depends(get_settings)) -> TokenResponse:
    existing = db.query(User).filter(User.email == req.email).one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail="email already registered")
    user = User(email=req.email, hashed_password=hash_password(req.password), full_name=req.full_name)
    db.add(user)
    db.commit()
    db.refresh(user)
    return TokenResponse(
        access_token=issue_access_token(user.id, settings),
        refresh_token=issue_refresh_token(user.id),
        token_type="bearer",
        expires_in=settings.jwt_ttl_seconds,
    )


@router.post("/login", response_model=TokenResponse)
def login(req: LoginRequest, db: Session = Depends(get_db),
          settings: Settings = Depends(get_settings)) -> TokenResponse:
    user = db.query(User).filter(User.email == req.email).one_or_none()
    if user is None or not verify_password(req.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="invalid credentials")
    return TokenResponse(
        access_token=issue_access_token(user.id, settings),
        refresh_token=issue_refresh_token(user.id),
        token_type="bearer",
        expires_in=settings.jwt_ttl_seconds,
    )


@router.get("/me")
def me(current_user: User = Depends(get_current_user)) -> dict:
    return {"id": current_user.id, "email": current_user.email,
            "full_name": current_user.full_name, "is_admin": current_user.is_admin}
