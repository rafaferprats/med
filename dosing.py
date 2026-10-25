"""Follow-up questions about how to take a medicine, answered from code.

"¿Cada cuánto debo tomarlo?" after an answer that suggested paracetamol got a vague answer
from the model, which must not give doses (it gave wrong ones in testing). Here the medicines
are found in the question or in the previous answer, and each gets the reviewed text from
static/dosing.json, the same one the medication table shows (static/app.js). The same rules as
the table apply: no doses for children, "ask first" in pregnancy, and a warning instead of a
dose for what this person must not take.
"""
import json
import re
import unicodedata
from pathlib import Path
from typing import Optional

DOSING = [(d["name"], re.compile(d["match"], re.I), d["how"])
          for d in json.loads((Path(__file__).parent / "static" / "dosing.json").read_text(encoding="utf-8"))]


def fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn")


# "¿Cada cuánto debo tomarlo?", "¿cuántas veces al día?", "¿antes o después de comer?"...
HOW_TO_TAKE = re.compile(
    r"cada cuant[ao]s?|cuantas veces|cuant[ao]s? (comprimidos?|pastillas?|capsulas?|sobres?|mg|gotas)|que dosis|"
    r"cuanto (debo|puedo|le|me|se) (tomar|tomo|doy|dar|toma)|cuanto (tomo|le doy|me tomo)|"
    r"cuant[ao]s? [a-z]+ (le |me |se )?(doy|dar|damos|tomo|tomar|toma|pongo|echo)\b|"  # "¿cuánto suero le doy?"
    r"como (lo |la |los |las |me lo |me la |se lo |se la |se |le )?(tomo|toma|tomar|tomarlo|tomarla|tomarlos|doy|dar|"
    r"aplico|aplica|pongo|uso|usar)\b|cuantos dias|durante cuanto tiempo|hasta cuando|antes o despues de (comer|las comidas)|"
    r"con o sin comida|en ayunas|a que hora|cuanto tiempo (lo|la) tomo")

NSAID = re.compile(r"ibuprofen|naproxen|aspirin|acetilsalic|dexketoprofen|diclofenac", re.I)
ANTICOAG = re.compile(r"sintrom|acenocumarol|warfarin|apixaban|rivaroxaban|dabigatran|edoxaban|heparin|anticoag")


def _person(context: str) -> dict:
    ctx = fold(context)
    age = re.search(r"(\d+) anos", ctx)
    return {
        "child": "hijo o hija" in ctx or bool(age and int(age.group(1)) < 16),
        "pregnant": "embarazada" in ctx,
        "breastfeeding": "lactancia" in ctx,
        "anticoag": bool(ANTICOAG.search(ctx)),
        "asthma": "asma" in ctx,
        "penicillin": "penicil" in ctx,
    }


def _risk(name: str, p: dict) -> Optional[str]:
    """Why this person must not take it (the same rules as medicationRisks in static/app.js)."""
    if p["penicillin"] and re.search(r"amoxicil|penicil|ampicil|cloxacil", name, re.I):
        return "Tienes alergia a la penicilina: puede causarte una reacción grave."
    if NSAID.search(name):
        if p["anticoag"]:
            return "No tomar con anticoagulantes: riesgo de sangrado."
        if p["pregnant"]:
            return "No recomendado en el embarazo, sobre todo desde la semana 20."
        if p["asthma"]:
            return "Puede provocar crisis de asma en algunas personas."
    if p["child"] and re.search(r"aspirin|acetilsalic", name, re.I):
        return "Nunca dar aspirina a niños: riesgo de síndrome de Reye."
    return None


def _medicines(question: str, previous: list[dict]) -> list[tuple[str, str]]:
    """(name, how) of the medicines asked about: named in the question, or else in the
    medication section of the last answer (not the ones under "Evitar:")."""
    found = [(n, how) for n, rx, how in DOSING if rx.search(question)]
    if found:
        return found
    # The last answer that mentions medicines (the very last one may be "only health topics")
    for message in reversed(previous):
        if message.get("role") != "assistant":
            continue
        text = message["content"]
        section = re.split(r"Señales de alarma", text.split("Medicaci", 1)[-1], maxsplit=1)[0]
        section = "\n".join(line for line in section.splitlines() if not re.match(r"\W*(Evitar|Avoid)\W*:", line, re.I))
        found = [(n, how) for n, rx, how in DOSING if rx.search(section)][:4]
        if found:
            return found
    return []


def follow_up_answer(question: str, previous: list[dict], context: str = "") -> Optional[str]:
    """The answer to "¿cómo / cada cuánto lo tomo?" in a conversation, or None to let the model answer."""
    if not previous or not HOW_TO_TAKE.search(fold(question)):
        return None
    meds = _medicines(question, previous)
    if not meds:
        return None
    p = _person(context)
    lines = []
    for name, how in meds:
        risk = _risk(name, p)
        if risk:
            text = f"mejor no lo tomes. {risk} Consulta a tu médico o farmacéutico."
        elif p["child"]:
            text = "según el peso: sigue el prospecto o pregunta al pediatra o farmacéutico."
        elif p["pregnant"] or p["breastfeeding"]:
            text = "pregunta antes a tu médico o farmacéutico, que te dirá la dosis y cada cuánto."
        else:
            text = how
        lines.append(f"- **{name}:** {text}")
    if p["child"]:
        footer = ("En niños la dosis depende del **peso** y de la presentación (jarabe, sobres, supositorios): "
                  "mira la tabla del prospecto o pregunta en la farmacia. No le des medicamentos de adultos.")
    else:
        footer = ("Son pautas orientativas para adultos con medicamentos sin receta: comprueba siempre el "
                  "prospecto de tu envase, porque la dosis cambia según la presentación. Si en 3 días no "
                  "mejoras o empeoras, consulta a tu médico.")
    return "**Cómo se toma:**\n" + "\n".join(lines) + "\n\n" + footer
