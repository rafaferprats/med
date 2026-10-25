"""Prepares each question before it reaches the model.

- The "[Contexto: ...]" line the web interface adds (age, pregnancy, allergies, medicines)
  is taken out of the question and sent as a separate instruction: inside the question,
  the model kept repeating it at the end of its answers.
- Medicines this person must not take (pregnancy, anticoagulants, asthma, children,
  penicillin allergy) are worked out here, in code, and given to the model as a rule:
  the model kept recommending ibuprofen to a pregnant woman and to someone on Sintrom.
- Official CIMA facts about the medicines mentioned go in that same instruction.
- Some questions are answered without the model: medicines that do not exist in Spain,
  and "what is X?" for a real medicine, written from the CIMA facts.
"""
import re
from dataclasses import dataclass, field
from typing import Optional

import guardrails
from medicines import analyse, candidate_names, lookup, unknown_answer
from symptom_meds import medicines_for, suggested_for

CONTEXT_RE = re.compile(r"^\s*\[Contexto:\s*(.*?)\]\s*$")
NSAIDS = "ibuprofeno, naproxeno, dexketoprofeno, diclofenaco y aspirina (ácido acetilsalicílico)"
ANTICOAGULANT_RE = re.compile(
    r"sintrom|acenocumarol|warfarin|apixab|rivaroxab|dabigatr|edoxab|heparin|anticoag|eliquis|xarelto|pradaxa",
    re.I)
# "¿Qué es X?" / "¿Para qué sirve X?" and the same with the name first
INFO_PATTERNS = [
    r"\bqu[eé] es (?:el |la |un |una )?(?P<name>[\w\-]+)",
    r"\bpara qu[eé] (?:sirve|se usa|vale) (?:el |la )?(?P<name>[\w\-]+)",
    r"\bwhat is (?P<name>[\w\-]+)",
    r"(?P<name>[\w\-]+)\s*,?\s*¿?\s*(?:qu[eé] es|para qu[eé] sirve)\s*\??\s*$",
]


@dataclass
class Prepared:
    entry: dict                      # the user message as stored in the conversation
    fixed_answer: Optional[str] = None  # answer written in code, the model is not used
    notes: list[str] = field(default_factory=list)
    suggested: list = field(default_factory=list)  # (medicine, what for) for the symptom (symptom_meds.py)


def split_context(message: str) -> tuple[str, str]:
    """(context text or "", question without the context line)."""
    context, lines = "", []
    for line in message.splitlines():
        m = CONTEXT_RE.match(line)
        if m and not context:
            context = m.group(1)
        else:
            lines.append(line)
    return context, "\n".join(lines).strip()


def restrictions_for(context: str) -> list[str]:
    """Rules about medicines this person must not take, worked out from the context."""
    c = context.lower()
    rules = []
    if "embarazada" in c:
        rules.append(f"Está embarazada: NO puede tomar {NSAIDS}.")
    if ANTICOAGULANT_RE.search(c):
        rules.append(f"Toma un anticoagulante: NO puede tomar {NSAIDS}, por riesgo de sangrado.")
    if "asma" in c:
        rules.append(f"Tiene asma: evita recomendarle {NSAIDS}.")
    age = re.search(r"(\d+)\s*años", c)
    if "hijo" in c or (age and int(age.group(1)) < 16):
        rules.append("Es un niño o niña: NO puede tomar aspirina (ácido acetilsalicílico).")
    if re.search(r"alergia a [^·]*penicil", c):
        rules.append("Tiene alergia a la penicilina: NO puede tomar amoxicilina ni ninguna penicilina.")
    return rules


def info_answer(name: str, info: dict) -> str:
    """'What is X?' for a real medicine, written only from the official CIMA facts."""
    rx = {True: "Necesita receta médica.",
          False: "Se puede comprar sin receta.",
          None: "Según la presentación, puede necesitar receta o no."}[info["prescription"]]
    group = f" Pertenece al grupo de los **{info['group'].lower()}**." if info["group"] else ""
    return (
        f"**{name.capitalize()}** contiene **{info['active'].lower()}**.{group} {rx}\n\n"
        f"Esta información viene de **CIMA**, la base oficial de medicamentos de la AEMPS. "
        f"Para saber por qué te lo han indicado a ti, cómo tomarlo y sus contraindicaciones, "
        f"mira el prospecto o pregunta a tu médico o farmacéutico."
    )


def info_question_name(question: str) -> Optional[str]:
    names = {n.lower() for n in candidate_names(question)}
    for pattern in INFO_PATTERNS:
        m = re.search(pattern, question, re.IGNORECASE)
        if m and m.group("name").lower() in names:
            return m.group("name")
    return None


def prepare(message: str, in_conversation: bool = False) -> Prepared:
    context, question = split_context(message)
    # Dangerous or off-topic requests get a fixed answer (guardrails.py)
    rule = guardrails.check(question, in_conversation)
    if rule:
        return Prepared(entry={"role": "user", "content": question}, fixed_answer=rule[1])
    facts, unknown = analyse(message)
    if unknown:
        return Prepared(entry={"role": "user", "content": question}, fixed_answer=unknown_answer(unknown))
    name = info_question_name(question)
    if name:
        info = lookup(name)
        # Only when the question is just "what is X?" (a short question about one medicine)
        if info and len(question.split()) <= 8:
            return Prepared(entry={"role": "user", "content": question},
                            fixed_answer=info_answer(name, info))
    notes = []
    if context:
        notes.append(f"Datos de la persona: {context}. Úsalos para adaptar la respuesta, sin repetirlos.")
        notes += restrictions_for(context)
        if len(notes) > 1:
            notes.append("No pongas esos medicamentos en la tabla de Medicación: menciónalos solo en 'Evitar:'.")
    # The right over-the-counter medicines for the symptom (symptom_meds.py)
    meds_note = medicines_for(question, context or "")
    if meds_note:
        notes.append(meds_note)
    if facts:
        notes.append("Datos oficiales de CIMA (AEMPS), úsalos en lugar de tu memoria y no los repitas tal cual:\n"
                     + facts.replace("[Datos oficiales de CIMA (AEMPS)", "-").replace(". Explica qué es usando "
                                                                                     "solo estos datos, aunque creas saber otra cosa.]", "."))
    entry = {"role": "user", "content": question}
    if notes:
        entry["notes"] = "\n".join(notes)
    return Prepared(entry=entry, notes=notes, suggested=suggested_for(question, context or ""))
