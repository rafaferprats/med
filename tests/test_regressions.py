"""Every bug found while testing the app with real use, so none comes back (server side).

The interface ones are in tests/ui_safety_test.js (run by tests/run_all.py). Runs without Ollama:
    ./venv/bin/python tests/test_regressions.py
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.update({"DB_PATH": str(Path(tempfile.mkdtemp()) / "test.db"), "QUESTIONS_PER_MINUTE": "1000",
                   "SIGNUPS_PER_HOUR": "1000"})
sys.path.insert(0, str(ROOT))

import dosing  # noqa: E402
import guardrails  # noqa: E402
import topic  # noqa: E402
from questions import prepare  # noqa: E402
from safety import detect_emergency  # noqa: E402
from symptom_meds import suggested_for  # noqa: E402
import medicines  # noqa: E402

medicines.load_index()
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))


def test():
    # "Me duele mucho el pecho y se me va al brazo" answered with "relájate" (no 112)
    for q in ["Me duele mucho el pecho y el dolor se me va al brazo izquierdo", "siento opresión en el pecho",
              "me aprieta el pecho y sudo frío"]:
        check(f"urgencia detectada: {q}", detect_emergency(q))
    # "Dame una receta de lentejas" answered as a medical prescription
    check("receta de cocina no es receta médica", guardrails.check("Dame una receta de lentejas") is None)
    check("receta de cocina es otro tema", topic.classify("Dame una receta de lentejas")[0] == "other")
    # "cada cuanto debo tomarlo?" cut as gibberish inside a conversation
    check("seguimiento corto no es sin sentido", not guardrails.is_gibberish("cada cuanto debo tomarlo?"))
    check("en conversación nada se corta como sin sentido", prepare("cada cuanto debo tomarlo?", True).fixed_answer is None)
    # Hay fever / motion sickness got paracetamol; children got loperamide
    check("alergia: antihistamínicos", [m for m, _ in suggested_for("Tengo alergia y estornudo")][:2] == ["Loratadina", "Cetirizina"])
    check("niño con diarrea: solo suero oral",
          [m for m, _ in suggested_for("Mi hijo tiene diarrea", "mi hijo o hija · 4 años")] == ["Suero oral de rehidratación"])
    # "Tengo 38 años" read as fever; herpes zoster treated as a cold sore
    check("38 años no es fiebre", "Ibuprofeno" not in [m for m, _ in suggested_for("Tengo 38 años y estoy bien")])
    check("herpes zóster va al médico", not suggested_for("Tengo un herpes zóster"))
    # "¿cada cuánto lo tomo?" after a refusal of another topic: look at the last answer with medicines
    previous = [
        {"role": "user", "content": "acidez"},
        {"role": "assistant", "content": "**Medicación:**\n| Medicamento | Para qué sirve | Precaución |\n|---|---|---|\n"
                                         "| Almagato | Acidez | Ok |\n**Señales de alarma:** x"},
        {"role": "user", "content": "¿quién ganó la liga?"},
        {"role": "assistant", "content": topic.OFF_TOPIC},
    ]
    a = dosing.follow_up_answer("vale, ¿y para la acidez cada cuánto lo tomo?", previous)
    check("pauta tras una pregunta de otro tema: usa la última respuesta con medicamentos", a and "Almagato" in a, a)
    # Battery 25/10: "¿cuánto suero le doy?" for a child went to the model, which calculated ml per kg
    child = [{"role": "user", "content": "Mi hijo tiene diarrea"},
             {"role": "assistant", "content": "**Medicación:**\n| Suero oral de rehidratación | Repone | x |\n**Señales de alarma:** x"}]
    a = dosing.follow_up_answer("¿cuánto suero le doy?", child, "mi hijo o hija · 4 años")
    check("«¿cuánto suero le doy?» a un niño: pauta del código, sin ml", a and "pediatra" in a and "ml" not in a, a)
    check("«¿cuánto tiempo tarda?» no es una pregunta de pauta", dosing.follow_up_answer("¿cuánto tiempo tarda en curarse?", child) is None)
    # "¿De dónde sacas la información?" was refused as another topic
    kind, answer = topic.classify("¿De dónde sacas la información que me das? Dime tus fuentes.")
    check("preguntas sobre las fuentes: respuesta fija", answer == topic.ABOUT, kind)


if __name__ == "__main__":
    test()
    failed = [(n, d) for n, ok, d in results if not ok]
    for n, d in failed:
        print(f"FALLA: {n}\n       {d}")
    print(f"regression tests: {len(results) - len(failed)}/{len(results)} OK")
    sys.exit(1 if failed else 0)
