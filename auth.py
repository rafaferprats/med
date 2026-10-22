"""Accounts: password hashing, login tokens and the current_user dependency."""
import hashlib
import hmac
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException, Request

from config import COOKIE_NAME, MIN_PASSWORD_LENGTH, TOKEN_DAYS
from db import connect

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SCRYPT = {"n": 2**14, "r": 8, "p": 1}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **SCRYPT)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    salt_hex, digest_hex = stored.split("$", 1)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), **SCRYPT)
    return hmac.compare_digest(digest.hex(), digest_hex)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_user(email: str, password: str) -> int:
    email = email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="Escribe un email válido.")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"La contraseña necesita al menos {MIN_PASSWORD_LENGTH} caracteres.",
        )
    try:
        with connect() as conn:
            cur = conn.execute(
                "INSERT INTO users (email, password_hash) VALUES (?, ?)",
                (email, hash_password(password)),
            )
            return cur.lastrowid
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="Ya existe una cuenta con este email.")


def authenticate(email: str, password: str) -> int:
    with connect() as conn:
        row = conn.execute(
            "SELECT id, password_hash FROM users WHERE email = ?", (email.strip().lower(),)
        ).fetchone()
    if not row or not verify_password(password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Email o contraseña incorrectos.")
    return row["id"]


def create_token(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires = (_now() + timedelta(days=TOKEN_DAYS)).isoformat()
    with connect() as conn:
        conn.execute(
            "INSERT INTO auth_tokens (token, user_id, expires_at) VALUES (?, ?, ?)",
            (token, user_id, expires),
        )
    return token


def delete_token(token: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM auth_tokens WHERE token = ?", (token,))


def user_by_id(user_id: int) -> sqlite3.Row:
    with connect() as conn:
        return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def optional_user(request: Request) -> Optional[sqlite3.Row]:
    """FastAPI dependency: the logged-in user, or None for a visitor."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    with connect() as conn:
        return conn.execute(
            "SELECT u.* FROM auth_tokens t JOIN users u ON u.id = t.user_id "
            "WHERE t.token = ? AND t.expires_at > ?",
            (token, _now().isoformat()),
        ).fetchone()


def current_user(request: Request) -> sqlite3.Row:
    """FastAPI dependency: the logged-in user, or 401."""
    user = optional_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Inicia sesión para continuar.")
    return user
