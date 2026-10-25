"""End-to-end check of accounts, the daily limit, emergencies and test billing.

Runs without Ollama: the model call is replaced by a fake answer.
    ./venv/bin/python tests/smoke_test.py
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ["DB_PATH"] = str(Path(tempfile.mkdtemp()) / "test.db")
os.environ["FREE_QUESTIONS"] = "3"
os.environ["FREE_IMAGES"] = "1"
os.environ["QUESTIONS_PER_MINUTE"] = "100"  # rate limits are tested in test_security.py
os.environ["SIGNUPS_PER_HOUR"] = "100"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from safety import EMERGENCY_MESSAGE  # noqa: E402


class FakeChunk:
    def __init__(self, text):
        self.message = type("M", (), {"content": text})()


async def fake_chat(**kwargs):
    if kwargs.get("stream"):
        async def gen():
            yield FakeChunk("Fake answer.")
        return gen()
    return FakeChunk("Fake answer.")


main.client.chat = fake_chat
ASK = {"message": "I have a mild headache."}
URGENT = {"message": "I have chest pain spreading to my left arm."}


def new_client(email):
    c = TestClient(main.app)
    r = c.post("/auth/register", json={"email": email, "password": "password123"})
    assert r.status_code == 204, r.text
    return c


def main_test():
    # A visitor gets one answer, then must sign up; emergencies are always answered
    anon = TestClient(main.app)
    assert anon.get("/me").status_code == 401
    r = anon.post("/chat/stream", json=ASK)
    assert r.status_code == 200 and r.text == "Fake answer." and r.headers["X-Guest"] == "1"
    guest_sid = r.headers["X-Session-Id"]
    r = anon.post("/chat/stream", json=ASK)
    assert r.status_code == 401 and r.headers["X-Signup-Required"] == "1"
    r = anon.post("/chat/stream", json=URGENT)
    assert r.status_code == 200 and r.text == EMERGENCY_MESSAGE
    assert anon.post("/chat/image", data={"message": "x"}, files={"image": ("a.png", b"x")}).status_code == 401
    # Signing up keeps the guest conversation
    assert anon.post("/auth/register", json={"email": "guest@example.com", "password": "password123"}).status_code == 204
    r = anon.post("/chat/stream", json=ASK | {"session_id": guest_sid})
    assert r.status_code == 200 and r.headers["X-Session-Id"] == guest_sid

    a = new_client("ana@example.com")
    assert a.post("/auth/register", json={"email": "ana@example.com", "password": "password123"}).status_code == 409
    assert a.post("/auth/register", json={"email": "bad", "password": "password123"}).status_code == 400
    assert TestClient(main.app).post("/auth/login", json={"email": "ana@example.com", "password": "wrong-pass"}).status_code == 401

    me = a.get("/me").json()
    assert me["plan"] == "free" and me["remaining"] == {"text": 3, "image": 1, "lab": 1}

    for left in ("2", "1", "0"):
        r = a.post("/chat/stream", json=ASK)
        assert r.status_code == 200 and r.text == "Fake answer.", r.text
        assert r.headers["X-Usage-Text"] == left
    r = a.post("/chat/stream", json=ASK)
    assert r.status_code == 402, r.text

    # Emergency with no questions left: fixed message, not counted, no paywall
    r = a.post("/chat/stream", json=URGENT)
    assert r.status_code == 200 and r.text == EMERGENCY_MESSAGE and r.headers["X-Emergency"] == "1"
    assert a.get("/me").json()["usage"]["text"] == 3

    # Sessions belong to their owner
    b = new_client("ben@example.com")
    sid = b.post("/chat/stream", json=ASK).headers["X-Session-Id"]
    assert a.delete(f"/sessions/{sid}").status_code == 404
    r = a.post("/chat/stream", json=URGENT | {"session_id": sid})
    assert r.headers["X-Session-Id"] != sid
    assert b.delete(f"/sessions/{sid}").status_code == 200

    # Test checkout: Premium removes the limit
    assert a.post("/billing/checkout", json={"plan_id": "nope"}).status_code == 400
    status = a.post("/billing/checkout", json={"plan_id": "weekly"}).json()
    assert status["plan"] == "premium" and status["remaining"] is None
    r = a.post("/chat/stream", json=ASK)
    assert r.status_code == 200 and r.headers["X-Usage-Text"] == "unlimited"

    # The model wrote nothing (all thinking): one more try with only the question
    calls = []

    async def empty_then_answer(**kwargs):
        calls.append(len(kwargs["messages"]))
        async def gen():
            yield FakeChunk("" if len(calls) == 1 else "Second try.")
        return gen()
    main.client.chat = empty_then_answer
    r = a.post("/chat/stream", json=ASK)
    main.client.chat = fake_chat
    assert r.text == "Second try." and len(calls) == 2, (r.text, calls)

    assert a.post("/auth/logout").status_code == 204
    assert a.get("/me").status_code == 401
    print("smoke test OK")


if __name__ == "__main__":
    main_test()
