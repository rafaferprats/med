"""Protections for running the app on the internet (behind an HTTPS tunnel).

- Rate limits in memory: failed logins, sign-ups and questions per minute.
- The real client IP when the request comes through a local tunnel (cloudflared).
- Secure cookies when the request arrived over HTTPS.
- Security headers on every response.
"""
import os
import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

LOGIN_FAILURES = int(os.getenv("LOGIN_FAILURES", "5"))          # per IP + email
LOGIN_WINDOW = int(os.getenv("LOGIN_WINDOW_SECONDS", "900"))     # 15 minutes
SIGNUPS_PER_HOUR = int(os.getenv("SIGNUPS_PER_HOUR", "5"))       # per IP
QUESTIONS_PER_MINUTE = int(os.getenv("QUESTIONS_PER_MINUTE", "6"))
LAB_UPLOADS_PER_HOUR = int(os.getenv("LAB_UPLOADS_PER_HOUR", "20"))  # PDFs per account, Premium too
MAX_MESSAGE_CHARS = int(os.getenv("MAX_MESSAGE_CHARS", "4000"))
MAX_IMAGE_MB = int(os.getenv("MAX_IMAGE_MB", "10"))
MAX_PDF_MB = int(os.getenv("MAX_PDF_MB", "10"))
MAX_BODY_MB = max(MAX_IMAGE_MB, MAX_PDF_MB) + 1  # biggest upload, plus the form


def max_body_bytes(path: str) -> int:
    """Biggest request accepted on each route: uploads get their own limit, the rest 1 MB."""
    if path.startswith("/labs/analyze"):
        return (MAX_PDF_MB + 1) * 1024 * 1024
    if path.startswith("/chat/image"):
        return (MAX_IMAGE_MB + 1) * 1024 * 1024
    return 1024 * 1024

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' blob: data:; style-src 'self' 'unsafe-inline'; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    ),
}


class RateLimiter:
    """Counts events per key inside a sliding time window."""

    def __init__(self):
        self._events: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def _clean(self, key: str, window: int) -> deque:
        q = self._events[key]
        limit = time.monotonic() - window
        while q and q[0] < limit:
            q.popleft()
        return q

    def count(self, key: str, window: int) -> int:
        with self._lock:
            return len(self._clean(key, window))

    def hit(self, key: str, window: int) -> int:
        with self._lock:
            q = self._clean(key, window)
            q.append(time.monotonic())
            return len(q)

    def reset(self, key: str) -> None:
        with self._lock:
            self._events.pop(key, None)


limiter = RateLimiter()


def client_ip(request: Request) -> str:
    """The visitor's IP. Behind a local tunnel every request comes from 127.0.0.1, so the
    tunnel's header is trusted only in that case (it cannot be faked from outside)."""
    peer = request.client.host if request.client else "unknown"
    if peer in ("127.0.0.1", "::1"):
        forwarded = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return peer


def is_https(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "") == "https"


def check_login_allowed(request: Request, email: str) -> str:
    key = f"login:{client_ip(request)}:{email.strip().lower()}"
    if limiter.count(key, LOGIN_WINDOW) >= LOGIN_FAILURES:
        raise HTTPException(status_code=429, detail="Demasiados intentos fallidos. Espera 15 minutos y vuelve a probar.")
    return key


def check_signup_allowed(request: Request) -> None:
    if limiter.hit(f"signup:{client_ip(request)}", 3600) > SIGNUPS_PER_HOUR:
        raise HTTPException(status_code=429, detail="Demasiadas cuentas creadas desde esta conexión. Prueba más tarde.")


def check_question_rate(request: Request, who: str) -> None:
    """Questions per minute, per account (or visitor) and per IP."""
    for key in (f"q:{who}", f"q-ip:{client_ip(request)}"):
        if limiter.hit(key, 60) > QUESTIONS_PER_MINUTE:
            raise HTTPException(status_code=429, detail="Vas muy rápido: espera un minuto antes de seguir preguntando.")


def check_lab_uploads(user_id: int) -> None:
    """Reading a PDF costs CPU: a limit per account, so nobody can keep the server busy."""
    if limiter.hit(f"lab:{user_id}", 3600) > LAB_UPLOADS_PER_HOUR:
        raise HTTPException(status_code=429, detail="Has subido muchas analíticas en poco tiempo. Prueba dentro de un rato.")


def check_message(message: str) -> None:
    if len(message) > MAX_MESSAGE_CHARS:
        raise HTTPException(status_code=413, detail=f"El mensaje es demasiado largo (máximo {MAX_MESSAGE_CHARS} caracteres).")
