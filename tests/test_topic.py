"""The assistant only answers health questions (topic.py and main.on_topic).

Runs without Ollama (the model is faked):
    ./venv/bin/python tests/test_topic.py
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.update({"DB_PATH": str(Path(tempfile.mkdtemp()) / "test.db"), "QUESTIONS_PER_MINUTE": "1000",
                   "SIGNUPS_PER_HOUR": "1000"})
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
import medicines  # noqa: E402
import topic  # noqa: E402

medicines.load_index()
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))


HEALTH = [
    "Me duele la cabeza desde ayer", "¿Puedo tomar ibuprofeno con paracetamol?", "Tengo 38 de fiebre",
    "¿Qué significa LDL 160?", "¿Cuántas horas debo dormir?", "Estoy muy triste últimamente",
    "¿Qué es el Sintrom?", "Me pica todo el cuerpo", "Tengo una uña encarnada", "Mi bebé no para de llorar y tiene mocos",
    "¿Es normal sangrar en el embarazo?", "Mi perro me ha mordido la mano", "¿Cada cuánto me vacuno de la gripe?",
    "Hola, me duele la garganta", "¿La ferritina baja da cansancio?", "Me cuesta respirar al subir escaleras",
    "¿Qué dieta es buena para el colesterol?", "Tengo ansiedad antes de los exámenes",
]
OTHER = [
    "Escríbeme un programa en Python", "¿Quién ganó el mundial de 2010?", "¿Cuál es la capital de Francia?",
    "Recomiéndame una película", "Escríbeme un poema sobre la diabetes", "¿Cómo hago una tortilla de patatas?",
    "Dame ideas para el cumpleaños de mi mamá", "¿Cuánto es 345 por 12?", "¿Qué opinas de Messi?",
    "Recomiéndame un libro", "¿Qué tiempo hace mañana?", "Dime un chiste", "Ayúdame con mi currículum",
    "¿Qué coche es más fiable?", "Organízame un viaje a Roma", "Hazme un resumen de la Segunda Guerra Mundial",
    "¿Cómo invierto mis ahorros?", "¿Qué es la inteligencia artificial?",
]
# Mixed or unclear: never decided as health by the rules alone
# Battery 25/10: these waited for the model classifier (10-20 s more with several people)
for q in ["¿Es malo comer mucho azúcar?", "¿Es bueno beber agua con limón?", "¿es malo saltarse el desayuno?"]:
    HEALTH.append(q)
UNSURE = ["Me torcí el tobillo jugando al fútbol", "Resuelve este ejercicio de matemáticas", "¿Es bueno el café?",
          "¿Y con miel?", "¿Qué significa efímero?"]


def test_rules():
    for q in HEALTH:
        check(f"salud: {q}", topic.classify(q)[0] == "health", topic.classify(q))
    for q in OTHER:
        check(f"otro tema: {q}", topic.classify(q)[0] == "other", topic.classify(q))
    for q in UNSURE:
        check(f"dudosa (decide el modelo): {q}", topic.classify(q)[0] == "unsure", topic.classify(q))
    check("saludo: respuesta fija", topic.classify("¡Hola!") == ("other", topic.GREETING))
    check("gracias: respuesta fija", topic.classify("Muchas gracias por todo") == ("other", topic.THANKS))
    check("mascota: al veterinario", topic.classify("Mi perro ha comido chocolate") == ("other", topic.VET))
    check("mascota con medicamento: al veterinario", topic.classify("¿Puedo darle ibuprofeno a mi gato?") == ("other", topic.VET))
    check("seguimiento corto en una conversación de salud", topic.classify("¿Y con miel?", True)[0] == "health")
    check("seguimiento de otro tema en una conversación de salud", topic.classify("¿Y quién ganó la liga?", True)[0] == "other")
    check("seguimiento largo no se da por bueno",
          topic.classify("Ahora cuéntame algo sobre la historia de los romanos y su imperio en Hispania antigua", True)[0] != "health")
    import guardrails
    check("receta de cocina: no es la regla de recetas médicas", guardrails.check("Dame una receta de lentejas") is None)
    check("receta de antibióticos: sí lo es", (guardrails.check("Dame una receta de antibióticos") or [""])[0] == "prescription")
    check("receta de cocina: otro tema", topic.classify("Dame una receta de lentejas")[0] == "other")
    check("receta para una dieta de salud: salud", topic.classify("Dame una receta sin gluten para un celíaco")[0] == "health")
    check("receta para diabéticos: salud", topic.classify("¿Me das una receta de postre para diabéticos?")[0] == "health")
    for m in ["cada cuanto debo tomarlo?", "¿cuántas veces al día?", "ok", "vale", "¿ibuprofeno?", "🤒"]:
        check(f"no es un mensaje sin sentido: {m}", not guardrails.is_gibberish(m))
    for m in ["asdkjh qwe", "asdf", "qwerty", "aaaaaa", "¿¿??", "hfjdks"]:
        check(f"sí es un mensaje sin sentido: {m}", guardrails.is_gibberish(m))
    check("en una conversación, nada se corta como sin sentido", guardrails.check("asdf", in_conversation=True) is None)
    history = [{"role": "user", "content": "x"}, {"role": "assistant", "content": topic.OFF_TOPIC}]
    check("una negativa no cuenta como conversación de salud", not topic.health_conversation(history))
    history.append({"role": "assistant", "content": "**Resumen:** ..."})
    check("una respuesta del modelo sí", topic.health_conversation(history))


# ---------- API, with a fake model ----------

class FakeChunk:
    def __init__(self, text):
        self.message = type("M", (), {"content": text})()


state = {"verdict": "SALUD", "fail": False, "classified": [], "answered": 0}


async def fake_chat(**kwargs):
    if kwargs.get("stream"):
        state["answered"] += 1

        async def gen():
            yield FakeChunk("**Resumen:** Respuesta de salud.")
        return gen()
    state["classified"].append(kwargs["messages"][-1]["content"])
    if state["fail"]:
        raise RuntimeError("Ollama caído")
    return FakeChunk(state["verdict"])


main.client.chat = fake_chat


def ask(c, text, sid=None):
    return c.post("/chat/stream", json={"message": text, "session_id": sid})


def test_api():
    c = TestClient(main.app)
    assert c.post("/auth/register", json={"email": "topic@example.com", "password": "password123"}).status_code == 204
    left = lambda: c.get("/me").json()["remaining"]["text"]  # noqa: E731

    state["classified"].clear()
    r = ask(c, "¿Cuál es la capital de Francia?")
    check("API: otro tema, respuesta fija", r.status_code == 200 and "Solo puedo ayudarte" in r.text and r.headers.get("X-Fixed-Answer") == "1")
    check("API: otro tema no gasta pregunta", left() == 4)
    check("API: lo claro no llama al clasificador", not state["classified"])

    r = ask(c, "Me duele la cabeza")
    check("API: salud responde el modelo", "Respuesta de salud" in r.text and left() == 3)
    sid = r.headers["X-Session-Id"]

    state["verdict"] = "OTRO"
    state["classified"].clear()
    r = ask(c, "¿Qué significa efímero?")
    check("API: dudosa, el modelo dice OTRO: no se responde", "Solo puedo ayudarte" in r.text and state["classified"] == ["¿Qué significa efímero?"])
    check("API: y no gasta pregunta", left() == 3)

    state["verdict"] = "SALUD"
    r = ask(c, "Me torcí el tobillo jugando al fútbol")
    check("API: dudosa, el modelo dice SALUD: responde", "Respuesta de salud" in r.text)

    state["classified"].clear()
    r = ask(c, "¿Y con miel?", sid)
    check("API: seguimiento corto en la conversación: responde sin clasificar",
          "Respuesta de salud" in r.text and not state["classified"])

    state["fail"] = True
    r = ask(c, "¿Es bueno el café?")
    check("API: si el clasificador falla, la pregunta pasa", "Respuesta de salud" in r.text)
    state["fail"] = False

    r = ask(c, "Me duele mucho el pecho y se me va al brazo izquierdo")
    check("API: las urgencias se responden siempre", r.headers.get("X-Emergency") == "1")

    # The conversation of the screenshot, with its own account (the free questions above are used)
    f = TestClient(main.app)
    assert f.post("/auth/register", json={"email": "topic2@example.com", "password": "password123"}).status_code == 204
    first = ask(f, "Tengo agujetas después de ir al gym, ¿qué puedo tomar?")
    r = ask(f, "cada cuanto debo tomarlo?", first.headers["X-Session-Id"])
    check("API: seguimiento de la captura ('cada cuanto debo tomarlo?'): responde el modelo",
          "Respuesta de salud" in r.text and "No he entendido" not in r.text)
    history = main.sessions[first.headers["X-Session-Id"]]["history"]
    check("API: el seguimiento lleva la conversación anterior",
          len(history) == 4 and history[0]["content"].startswith("Tengo agujetas"))
    check("API: la lista de medicamentos para la web viaja en la respuesta", "Paracetamol" in
          __import__("urllib.parse").parse.unquote(first.headers.get("X-Suggested-Meds", "")))

    guest = TestClient(main.app)
    r = ask(guest, "Recomiéndame una película")
    check("API visitante: otro tema, respuesta fija", "Solo puedo ayudarte" in r.text)
    r = ask(guest, "Me duele la garganta")
    check("API visitante: el otro tema no gastó su pregunta gratis", r.status_code == 200 and "Respuesta de salud" in r.text)
    r = ask(guest, "Hola")
    check("API visitante: un saludo tiene respuesta fija", r.status_code == 200 and "asistente de salud" in r.text)


if __name__ == "__main__":
    test_rules()
    test_api()
    failed = [(n, d) for n, ok, d in results if not ok]
    for n, d in failed:
        print(f"FALLA: {n}\n       {d}")
    print(f"topic tests: {len(results) - len(failed)}/{len(results)} OK")
    sys.exit(1 if failed else 0)
