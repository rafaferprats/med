"""All the fast tests in one command (no Ollama needed, about 1-2 minutes):

    ./venv/bin/python tests/run_all.py

1. Every Python suite in tests/.
2. The interface tests (tests/ui_safety_test.js) in headless Chrome, with the real static/app.js.
3. Strange model output (eval/full/d_fuzz.py, built-in examples): no crash, no injected code.

Run it before every commit. The slow battery with the real model is eval/full/run_full.py.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval" / "full"))

SUITES = ["smoke_test", "test_regressions", "test_safety", "test_topic", "test_symptom_meds", "test_dosing",
          "test_billing", "test_security", "test_labs", "test_pdf_guard"]


def python_suites() -> list[tuple[str, bool, str]]:
    out = []
    for name in SUITES:
        t0 = time.time()
        p = subprocess.run([sys.executable, "-W", "ignore", str(ROOT / "tests" / f"{name}.py")], cwd=ROOT,
                           capture_output=True, text=True)
        lines = [l for l in (p.stdout + p.stderr).splitlines() if l.strip() and "EOF marker" not in l and not l.startswith("[")]
        summary = lines[-1] if lines else "(sin salida)"
        failures = [l for l in lines if l.startswith("FALLA") or l.startswith("FAIL")]
        out.append((name, p.returncode == 0, f"{summary} ({time.time() - t0:.0f} s)" + ("\n    " + "\n    ".join(failures[:8]) if failures else "")))
    return out


def browser_suites() -> list[tuple[str, bool, str]]:
    import asyncio

    from common import Browser, TestServer
    import d_fuzz

    async def ui(server):
        async with Browser() as b:
            await b.goto(server.url)
            # The file is a commented expression "(() => {...})();": its value is the result
            code = (ROOT / "tests" / "ui_safety_test.js").read_text().strip().rstrip(";")
            r = await b.cmd("Runtime.evaluate", expression=code, returnByValue=True)
            return r.get("result", {}).get("value")

    with TestServer(port=8021) as server:
        res = asyncio.run(ui(server))
        out = [("ui_safety_test", not res["failures"], f"{res['passed']}/{res['total']} OK"
                + ("\n    " + "\n    ".join(res["failures"]) if res["failures"] else ""))]
        fz = d_fuzz.run_block(server)
        bad = fz["crashes"] + fz["xss"] + sum(fz["violations"].values())
        out.append(("ui_fuzz", bad == 0, f"{fz['total']} variantes · {fz['crashes']} errores · {fz['xss']} inyecciones · "
                    f"avisos {json.dumps(fz['violations'], ensure_ascii=False)}"))
    return out


if __name__ == "__main__":
    t0 = time.time()
    results = python_suites() + browser_suites()
    for name, ok, detail in results:
        print(f"{'✔' if ok else '✘'} {name}: {detail}")
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} suites OK en {time.time() - t0:.0f} s")
    sys.exit(1 if failed else 0)
