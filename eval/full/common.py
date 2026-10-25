"""Shared pieces of the full test battery (eval/full/run_full.py).

- TestServer: a separate copy of the app on another port, with a temporary database and
  generous limits, so the battery never touches the real data or the server people use.
  It uses the real Ollama.
- Browser: headless Chrome driven over the DevTools protocol, to render answers with the
  real static/app.js and to walk through the app like a person.
"""
import asyncio
import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import websockets

ROOT = Path(__file__).resolve().parents[2]
PYTHON = str(ROOT / "venv" / "bin" / "python")
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
RESULTS = ROOT / "eval" / "results"


def free_port(preferred: int) -> int:
    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", preferred)) != 0:
            return preferred
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestServer:
    """uvicorn main:app on its own port and database (no --reload)."""

    def __init__(self, port: int = 8001, **env):
        self.port = free_port(port)
        self.dir = Path(tempfile.mkdtemp(prefix="medapp-battery-"))
        self.env = {**os.environ, "DB_PATH": str(self.dir / "app.db"), "SIGNUPS_PER_HOUR": "100000",
                    "QUESTIONS_PER_MINUTE": "100000", "LAB_UPLOADS_PER_HOUR": "100000", **env}
        self.proc = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self):
        # The medicines index is copied so the server does not download it again
        (self.dir).mkdir(exist_ok=True)
        index = ROOT / "data" / "cima_index.json"
        if index.exists():
            shutil.copy(index, self.dir / "cima_index.json")
        self.proc = subprocess.Popen(
            [PYTHON, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(self.port), "--log-level", "warning"],
            cwd=ROOT, env=self.env, stdout=subprocess.DEVNULL, stderr=open(self.dir / "stderr.log", "w"))
        for _ in range(120):
            try:
                if httpx.get(f"{self.url}/health", timeout=1).status_code == 200:
                    return self
            except httpx.HTTPError:
                time.sleep(0.25)
        raise RuntimeError(f"test server did not start: {(self.dir / 'stderr.log').read_text()[-800:]}")

    def __exit__(self, *exc):
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def make_user(self, email: str, premium: bool = False) -> None:
        """Creates an account straight in the test database."""
        code = (f"import auth, sqlite3; auth.create_user({email!r}, 'battery-pass-123')\n"
                + (f"c = sqlite3.connect({str(self.dir / 'app.db')!r}); c.execute(\"UPDATE users SET plan='premium', "
                   f"premium_until='2099-01-01T00:00:00+00:00' WHERE email=?\", ({email!r},)); c.commit()" if premium else ""))
        subprocess.run([PYTHON, "-c", code], cwd=ROOT, env=self.env, check=True)

    def client(self, email: str, timeout: float = 300) -> httpx.Client:
        c = httpx.Client(base_url=self.url, timeout=timeout)
        r = c.post("/auth/login", json={"email": email, "password": "battery-pass-123"})
        r.raise_for_status()
        return c


class Browser:
    """Headless Chrome over the DevTools protocol."""

    def __init__(self, width: int = 1280, height: int = 800, scale: float = 1, mobile: bool = False):
        self.size = (width, height, scale, mobile)
        self.profile = Path(tempfile.mkdtemp(prefix="medapp-chrome-"))
        self.port = free_port(9340)
        self.proc = self.ws = None
        self.n = 0
        self.console: list[str] = []

    async def __aenter__(self):
        self.proc = subprocess.Popen(
            [CHROME, "--headless=new", f"--remote-debugging-port={self.port}", f"--user-data-dir={self.profile}",
             "--hide-scrollbars", "--no-first-run", "--no-default-browser-check", "--mute-audio", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(80):
            try:
                targets = httpx.get(f"http://127.0.0.1:{self.port}/json", timeout=1).json()
                break
            except Exception:
                await asyncio.sleep(0.25)
        target = next(t for t in targets if t["type"] == "page")
        self.ws = await websockets.connect(target["webSocketDebuggerUrl"], max_size=100_000_000)
        await self.cmd("Page.enable")
        await self.cmd("Runtime.enable")
        await self.cmd("Emulation.setEmulatedMedia", features=[{"name": "prefers-color-scheme", "value": "light"}])
        await self.resize(*self.size)
        return self

    async def __aexit__(self, *exc):
        if self.ws:
            await self.ws.close()
        if self.proc:
            self.proc.terminate()
        shutil.rmtree(self.profile, ignore_errors=True)

    async def cmd(self, method: str, **params):
        self.n += 1
        my = self.n
        await self.ws.send(json.dumps({"id": my, "method": method, "params": params}))
        while True:
            msg = json.loads(await self.ws.recv())
            if msg.get("method") == "Runtime.consoleAPICalled" and msg["params"]["type"] in ("error", "warning"):
                self.console.append(" ".join(str(a.get("value", a.get("description", ""))) for a in msg["params"]["args"]))
            if msg.get("method") == "Runtime.exceptionThrown":
                self.console.append("EXCEPTION " + json.dumps(msg["params"]["exceptionDetails"])[:300])
            if msg.get("id") == my:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    async def js(self, code: str, timeout: float = 300):
        r = await self.cmd("Runtime.evaluate", expression=f"(async () => {{ {code} }})()", awaitPromise=True,
                           returnByValue=True, timeout=int(timeout * 1000))
        if r.get("exceptionDetails"):
            raise RuntimeError(json.dumps(r["exceptionDetails"])[:600])
        return r.get("result", {}).get("value")

    async def resize(self, width, height, scale=1, mobile=False):
        await self.cmd("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=scale, mobile=mobile)

    async def goto(self, url: str):
        await self.cmd("Page.navigate", url=url)
        await asyncio.sleep(1.2)
        await self.js("await document.fonts.ready; for (let i = 0; i < 50 && !window.DOSING?.length && typeof DOSING !== 'undefined' && !DOSING.length; i++) await new Promise(r => setTimeout(r, 100)); return 1;")

    async def screenshot(self, path: Path, quality: int = 70) -> str:
        data = (await self.cmd("Page.captureScreenshot", format="jpeg", quality=quality))["data"]
        path.write_bytes(base64.b64decode(data))
        return data


def run(coro):
    return asyncio.run(coro)


def log(msg: str) -> None:
    print(msg, flush=True)
    sys.stdout.flush()
