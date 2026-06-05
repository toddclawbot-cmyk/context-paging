"""Authentication: password hashing, JWT issuance, refresh tokens."""
from __future__ import annotations
import hashlib
import hmac
import os
import secrets
import time
from typing import Optional, Dict, Any
import jwt
from .settings import Settings


# --- Password hashing -------------------------------------------------------

def hash_password(password: str) -> str:
    """Hash a password with PBKDF2-HMAC-SHA256, 200_000 iterations, 16-byte salt."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return f"pbkdf2_sha256$200000${salt.hex()}${digest.hex()}"


def verify_password(password: str, hashed: str) -> bool:
    """Constant-time verify a password against a stored hash."""
    try:
        algo, iters, salt_hex, digest_hex = hashed.split("$")
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    salt = bytes.fromhex(salt_hex)
    expected = bytes.fromhex(digest_hex)
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iters))
    return hmac.compare_digest(expected, actual)


# --- JWT --------------------------------------------------------------------

def issue_access_token(user_id: str, settings: Settings) -> str:
    """Issue a short-lived access JWT for a user."""
    now = int(time.time())
    payload = {
        "sub": user_id,
        "iat": now,
        "exp": now + settings.jwt_ttl_seconds,
        "typ": "access",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def issue_refresh_token(user_id: str) -> str:
    """Issue an opaque refresh token; store its hash in DB."""
    raw = secrets.token_urlsafe(48)
    return raw


def hash_refresh_token(token: str) -> str:
    """Store only the SHA-256 hash of a refresh token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def decode_access_token(token: str, settings: Settings) -> Optional[Dict[str, Any]]:
    """Decode and validate an access JWT. Returns None on any failure."""
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
