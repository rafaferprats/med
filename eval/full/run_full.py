"""The full battery with the real model (about 1-1.5 hours), and its HTML report.

    ./venv/bin/python eval/full/run_full.py              # everything
    ./venv/bin/python eval/full/run_full.py A B          # some blocks

A safety on real answers · B conversations · C the app in Chrome · D strange output ·
E several people at once (restarts Ollama with 4 and 8 answers at a time) · F/G fast suites.
Results: eval/results/battery_<date>.html and .json
"""
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import a_safety  # noqa: E402
import b_conversations  # noqa: E402
import c_e2e  # noqa: E402
import d_fuzz  # noqa: E402
import e_load  # noqa: E402
from common import RESULTS, ROOT, TestServer, log  # noqa: E402
from report import write_report  # noqa: E402


def fast_suites() -> dict:
    p = subprocess.run([sys.executable, "-W", "ignore", str(ROOT / "tests" / "run_all.py")], cwd=ROOT,
                       capture_output=True, text=True)
    lines = [l for l in p.stdout.splitlines() if l.startswith(("✔", "✘"))]
    return {"ok": p.returncode == 0, "lines": lines, "summary": p.stdout.strip().splitlines()[-1] if p.stdout.strip() else ""}


def main(blocks: set) -> None:
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = {"stamp": stamp, "started": time.time()}
    json_path = RESULTS / f"battery_{stamp}.json"

    def save():
        json_path.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str))
        write_report(out, RESULTS / f"battery_{stamp}.html")

    if "G" in blocks:
        log("F/G: tests rápidos…")
        out["fast"] = fast_suites()
        log(f"F/G: {out['fast']['summary']}")
        save()
    def block(name, fn, *args):
        try:
            out[name] = fn(*args)
        except Exception as e:  # recorded in the report; the other blocks still run
            import traceback
            out.setdefault("errors", {})[name] = traceback.format_exc()[-1500:]
            log(f"{name}: ERROR {e!r}")
        save()

    with TestServer() as server:
        log(f"Servidor de pruebas en {server.url} (base de datos temporal)")
        if "A" in blocks:
            block("A", a_safety.run_block, server)
        if "B" in blocks:
            block("B", b_conversations.run_block, server)
        if "C" in blocks:
            block("C", c_e2e.run_block, server)
        if "D" in blocks:
            real = [c["text"] for c in out.get("A", {}).get("cases", []) if c.get("status") == 200]
            block("D", d_fuzz.run_block, server, real)
        if "E" in blocks:
            block("E", e_load.run_block, server)
    out["seconds"] = time.time() - out["started"]
    save()
    log(f"Listo en {out['seconds'] / 60:.0f} min · eval/results/battery_{stamp}.html")


if __name__ == "__main__":
    main(set(sys.argv[1:]) or {"A", "B", "C", "D", "E", "G"})
