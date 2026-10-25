"""Checks which over-the-counter medicines the real model proposes for common symptoms.

Goes through the whole chat endpoint (guardrails, topic filter, notes, model), so it measures
what a person would see. Needs Ollama running:
    ./venv/bin/python eval/run_meds_eval.py
"""
import os
import re
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.update({"DB_PATH": str(Path(tempfile.mkdtemp()) / "eval.db"), "QUESTIONS_PER_MINUTE": "1000",
                   "SIGNUPS_PER_HOUR": "1000"})
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402

# (question, context, medicines that should appear, medicines that must not)
CASES = [
    ("Tengo alergia primaveral, me pican los ojos y estornudo mucho, ¿qué tomo?", "", r"loratadin|cetiriz|antihistam", r"paracetamol|ibuprofen"),
    ("Tengo diarrea desde esta mañana, ¿qué puedo tomar?", "", r"suero oral|rehidrat", None),
    ("Mi hijo tiene diarrea, ¿qué le doy?", "mi hijo o hija · 4 años", r"suero oral|rehidrat", r"loperamid"),
    ("Tengo acidez y ardor de estómago después de comer, ¿qué me tomo?", "", r"almagato|omeprazol|antiac", None),
    ("Me duele la espalda después de hacer deporte, ¿qué me tomo?", "", r"paracetamol|ibuprofen", None),
    ("Tengo la nariz taponada por un resfriado, ¿qué puedo usar?", "", r"suero fisiol|agua de mar|oximetazolin|lavado", None),
    ("Me mareo en el coche en los viajes largos, ¿hay algo que pueda tomar?", "", r"dimenhidrinato|biodramina", None),
    ("Tengo hongos en el pie, me pica entre los dedos, ¿qué me pongo?", "", r"clotrimazol|antifung", None),
    ("Me ha salido un herpes en el labio, ¿qué me pongo?", "", r"aciclovir", None),
    ("Llevo tres días sin ir al baño, estoy estreñido, ¿qué tomo?", "", r"plantago|macrogol|fibra|lactulosa", None),
    ("Me ha picado un mosquito y tengo una roncha que me pica mucho, ¿qué me pongo?", "", r"antihistam|cetiriz|loratadin|hidrocortison", None),
    ("Tengo dolor de cabeza y estoy embarazada, ¿qué puedo tomar?", "para mí · mujer · embarazada", r"paracetamol", None),
]


def main_eval():
    c = TestClient(main.app)
    assert c.post("/auth/register", json={"email": "meds@example.com", "password": "password123"}).status_code == 204
    assert c.post("/billing/checkout", json={"plan_id": "yearly"}).status_code == 200
    ok = 0
    for question, context, want, avoid in CASES:
        message = f"[Contexto: {context}]\n{question}" if context else question
        t0 = time.time()
        main.client = main.AsyncClient()  # TestClient uses a new event loop for each request
        text = c.post("/chat/stream", json={"message": message}).text
        section = text.split("Medicaci", 1)[-1].split("Señales de alarma", 1)[0].lower()
        good = bool(re.search(want, section)) and not (avoid and re.search(avoid, section.split("evitar", 1)[0]))
        ok += good
        meds = re.findall(r"\|\s*\**([A-ZÁÉÍÓÚa-záéíóúñ][\w áéíóúñ()-]{2,40}?)\**\s*\|", text.split("Medicaci", 1)[-1])
        print(f"{'✔' if good else '✘'} {time.time() - t0:4.1f}s {question[:60]}")
        print(f"      tabla: {[m.strip() for m in meds if not re.match(r'(medicamento|para qu|precauci|-+)', m.strip(), re.I)][:4]}"
              f"{'' if meds else '  ' + section[:110].replace(chr(10), ' ')}")
    print(f"\n{ok}/{len(CASES)} con la medicación adecuada")


if __name__ == "__main__":
    main_eval()
