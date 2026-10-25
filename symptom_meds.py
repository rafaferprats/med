"""Over-the-counter medicines that fit each common symptom, written in code.

The model chose badly on its own: paracetamol and ibuprofen for hay fever or motion sickness,
"no hace falta medicación" for diarrhoea, back pain or a cold sore. When a question matches a
symptom here, the model gets the right options as an instruction (questions.prepare), like the
official CIMA facts. Safety rules still apply afterwards: pregnancy, children, anticoagulants,
asthma and allergies (questions.restrictions_for and static/app.js).

Only non-prescription medicines sold in Spanish pharmacies. Must be reviewed by a pharmacist
before real use.
"""
import re
import unicodedata
from typing import Optional


def fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn")


# (name, words that identify the symptom, [(medicine, what it is for)], extra note or "")
SYMPTOMS = [
    ("alergia (rinitis, picor de ojos, estornudos)",
     r"alergi\w*|rinitis|estornud\w*|polen|pican? (los )?ojos|picor de ojos|fiebre del heno",
     [("Loratadina", "Antihistamínico: alivia estornudos, picor y mocos de la alergia, casi sin dar sueño"),
      ("Cetirizina", "Antihistamínico: alivia los síntomas de la alergia; puede dar algo de sueño"),
      ("Suero fisiológico", "Lavados nasales para arrastrar el polen")],
     "Paracetamol e ibuprofeno no sirven para la alergia: no los pongas."),
    ("diarrea",
     r"diarrea\w*|gastroenteritis|descomposicion|heces (liquidas|blandas)|cagalera",
     [("Suero oral de rehidratación", "Repone el agua y las sales perdidas: es lo más importante"),
      ("Loperamida", "Reduce las deposiciones en adultos, solo si no hay fiebre alta ni sangre")],
     "En niños y en embarazo, solo suero oral (nada de loperamida)."),
    ("estreñimiento",
     r"estren\w*|no (puedo )?ir al bano|no voy al bano|sin ir al bano|no hago de vientre",
     [("Plantago ovata", "Fibra que ablanda las heces y facilita ir al baño"),
      ("Macrogol", "Laxante suave que retiene agua en el intestino")],
     "Recuerda también beber agua y comer fibra."),
    ("acidez o ardor de estómago",
     r"acidez|ardor (de estomago|en el estomago)|reflujo|me repite|pirosis|indigestion",
     [("Almagato", "Antiácido: neutraliza el ácido y alivia el ardor en minutos"),
      ("Omeprazol", "Reduce la producción de ácido si la acidez se repite")],
     ""),
    ("dolor de cabeza, de espalda, muscular, de muelas o de regla",
     r"(dolor|duelen?|dolorid\w*) (de |en )?(el |la |los |las |un |una |mi |mis )?(cabeza|espalda|muelas?|regla|"
     r"rodillas?|tobillos?|hombros?|cuello|munecas?|codos?|piernas?|brazos?|lumbares?|musculos?|cervicales|caderas?)|"
     r"migrana|jaqueca|lumbago|contractura|agujetas|dolor muscular|me duelen? (los musculos|la regla)|"
     r"dolor menstrual|tiron|esguince|torcedura|golpe",
     [("Paracetamol", "Alivia el dolor"),
      ("Ibuprofeno", "Alivia el dolor y la inflamación; tomar con comida")],
     ""),
    ("fiebre",
     r"fiebre|febricula|temperatura alta|3[89]([.,]\d)? ?(grados|º)",
     [("Paracetamol", "Baja la fiebre y alivia el malestar"),
      ("Ibuprofeno", "Baja la fiebre y la inflamación; tomar con comida")],
     ""),
    ("congestión nasal o resfriado",
     r"nariz (taponada|tapada|congestionada)|congestion nasal|mocos|resfriad\w*|catarro|constipad\w*",
     [("Suero fisiológico", "Lavados nasales para despejar la nariz"),
      ("Oximetazolina", "Spray descongestionante; como mucho 3 días seguidos"),
      ("Paracetamol", "Alivia el malestar y la fiebre del resfriado")],
     ""),
    ("dolor de garganta",
     r"(dolor de|duele la|me pica la|irritacion de) garganta|faringitis|afonia",
     [("Paracetamol", "Alivia el dolor de garganta"),
      ("Ibuprofeno", "Alivia el dolor y la inflamación; tomar con comida"),
      ("Pastillas para chupar para la garganta", "Calman la irritación")],
     "No hacen falta antibióticos salvo que lo diga el médico."),
    ("mareo en los viajes",
     r"mare\w* (en|al ir en) (el |los )?(coche|barco|avion|autobus|viaje\w*)|cinetosis|mareo del viaje",
     [("Dimenhidrinato", "Previene el mareo en los viajes; tomar media hora antes; da sueño")],
     "No conduzcas si lo tomas."),
    ("hongos en los pies o la piel",
     r"hongos?|pie de atleta|tina|candidiasis cutanea",
     [("Clotrimazol crema", "Antifúngico: elimina los hongos de la piel; aplicar 2 veces al día varias semanas")],
     ""),
    ("herpes labial (calentura)",
     r"herpes (labial|en el labio)|calentura|pupa en el labio",  # not shingles (herpes zóster): doctor
     [("Aciclovir crema", "Antiviral: acorta el brote si se aplica desde el primer picor")],
     ""),
    ("picaduras o ronchas que pican",
     r"picadura\w*|me ha picado (un|una)|roncha\w*|urticaria|habon\w*",
     [("Cetirizina", "Antihistamínico oral: calma el picor"),
      ("Hidrocortisona crema", "Corticoide suave: reduce la inflamación y el picor de la zona")],
     ""),
]
_COMPILED = [(name, re.compile(rf"\b({words})\b"), meds, note) for name, words, meds, note in SYMPTOMS]


