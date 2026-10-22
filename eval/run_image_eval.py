"""Runs the image evaluation prompts against the local model and writes a report.

Images go through the same pipeline as the app (prepare_image + build_messages).

Usage (from the project folder, with Ollama running):
    ./venv/bin/python eval/run_image_eval.py
Re-score saved answers with the current checks (no model calls):
    ./venv/bin/python eval/run_image_eval.py --rescore eval/results/image_eval_XXXX.json
"""
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ollama import Client  # noqa: E402

from main import MODEL, OPTIONS, build_messages, clean, prepare_image  # noqa: E402
from questions import prepare  # noqa: E402
from run_eval import ENGLISH_WORDS, write_html  # noqa: E402

EVAL_DIR = Path(__file__).resolve().parent
IMG_DIR = EVAL_DIR / "images"
OUT_DIR = EVAL_DIR / "results"

BBOX_RE = re.compile(r"\[\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*,"
                     r"\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*\]")
NEGATION_RE = re.compile(r"\b(no|not|without|absence of|negative for|rule out|ruled out|sin|ausencia de|descarta|descartar)\b")
# A clear "go now" recommendation, not a generic "seek attention if..." warning
EMERGENCY_STRICT = ["urgencias", "112", "emergencias", "ambulancia",
                    "emergency department", "emergency room", "call 911", "ambulance"]
EMERGENCY_WINDOW = 400  # the advice must come at the start of the answer
MIN_IOU = 0.3
APP_URGENT_RE = re.compile(
    r"neumot[oó]rax|hemorragia|hematoma|sangrado|fractura|ictus|infarto|derrame|\bmasa\b|tumor|"
    r"neoplasi|disecci[oó]n|perforaci[oó]n|obstrucci[oó]n|trombo|embolia|aneurisma|edema pulmonar", re.I)  # the reference boxes are hand-drawn estimates, so be tolerant
LAB_VERDICTS = {
    "high": r"\b(above|high|elevated|exceeds?|higher|por encima|alt[oa]|elevad[oa]|superior)\b",
    "low": r"\b(below|low|decreased|lower|por debajo|baj[oa]|inferior)\b",
    "normal": r"\b(within|normal range|in range|is normal|dentro|normal)\b",
    # Spanish answers often just say "fuera de rango", without saying high or low
    "out": r"\b(fuera (?:de|del) (?:rango|intervalo)|outside the (?:reference )?range)\b",
}


def mentioned(low, kw):
    """True if kw appears at least once without a negation earlier in its sentence."""
    for m in re.finditer(re.escape(kw), low):
        before = re.split(r"[.\n]", low[max(0, m.start() - 60):m.start()])[-1]
        if not NEGATION_RE.search(before):
            return True
    return False


def iou(a, b):
    y0, x0 = max(a[0], b[0]), max(a[1], b[1])
    y1, x1 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, y1 - y0) * max(0, x1 - x0)
    area = lambda r: max(0, r[2] - r[0]) * max(0, r[3] - r[1])  # noqa: E731
    union = area(a) + area(b) - inter
    return inter / union if union else 0.0


def lab_verdicts(low, truth):
    """Reads the model's verdict for each test from the line that names only that test."""
    names = [n for n, _ in truth]
    patterns = {n: re.compile(rf"\b(?:{n})" + (r"(?!.*a1c)" if n == "hemoglobin" else ""))
                for n in names}
    found = {}
    for line in low.splitlines():
        hits = [n for n in names if patterns[n].search(line)]
        if len(hits) != 1 or hits[0] in found:
            continue
        kinds = [k for k, rx in LAB_VERDICTS.items() if re.search(rx, line)]
        if kinds:
            found[hits[0]] = kinds[0] if len(kinds) == 1 else "ambiguous"
    return found


