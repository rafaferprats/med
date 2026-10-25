"""Block E: 1, 3, 5 and 7 people at the same time, with Ollama answering 4 or 8 at once.

Each person does something different, like real people: a clear question, an unsure one (it
goes through the topic classifier), a follow-up in a conversation, a photo or a lab report.
Measures when the first words appear and when everything is done, errors, memory, and that
the free quota is right when one person sends two questions at the same time.
"""
import asyncio
import base64
import io
import json
import statistics
import subprocess
import time
from pathlib import Path

import httpx
from PIL import Image

from common import ROOT, TestServer, log

LEVELS = [1, 3, 5, 7]
PHOTO = None


def photo_bytes() -> bytes:
    global PHOTO
    if PHOTO is None:
        im = Image.open(ROOT / "eval" / "images" / "06_derm_shingles.jpg").convert("RGB")
        im.thumbnail((1600, 1600))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=88)  # what the browser sends after shrinking
        PHOTO = buf.getvalue()
    return PHOTO


async def stream(c: httpx.AsyncClient, path: str, **kw) -> tuple[float, float, str, int]:
    """(first words, total, text, status) of a streamed answer."""
    t0, first, text = time.perf_counter(), None, ""
    async with c.stream("POST", path, **kw) as r:
        async for chunk in r.aiter_text():
            if chunk.strip() and first is None:
                first = time.perf_counter() - t0
            text += chunk
        sid = r.headers.get("X-Session-Id")
    return first or (time.perf_counter() - t0), time.perf_counter() - t0, text, r.status_code, sid


async def person(server: TestServer, n: int, kind: str, start: asyncio.Event) -> dict:
    email = f"carga-{kind}-{n}-{time.time_ns()}@example.com"
    server.make_user(email, premium=True)
    async with httpx.AsyncClient(base_url=server.url, timeout=400) as c:
        await c.post("/auth/login", json={"email": email, "password": "battery-pass-123"})
        await start.wait()
        t0 = time.perf_counter()
        try:
            if kind == "clara":
                first, total, text, status, _ = await stream(c, "/chat/stream", json={"message": "Me duele la garganta y tengo fiebre, ¿qué hago?"})
            elif kind == "dudosa":
                first, total, text, status, _ = await stream(c, "/chat/stream", json={"message": "¿Es malo comer mucho azúcar?"})
            elif kind == "seguimiento":
                _, _, _, _, sid = await stream(c, "/chat/stream", json={"message": "Tengo acidez después de cenar, ¿qué tomo?"})
                # only the follow-up is timed: the first question is a separate wait the person already had
                first, total, text, status, _ = await stream(c, "/chat/stream", json={"message": "¿y si no se me pasa?", "session_id": sid})
            elif kind == "foto":
                first, total, text, status, _ = await stream(
                    c, "/chat/image", data={"message": "¿Qué es esto en mi piel?", "session_id": ""},
                    files={"image": ("foto.jpg", photo_bytes(), "image/jpeg")})
            else:  # lab report
                pdf = (ROOT / "eval" / "labs" / "01_ferropenia_columnas.pdf").read_bytes()
                r = await c.post("/labs/analyze", files={"file": ("a.pdf", pdf, "application/pdf")})
                status = r.status_code
                first = time.perf_counter() - t0  # the table is on screen as soon as this returns
                data = r.json()
                _, _, text, _, _ = await stream(c, f"/labs/{data['lab_id']}/summary", json={"session_id": data["session_id"]})
                total = time.perf_counter() - t0
        except Exception as e:
            return {"kind": kind, "error": repr(e)[:200], "first": None, "total": time.perf_counter() - t0}
    ok = status == 200 and len(text.strip()) > 40
    return {"kind": kind, "first": first, "total": total, "ok": ok, "status": status, "words": len(text.split())}


KINDS = ["clara", "dudosa", "seguimiento", "foto", "pdf", "clara", "dudosa"]