# Medicines left out of the list for some people (the safety rules would cross them out anyway)
NSAID = re.compile(r"ibuprofeno|naproxeno|aspirina|diclofenaco|dexketoprofeno", re.I)
NOT_FOR = [
    (re.compile(r"embarazad|lactancia"), re.compile(r"ibuprofeno|loperamida|oximetazolina|dimenhidrinato", re.I)),
    (re.compile(r"hijo|hija|nino|nina|bebe|\b([0-9]|1[01]) anos"),
     re.compile(r"loperamida|oximetazolina|omeprazol|almagato|dimenhidrinato", re.I)),
    (re.compile(r"sintrom|acenocumarol|warfarin|anticoag|apixaban|rivaroxaban|asma"), NSAID),
]


def _matches(question: str, context: str) -> list[tuple[str, list, str]]:
    """(symptom, medicines this person can take, note) for the main symptoms of the question."""
    plain, ctx = fold(question), fold(context)
    banned = [rx for who, rx in NOT_FOR if who.search(ctx)]
    out = []
    for name, rx, meds, note in _COMPILED:
        if rx.search(plain):
            meds = [(m, why) for m, why in meds if not any(b.search(m) for b in banned)]
            if meds:
                out.append((name, meds, note))
    return out[:2]  # two symptoms at most: the main ones


def suggested_for(question: str, context: str = "") -> list[tuple[str, str]]:
    """The medicines (name, what for) to show if the model writes no table (static/app.js)."""
    seen, out = set(), []
    for _, meds, _ in _matches(question, context):
        for m, why in meds:
            if m not in seen:
                seen.add(m)
                out.append((m, why))
    return out


def medicines_for(question: str, context: str = "") -> Optional[str]:
    """Instruction for the model with the right medicines for the symptoms in the question,
    leaving out the ones this person must not take (pregnancy, children, anticoagulants, asthma)."""
    hits = _matches(question, context)
    if not hits:
        return None
    parts = []
    for name, meds, note in hits:
        listed = "; ".join(f"{m} ({why})" for m, why in meds)
        parts.append(f"Para {name}, los medicamentos sin receta adecuados son: {listed}. {note}".strip())
    if not parts:
        return None
    return ("En la tabla de Medicación pon estos medicamentos (los que encajen con la persona), no otros, "
            "y no escribas 'No hace falta medicación'. " + " ".join(parts))
