"""Checks for the protections used when the app is reachable from the internet:
rate limits, secure cookies, size limits and security headers. Runs without Ollama.
    ./venv/bin/python tests/test_security.py
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ["DB_PATH"] = str(Path(tempfile.mkdtemp()) / "test.db")
os.environ.update({"QUESTIONS_PER_MINUTE": "3", "LOGIN_FAILURES": "3", "SIGNUPS_PER_HOUR": "3",
                   "MAX_MESSAGE_CHARS": "200", "MAX_IMAGE_MB": "1", "FREE_QUESTIONS": "50"})
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from safety import EMERGENCY_MESSAGE  # noqa: E402


class FakeChunk:
    def __init__(self, text):
        self.message = type("M", (), {"content": text})()


async def fake_chat(**kwargs):
    async def gen():
        yield FakeChunk("Respuesta de prueba.")
    return gen()


main.client.chat = fake_chat
PASSWORD = "password123"
failures = []


def check(name, ok):
    if not ok:
        failures.append(name)


def run():
    c = TestClient(main.app)
    # Security headers and no API docs
    r = c.get("/")
    check("cabecera X-Frame-Options", r.headers.get("x-frame-options") == "DENY")
    check("cabecera Content-Security-Policy", "frame-ancestors 'none'" in r.headers.get("content-security-policy", ""))
    check("cabecera nosniff", r.headers.get("x-content-type-options") == "nosniff")
    check("/docs desactivado", c.get("/docs").status_code == 404)
    check("/openapi.json desactivado", c.get("/openapi.json").status_code == 404)

    # Secure cookie only through HTTPS (the tunnel sends X-Forwarded-Proto: https)
    r = c.post("/auth/register", json={"email": "ana@example.com", "password": PASSWORD},
               headers={"x-forwarded-proto": "https"})
    check("registro por HTTPS", r.status_code == 204)
    check("cookie Secure con HTTPS", "secure" in r.headers.get("set-cookie", "").lower())
    r = TestClient(main.app).post("/auth/register", json={"email": "ben@example.com", "password": PASSWORD})
    check("cookie sin Secure en HTTP local", "secure" not in r.headers.get("set-cookie", "").lower())

    # Sign-ups per IP: 3 per hour (2 already made above)
    TestClient(main.app).post("/auth/register", json={"email": "cris@example.com", "password": PASSWORD})
    r = TestClient(main.app).post("/auth/register", json={"email": "dani@example.com", "password": PASSWORD})
    check("4.º registro desde la misma IP bloqueado", r.status_code == 429)
    # The tunnel header is only trusted from localhost: it cannot be used to dodge limits
    r = TestClient(main.app).post("/auth/register", json={"email": "eva@example.com", "password": PASSWORD},
                                  headers={"cf-connecting-ip": "1.2.3.4"})
    check("cabecera de IP falsa ignorada", r.status_code == 429)

    # Failed logins: 3 failures lock that email from this IP for 15 minutes
    x = TestClient(main.app)
    codes = [x.post("/auth/login", json={"email": "ana@example.com", "password": "mala-clave"}).status_code for _ in range(3)]
    check("3 contraseñas incorrectas dan 401", codes == [401, 401, 401])
    r = x.post("/auth/login", json={"email": "ana@example.com", "password": PASSWORD})
    check("4.º intento bloqueado aunque la clave sea buena", r.status_code == 429)
    r = x.post("/auth/login", json={"email": "ben@example.com", "password": PASSWORD})
    check("otra cuenta no queda bloqueada", r.status_code == 204)

    # Questions per minute (3), but emergencies are always answered
    ok = [x.post("/chat/stream", json={"message": f"Me duele la cabeza {i}"}).status_code for i in range(3)]
    check("3 preguntas por minuto permitidas", ok == [200, 200, 200])
    check("4.ª pregunta en el mismo minuto bloqueada", x.post("/chat/stream", json={"message": "Tengo tos"}).status_code == 429)
    r = x.post("/chat/stream", json={"message": "Tengo un dolor en el pecho que se me va al brazo izquierdo"})
    check("una urgencia se responde aunque haya límite", r.status_code == 200 and r.text == EMERGENCY_MESSAGE)

    # Size limits
    # (another account: ben already used his questions for this minute above)
    main.security.limiter.reset("q-ip:testclient")
    y = TestClient(main.app)
    y.post("/auth/login", json={"email": "cris@example.com", "password": PASSWORD})
    check("mensaje demasiado largo rechazado", y.post("/chat/stream", json={"message": "a" * 201}).status_code == 413)
    big = b"x" * (1024 * 1024 + 10)
    r = y.post("/chat/image", data={"message": "¿Qué es esto?"}, files={"image": ("a.png", big, "image/png")})
    check("imagen de más de 1 MB rechazada", r.status_code == 413)
    r = y.post("/chat/stream", content=b"{" + b" " * (3 * 1024 * 1024) + b"}", headers={"content-type": "application/json"})
    check("petición enorme rechazada", r.status_code == 413)

    total = 20
    for f in failures:
        print("FAIL", f)
    print(f"security tests: {total - len(failures)}/{total} OK")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    run()