async def wave(server: TestServer, people: int) -> dict:
    start = asyncio.Event()
    tasks = [asyncio.create_task(person(server, i, KINDS[i % len(KINDS)], start)) for i in range(people)]
    await asyncio.sleep(1.0 + 0.3 * people)  # accounts ready
    mem = []

    async def sample():
        while not start.is_set() or not all(t.done() for t in tasks):
            out = subprocess.run(["ps", "-axo", "rss,comm"], capture_output=True, text=True).stdout
            mem.append(sum(int(l.split()[0]) for l in out.splitlines()[1:] if "llama-server" in l or l.strip().endswith("ollama")) / 1024)
            await asyncio.sleep(1)

    sampler = asyncio.create_task(sample())
    start.set()
    results = await asyncio.gather(*tasks)
    sampler.cancel()
    firsts = sorted(r["first"] for r in results if r["first"] is not None)
    totals = sorted(r["total"] for r in results)
    p95 = lambda xs: xs[min(len(xs) - 1, int(round(0.95 * (len(xs) - 1))))] if xs else None  # noqa: E731
    return {"people": people, "results": results,
            "first_median": statistics.median(firsts) if firsts else None, "first_worst": max(firsts) if firsts else None,
            "first_p95": p95(firsts), "total_median": statistics.median(totals), "total_worst": max(totals),
            "errors": sum(1 for r in results if not r.get("ok")), "memory_mb": max(mem) if mem else None}


async def quota_race(server: TestServer) -> dict:
    """One free question left and two requests at the same moment: only one may be answered."""
    email = f"carrera-{time.time_ns()}@example.com"
    server.make_user(email)
    import sqlite3
    with sqlite3.connect(server.dir / "app.db") as db:
        db.execute("UPDATE users SET free_text_used = 3 WHERE email = ?", (email,))
    async with httpx.AsyncClient(base_url=server.url, timeout=300) as c:
        await c.post("/auth/login", json={"email": email, "password": "battery-pass-123"})
        rs = await asyncio.gather(*(c.post("/chat/stream", json={"message": "Me duele la cabeza desde ayer"}) for _ in range(2)))
        me = (await c.get("/me")).json()
    codes = sorted(r.status_code for r in rs)
    return {"codes": codes, "used": me["usage"]["text"], "ok": codes == [200, 402] and me["usage"]["text"] == 4}


def set_ollama_parallel(n: int) -> None:
    """Restarts Ollama with OLLAMA_NUM_PARALLEL=n and loads the model."""
    subprocess.run(["launchctl", "setenv", "OLLAMA_NUM_PARALLEL", str(n)], check=True)
    subprocess.run(["pkill", "-x", "Ollama"])
    subprocess.run(["pkill", "-x", "ollama"])
    time.sleep(3)
    subprocess.run(["open", "-a", "Ollama"])
    for _ in range(120):
        try:
            if httpx.get("http://127.0.0.1:11434/api/version", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.5)
    httpx.post("http://127.0.0.1:11434/api/generate", timeout=180, json={
        "model": "medgemma1.5:4b", "prompt": "hola", "stream": False, "options": {"num_predict": 1, "num_ctx": 8192}})
    out = subprocess.run(["ps", "-axo", "command"], capture_output=True, text=True).stdout
    loaded = [l for l in out.splitlines() if "llama-server" in l]
    log(f"   E: Ollama reiniciado con {n} a la vez ({'-np ' + str(n) if loaded and f'-np {n}' in loaded[0] else 'comprobar'})")


def run_block(server: TestServer, parallel_options=(4, 8)) -> dict:
    t0 = time.time()
    out = {"configs": {}}
    for n in parallel_options:
        set_ollama_parallel(n)
        out["configs"][n] = []
        for people in LEVELS:
            w = asyncio.run(wave(server, people))
            out["configs"][n].append(w)
            log(f"   E: Ollama {n} · {people} personas: primeras palabras mediana {w['first_median']:.1f} s, "
                f"peor {w['first_worst']:.1f} s · completa peor {w['total_worst']:.1f} s · errores {w['errors']}")
    # Keep the best setting: lowest worst first-words time with 7 people
    best = min(parallel_options, key=lambda n: out["configs"][n][-1]["first_worst"])
    set_ollama_parallel(best)
    out["chosen"] = best
    out["quota_race"] = asyncio.run(quota_race(server))
    log(f"E: Ollama queda con {best} a la vez · carrera de cuota: {'bien' if out['quota_race']['ok'] else 'MAL'} "
        f"{out['quota_race']['codes']}")
    out["seconds"] = time.time() - t0
    return out
