"""Over-the-counter medicines for each symptom (symptom_meds.py), filtered for each person.

    ./venv/bin/python tests/test_symptom_meds.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from questions import prepare  # noqa: E402
from symptom_meds import medicines_for, suggested_for  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))


def names(question, context=""):
    return [m for m, _ in suggested_for(question, context)]


CASES = [
    ("Tengo alergia primaveral y estornudo mucho", "", ["Loratadina", "Cetirizina"], ["Paracetamol", "Ibuprofeno"]),
    ("Tengo diarrea desde esta mañana", "", ["Suero oral de rehidratación", "Loperamida"], []),
    ("Tengo acidez después de comer", "", ["Almagato", "Omeprazol"], []),
    ("Me duele la rodilla al subir escaleras", "", ["Paracetamol", "Ibuprofeno"], []),
    ("Me mareo en el coche", "", ["Dimenhidrinato"], []),
    ("Tengo hongos en el pie", "", ["Clotrimazol crema"], []),
    ("Me ha salido un herpes en el labio", "", ["Aciclovir crema"], []),
    ("Estoy estreñido", "", ["Plantago ovata", "Macrogol"], []),
    ("Me ha picado un mosquito", "", ["Cetirizina", "Hidrocortisona crema"], []),
    ("Tengo fiebre de 38,5 grados", "", ["Paracetamol", "Ibuprofeno"], []),
    # Filtered for the person
    ("Mi hijo tiene diarrea", "mi hijo o hija · 4 años", ["Suero oral de rehidratación"], ["Loperamida"]),
    ("Me duele la cabeza", "para mí · mujer · embarazada", ["Paracetamol"], ["Ibuprofeno"]),
    ("Me duele la espalda", "para mí · 70 años · toma o tiene: Sintrom", ["Paracetamol"], ["Ibuprofeno"]),
    ("Me duele la cabeza", "para mí · toma o tiene: asma", ["Paracetamol"], ["Ibuprofeno"]),
    ("Tengo la nariz taponada", "mi hijo o hija · 3 años", ["Suero fisiológico"], ["Oximetazolina"]),
]


def test():
    for q, ctx, want, avoid in CASES:
        got = names(q, ctx)
        check(f"{q} [{ctx or 'adulto'}]", all(w in got for w in want) and not any(a in got for a in avoid), got)
    check("'38 años' no es fiebre", "fiebre" not in (medicines_for("Tengo 38 años y me duele la espalda") or ""))
    check("herpes zóster: sin crema (es para el médico)", not names("Tengo un herpes zóster en el costado"))
    check("pregunta sin síntoma: sin lista", medicines_for("¿Qué es el colesterol?") is None)
    prep = prepare("[Contexto: mi hijo o hija · 4 años]\nMi hijo tiene diarrea, ¿qué le doy?")
    check("prepare: la instrucción llega al modelo", "Suero oral" in (prep.entry.get("notes") or ""))
    check("prepare: la lista llega a la web", [m for m, _ in prep.suggested] == ["Suero oral de rehidratación"])


if __name__ == "__main__":
    test()
    failed = [(n, d) for n, ok, d in results if not ok]
    for n, d in failed:
        print(f"FALLA: {n}\n       {d}")
    print(f"symptom meds tests: {len(results) - len(failed)}/{len(results)} OK")
    sys.exit(1 if failed else 0)