def checks(item, answer):
    low = answer.lower()
    words = re.findall(r"[a-záéíóúñ]+", low)
    n_words = len(words)
    en_ratio = sum(w in ENGLISH_WORDS for w in words) / max(n_words, 1)
    res = {
        "words": n_words,
        "spanish": en_ratio < 0.03,
        "no_think_leak": "<unused" not in answer,
    }
    if "expect_any" in item:
        res["finding"] = any(mentioned(low, k) for k in item["expect_any"])
    if "expect_none" in item:
        wrong = [k for k in item["expect_none"] if mentioned(low, k)]
        res["no_wrong_finding"] = not wrong
        res["wrong_terms"] = wrong
    if item.get("emergency"):
        res["emergency_advice"] = any(w in low[:EMERGENCY_WINDOW] for w in EMERGENCY_STRICT)
        # The banner the web interface adds from code (URGENT_IMAGE_RE in static/app.js)
        res["app_warns"] = bool(APP_URGENT_RE.search(answer))
    if item.get("expect_bbox"):
        m = BBOX_RE.search(answer)
        score = iou([float(v) for v in m.groups()], item["expect_bbox"]) if m else 0.0
        res["iou"] = round(score, 2)
        res["bbox"] = score >= MIN_IOU
    if "expect_all" in item:
        # Spanish uses a decimal comma: 7,1 instead of 7.1
        res["values_read"] = all(v in answer or v.replace(".", ",") in answer for v in item["expect_all"])
    if "lab_truth" in item:
        got = lab_verdicts(low, item["lab_truth"])
        # "fuera de rango" counts as right for a high or low value
        wrong = [n for n, v in item["lab_truth"]
                 if not (got.get(n) == v or (got.get(n) == "out" and v in ("high", "low")))]
        res["ranges_correct"] = not wrong
        res["wrong_ranges"] = {n: got.get(n, "missing") for n in wrong}
    return res


def rescore(path):
    """Re-applies the current checks to answers saved by an earlier run."""
    path = Path(path)
    prompts = {p["id"]: p for p in
               json.loads((EVAL_DIR / "image_prompts.json").read_text(encoding="utf-8"))}
    data = json.loads(path.read_text(encoding="utf-8"))
    for r in data["results"]:
        c = checks(prompts[r["id"]], r["answer"]) if r["answer"] else {}
        auto = [k for k, v in c.items() if isinstance(v, bool)]
        r["checks"] = c
        r["auto_score"] = f"{sum(c[k] for k in auto)}/{len(auto)}"
        print(f"[{r['id']:>2}] {r['auto_score']:>5}  "
              f"fail: {[k for k in auto if not c[k]]}")
    out = path.with_name(path.stem + "_rescored")
    out.with_suffix(".json").write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
    write_html(out.with_suffix(".html"), data["results"], path.stem + " (rescored)")
    print(f"\nSaved:\n  {out.with_suffix('.html')}\n  {out.with_suffix('.json')}")


def main():
    prompts = json.loads((EVAL_DIR / "image_prompts.json").read_text(encoding="utf-8"))
    missing = [p["image"] for p in prompts if not (IMG_DIR / p["image"]).exists()]
    if missing:
        sys.exit(f"Missing images in {IMG_DIR}: {', '.join(sorted(set(missing)))}")
    client = Client()
    OUT_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    rows = []
    for item in prompts:
        print(f"[{item['id']:>2}/{len(prompts)}] {item['image']}: {item['prompt'][:50]}...", flush=True)
        image = prepare_image((IMG_DIR / item["image"]).read_bytes())
        history = [{**prepare(item["prompt"]).entry, "images": [image]}]
        t0 = time.time()
        try:
            resp = client.chat(model=MODEL, messages=build_messages(history), options=OPTIONS)
            raw = resp.message.content
            answer = clean(raw)
            error = ""
        except Exception as e:  # keep going if one prompt fails
            raw, answer, error = "", "", repr(e)
        secs = round(time.time() - t0, 1)
        c = checks(item, answer) if answer else {}
        auto = [k for k, v in c.items() if isinstance(v, bool)]
        passed = sum(c[k] for k in auto)
        rows.append({**item, "category": f"{item['category']} · {item['image']}",
                     "expect": item["category"], "answer": answer, "raw_len": len(raw),
                     "seconds": secs, "error": error, "checks": c,
                     "auto_score": f"{passed}/{len(auto)}"})
        print(f"     {secs}s  auto {passed}/{len(auto)}  {error}", flush=True)

    json_path = OUT_DIR / f"image_eval_{stamp}.json"
    json_path.write_text(json.dumps({"model": MODEL, "options": OPTIONS, "results": rows},
                                    ensure_ascii=False, indent=2), encoding="utf-8")
    html_path = OUT_DIR / f"image_eval_{stamp}.html"
    write_html(html_path, rows, stamp)
    print(f"\nSaved:\n  {html_path}\n  {json_path}")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--rescore":
        rescore(sys.argv[2])
    else:
        main()
