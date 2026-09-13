import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, Response, status
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .database import get_db
from .models import AuthSession, User
from . import settings


COOKIE_NAME = "__Host-lan_chat_session" if settings.COOKIE_SECURE else "lan_chat_session"
SESSION_DAYS = int(os.getenv("SESSION_DAYS", "7"))
PBKDF2_ITERATIONS = 310_000
PASSWORD_HASHER = PasswordHasher(time_cost=2, memory_cost=19_456, parallelism=1)


def hash_password(password: str, salt_hex: str | None = None) -> tuple[str, str]:
    if salt_hex is None:
        return "argon2id", PASSWORD_HASHER.hash(password)
    salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS
    )
    return salt.hex(), digest.hex()


def verify_password(password: str, salt_hex: str, expected_hash: str) -> bool:
    if expected_hash.startswith("$argon2"):
        try:
            return PASSWORD_HASHER.verify(expected_hash, password)
        except (VerifyMismatchError, InvalidHashError):
            return False
    _, actual_hash = hash_password(password, salt_hex)
    return hmac.compare_digest(actual_hash, expected_hash)


def password_needs_rehash(password_hash: str) -> bool:
    return not password_hash.startswith("$argon2") or PASSWORD_HASHER.check_needs_rehash(
        password_hash
    )


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(db: Session, user_id: int, response: Response) -> None:
    raw_token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)
    db.execute(delete(AuthSession).where(AuthSession.expires_at <= datetime.now(timezone.utc)))
    db.add(
        AuthSession(
            token_hash=token_digest(raw_token),
            user_id=user_id,
            expires_at=expires_at,
        )
    )
    db.commit()
    response.set_cookie(
        COOKIE_NAME,
        raw_token,
        max_age=SESSION_DAYS * 24 * 60 * 60,
        httponly=True,
        samesite="lax",
        secure=settings.COOKIE_SECURE,
        path="/",
    )


def delete_session(db: Session, token: str | None, response: Response) -> None:
    if token:
        db.execute(delete(AuthSession).where(AuthSession.token_hash == token_digest(token)))
        db.commit()
    response.delete_cookie(
        COOKIE_NAME,
        path="/",
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
    )


def user_from_token(db: Session, token: str | None) -> User | None:
    if not token:
        return None
    now = datetime.now(timezone.utc)
    return db.scalar(
        select(User)
        .join(AuthSession, AuthSession.user_id == User.id)
        .where(
            AuthSession.token_hash == token_digest(token),
            AuthSession.expires_at > now,
        )
    )


def get_current_user(
    request: Request, db: Session = Depends(get_db)
) -> User:
    user = user_from_token(db, request.cookies.get(COOKIE_NAME))
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not signed in")
    return user
