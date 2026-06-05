"""FastAPI dependencies: DB session, current user, settings, Redis."""
from __future__ import annotations
from fastapi import Depends, HTTPException, Header
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
import redis
from typing import Optional
from .settings import Settings, load_settings
from .models import User
from .auth import decode_access_token


_engine = None
_SessionLocal = None
_redis_client = None


def get_engine():
    global _engine
    if _engine is None:
        settings = load_settings()
        _engine = create_engine(settings.database_url, pool_pre_ping=True, pool_size=10)
    return _engine


def get_session_local():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), autoflush=False, autocommit=False)
    return _SessionLocal


def get_db() -> Session:
    db = get_session_local()()
    try:
        yield db
    finally:
        db.close()


def get_redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        settings = load_settings()
        _redis_client = redis.from_url(settings.redis_url, decode_responses=True)
    return _redis_client


def get_settings() -> Settings:
    return load_settings()


def get_current_user(authorization: Optional[str] = Header(None),
                     db: Session = Depends(get_db),
                     settings: Settings = Depends(get_settings)) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization.split(" ", 1)[1]
    claims = decode_access_token(token, settings)
    if claims is None or "sub" not in claims:
        raise HTTPException(status_code=401, detail="invalid token")
    user = db.query(User).filter(User.id == claims["sub"]).one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="user not found")
    return user
