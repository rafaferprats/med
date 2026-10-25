"""Follow-up questions about how to take a medicine, answered from code (dosing.py).

    ./venv/bin/python tests/test_dosing.py
"""
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.update({"DB_PATH": str(Path(tempfile.mkdtemp()) / "test.db"), "QUESTIONS_PER_MINUTE": "1000",
                   "SIGNUPS_PER_HOUR": "1000"})
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

import dosing  # noqa: E402
import main  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))


PREVIOUS = [
    {"role": "user", "content": "Tengo agujetas"},
    {"role": "assistant", "content": "**Resumen:** x\n**Medicación:**\n| Medicamento | Para qué sirve | Precaución |\n"
                                     "|---|---|---|\n| Paracetamol | Dolor | Ok |\n| Ibuprofeno | Dolor | Con comida |\n"
                                     "Evitar: aspirina.\n**Señales de alarma:** x"},
]


def test_rules():
    a = dosing.follow_up_answer("cada cuanto debo tomarlo?", PREVIOUS)
    check("pauta de los medicamentos de la respuesta anterior", a and "cada 6–8 horas" in a and "1.200 mg" in a)
    check("no da pauta de lo que había que evitar", a and "Aspirina" not in a)
    for q in ["¿Cuántas veces al día?", "¿antes o después de comer?", "¿cómo lo tomo?", "¿cuántos días lo tomo?"]:
        check(f"reconoce la pregunta: {q}", dosing.follow_up_answer(q, PREVIOUS) is not None)
    for q in ["¿y es grave?", "¿cuándo voy al médico?", "gracias"]:
        check(f"no es de pautas: {q}", dosing.follow_up_answer(q, PREVIOUS) is None)
    check("sin conversación previa: no responde el código", dosing.follow_up_answer("¿cada cuánto lo tomo?", []) is None)
    a = dosing.follow_up_answer("¿cada cuánto tomo el omeprazol?", PREVIOUS)
    check("medicamento nombrado en la pregunta", a and "Omeprazol" in a and "Paracetamol" not in a)
    a = dosing.follow_up_answer("¿cada cuánto se lo doy?", PREVIOUS, "mi hijo o hija · 4 años")
    check("niño: sin dosis, según el peso", a and "según el peso" in a and not re.search(r"\d+ ?mg", a))
    a = dosing.follow_up_answer("¿cada cuánto lo tomo?", PREVIOUS, "para mí · mujer · embarazada")
    check("embarazo: ibuprofeno desaconsejado", a and "mejor no lo tomes" in a and "embarazo" in a)
    check("embarazo: paracetamol, preguntar antes", a and "pregunta antes a tu médico" in a)
    a = dosing.follow_up_answer("¿cada cuánto lo tomo?", PREVIOUS, "para mí · toma o tiene: Sintrom")
    check("Sintrom: paracetamol con pauta, ibuprofeno no", a and "6–8 horas" in a and "anticoagulantes" in a)
    shared = json.loads((ROOT / "static" / "dosing.json").read_text())
    check("la web y el servidor usan las mismas pautas", len(shared) == len(dosing.DOSING) and len(shared) >= 19)


class FakeChunk:
    def __init__(self, text):
        self.message = type("M", (), {"content": text})()


async def fake_chat(**kwargs):
    if kwargs.get("stream"):
        async def gen():
            yield FakeChunk("**Resumen:** Agujetas.\n**Medicación:**\n| Medicamento | Para qué sirve | Precaución |\n"
                            "|---|---|---|\n| Paracetamol | Dolor | Ok |\n**Señales de alarma:** x")
        return gen()
    return FakeChunk("SALUD")


def test_api():
    main.client.chat = fake_chat
    c = TestClient(main.app)
    assert c.post("/auth/register", json={"email": "dosing@example.com", "password": "password123"}).status_code == 204
    first = c.post("/chat/stream", json={"message": "Tengo agujetas después del gym, ¿qué tomo?"})
    sid = first.headers["X-Session-Id"]
    left = c.get("/me").json()["remaining"]["text"]
    r = c.post("/chat/stream", json={"message": "cada cuanto debo tomarlo?", "session_id": sid})
    check("API: el seguimiento responde con la pauta revisada", "1 comprimido de 500 mg a 1 g cada 6–8 horas" in r.text, r.text[:120])
    check("API: marcada como respuesta fija (la web conserva las dosis)", r.headers.get("X-Fixed-Answer") == "1")
    check("API: no gasta pregunta", c.get("/me").json()["remaining"]["text"] == left)
    check("API: queda en la conversación", main.sessions[sid]["history"][-1]["content"].startswith("**Cómo se toma:**"))


if __name__ == "__main__":
    test_rules()
    test_api()
    failed = [(n, d) for n, ok, d in results if not ok]
    for n, d in failed:
        print(f"FALLA: {n}\n       {d}")
    print(f"dosing tests: {len(results) - len(failed)}/{len(results)} OK")
    sys.exit(1 if failed else 0)
