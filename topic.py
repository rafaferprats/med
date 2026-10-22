"""Keeps the assistant on health topics: anything else gets a fixed answer, without the model.

Layers, cheapest first (the decision is made by the first one that is sure):
1. classify(): rules in code. Greetings and thanks get a short fixed answer; pets go to the vet;
   clear health vocabulary (symptoms, body, medicines from CIMA, lab tests, pregnancy, babies,
   mental health, sleep, diet...) is health; clear other topics (programming, homework, sports,
   films, politics, money, travel, cooking, general knowledge, jokes and poems...) are not.
   Short follow-ups in a conversation that is already about health ("¿y con miel?") pass.
2. Anything still unsure is classified by the model with a one-word question (main.py).
3. The system prompts also tell the model to answer only health questions (main.py).
"""
import re
import unicodedata
from typing import Optional

import medicines
from lab_tests import ANALYTES

OFF_TOPIC = (
    "Solo puedo ayudarte con **dudas de salud**: síntomas, medicamentos, analíticas, embarazo, "
    "niños, prevención o qué hacer en una situación concreta. Cuéntame qué te pasa y te ayudo."
)
GREETING = (
    "¡Hola! Soy tu asistente de salud. Cuéntame qué te pasa: qué síntomas notas, desde cuándo, "
    "o pregúntame por un medicamento o por tu analítica."
)
THANKS = "¡De nada! Si tienes cualquier otra duda de salud, aquí estoy. Y si empeoras, consulta a tu médico."
VET = (
    "Solo puedo ayudarte con la salud de las **personas**. Para tu mascota, consulta a un **veterinario**: "
    "si ha comido algo que puede ser tóxico (chocolate, uvas, medicamentos, xilitol…) o está muy decaída, "
    "llama ya a una clínica veterinaria de urgencias. No le des medicamentos para personas."
)

# The model, for the messages the rules can't decide (main.py)
CLASSIFIER_PROMPT = (
    "Classify the user's message. Answer with one word only: SALUD if it is about human health, "
    "medicine, symptoms, the body, medicines, diet or habits for health, pregnancy, babies, mental "
    "health, lab tests or medical images; OTRO for anything else (including pets)."
)
FOLLOW_UP_MAX_WORDS = 12


def fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn")


HEALTH = re.compile(
    r"\b(dol[oe]r\w*|duel[ea]n?|molest\w*|fiebre|febr\w*|tos|toso|mocos?|congest\w*|estornud\w*|pic[ao]r?|picores?|"
    r"escozor|escuece|ardor|nause\w*|vomit\w*|diarrea|estrenid\w*|mare\w*|vertigo\w*|cansanc\w*|fatiga\w*|"
    r"debilidad|hinch\w*|inflam\w*|sangr\w*|herid\w*|quemad\w*|ampolla\w*|granos?|acne|erupcion\w*|ronchas?|"
    r"alergi\w*|asma\w*|ahog\w*|respir\w*|palpitacion\w*|taquicard\w*|tension arterial|tension alta|"
    r"tension baja|hipertens\w*|colesterol|glucosa|diabet\w*|obesidad|sobrepeso|adelgaz\w*|dieta|nutricion\w*|"
    r"vitamin\w*|hierro|anemia|embaraz\w*|embarazada|regla|menstrua\w*|ovulacion|anticonceptiv\w*|"
    r"preservativo|pildora|bebes?|lactancia|pediatra|vacun\w*|infecc\w*|virus|bacteria\w*|gripe|covid|"
    r"resfriad\w*|catarro|garganta|oidos?|ojos?|vista|piel|pelo|cabello|uneros?|encarnad\w*|hongos?|dientes?|muelas?|encias?|"
    r"estomago|barriga|tripa|intestin\w*|higado|rinon\w*|corazon|pulmon\w*|cabeza|migran\w*|jaqueca|"
    r"espalda|lumbar\w*|cuello|hombros?|rodillas?|tobillos?|pies?|manos?|munecas?|brazos?|piernas?|"
    r"huesos?|fractur\w*|esguince\w*|torc\w*|musculo\w*|muscular|articulacion\w*|artrosis|tendinitis|cancer|"
    r"tumor\w*|bultos?|quistes?|ansiedad|estres|depresi\w*|triste\w*|tristeza|animo|apatic\w*|insomnio|dormir|"
    r"duermo|sueno|nervios|panico|suicid\w*|autolesi\w*|medic\w*|pastillas?|farmac\w*|receta|dosis|"
    r"prospecto|antibiotic\w*|analgesic\w*|antiinflamatori\w*|jarabe|crema|pomada|inyeccion\w*|cirugia|"
    r"operacion|operar\w*|hospital\w*|urgencias|centro de salud|ambulatorio|enfermer\w*|doctor\w*|"
    r"analitica\w*|analisis|radiograf\w*|ecograf\w*|resonancia|salud|sano|saludable|ejercicio|hidrat\w*|"
    r"alcohol|tabaco|fumar|fumo|drogas?|resaca|sintomas?|enfermedad\w*|enfermo|enferma|tratamiento|"
    r"diagnostic\w*|curar|prevenir|chequeo|ginecolog\w*|urolog\w*|dermatolog\w*|cardiolog\w*|psicolog\w*|"
    r"psiquiatr\w*|fisio\w*|dentista|oftalmolog\w*|otorrino|primeros auxilios|orina|orinar|heces|flemas?|"
    r"sudor\w*|picadura|mordedura|mordido|aranazo|veneno|intoxic\w*|sobredosis|desmay\w*|convulsion\w*|"
    r"pecho|senos?|prostata|testicul\w*|vagina\w*|pene|hemorroid\w*|varices|cicatri\w*|"
    r"pain|hurt|fever|sick|cough|headache|doctor|medicine|pregnan\w*)\b")
OTHER = re.compile(
    r"\b(programa(cion|r)?|codigo|python|javascript|java|html|sql|excel|powerpoint|ordenador|portatil|"
    r"wifi|videojuegos?|playstation|xbox|nintendo|futbol|baloncesto|tenis|liga|champions|mundial|"
    r"partidos? de|messi|ronaldo|real madrid|barca|peliculas?|series?|netflix|actor|actriz|cancion\w*|"
    r"musica|cantante|libros?|novelas?|poemas?|poesia|cuentos?|chistes?|adivinanzas?|acertijos?|guerra|"
    r"presidente|politic\w*|elecciones|gobierno|votar|abogad\w*|multa|hacienda|impuestos?|renta|dinero|"
    r"invertir|inversion\w*|bolsa|acciones|bitcoin|cripto\w*|hipoteca|banco|prestamo|coches?|motos?|"
    r"mecanico|viajes?|vuelos?|hotel\w*|vacaciones|turismo|capital de|paises|idiomas?|traduce|traduccion|"
    r"traducir|gramatica|ortografia|matematicas?|ecuacion\w*|calcula|cuanto es|sumar?|restar?|multiplica\w*|"
    r"deberes|redaccion|ensayo|resumen de|receta de|cocinar|tortilla|paella|horno|tiempo hace|horoscopo|"
    r"zodiaco|tarot|religion|biblia|planetas?|dinosaurios?|regalos?|cumpleanos|boda|ligar|ropa|moda|"
    r"maquillaje|decoracion|muebles?|jardin|empresa|negocio|marketing|curriculum|entrevista de trabajo|"
    r"que hora es|ruedas?|movil|rios?|ahorros|ajedrez|habitantes|correo|jefe|romanos|relatividad|"
    r"inteligencia artificial|agujero negro|universo|quijote|escritor|significa la palabra|tabla dinamica)\b")
TASK = re.compile(r"\b(escribe\w*|redacta\w*|hazme|inventa\w*|cuentame un|dime un) .{0,30}\b(poema|cancion|cuento|"
                  r"historia|redaccion|ensayo|carta|codigo|programa|chiste|resumen|trabajo)")
PET = re.compile(r"\b(perr[oa]s?|perrit[oa]s?|cachorr\w*|gat[oa]s?|gatit[oa]s?|mascotas?|conejo|hamster|"
                 r"caballo|loro|periquito|tortuga|veterinari\w*)\b")
HUMAN_BITE = re.compile(r"\b(me|le|nos|te) (ha|han) (mordido|aranado)|mordedura|aranazo|me ha picado")
GREETING_RE = re.compile(r"^(hola|buenas|buenos dias|buenas tardes|buenas noches|hey|hi|hello|que tal)[\s!.¡,]*"
                         r"(que tal|como estas)?[\s!?.¿]*$")
THANKS_RE = re.compile(r"^(muchas )?(gracias|grcias|thanks|thank you|vale gracias|ok gracias|perfecto gracias)"
                       r"[\s!.,]*(por todo|por la ayuda)?[\s!.]*$")
# Cooking recipes are not health questions, unless they are for an illness or a diet
COOKING = re.compile(r"\breceta(s)? (de|para)\b(?! (un |una |el |la )?(medicamento|medicina|antibiotic|pastilla|"
                     r"farmac|ibuprofeno|paracetamol|jarabe))|\b(cocinar|guisar|guiso|al horno|ingredientes)\b")
DIET_CONDITION = re.compile(r"\b(diabet\w*|celiac\w*|sin gluten|colesterol|hipertens\w*|tension|embaraz\w*|"
                            r"anemia|renal|rinon|intoleran\w*|alergi\w*|sin sal|sin lactosa|lactancia|bebe)\b")
_ANALYTE_WORDS = {w for a in ANALYTES for alias in a["aliases"] for w in [fold(alias)] if len(w) >= 4}
# Short lab test names, as people write them ("¿qué significa LDL 160?")
LAB_ACRONYMS = re.compile(r"\b(ldl|hdl|tsh|pcr|vsg|vcm|hcm|chcm|inr|psa|hba1c|t3|t4|b12|ige|gpt|got|ggt|ast|alt)\b")


def _mentions_medicine_or_test(plain: str) -> bool:
    words = set(re.findall(r"[a-z0-9]+", plain))
    if LAB_ACRONYMS.search(plain) or any(w in medicines._index for w in words):
        return True
    return any(alias in plain for alias in _ANALYTE_WORDS if " " in alias) or bool(words & _ANALYTE_WORDS)


def classify(question: str, health_conversation: bool = False) -> tuple[str, Optional[str]]:
    """("health" | "other" | "unsure", fixed answer or None)."""
    plain = fold(question).strip()
    bare = re.sub(r"^[\W_]+", "", plain)  # "¡Hola!" -> "hola!"
    if GREETING_RE.match(bare):
        return "other", GREETING
    if THANKS_RE.match(bare):
        return "other", THANKS
    if PET.search(plain) and not HUMAN_BITE.search(plain):
        return "other", VET
    if COOKING.search(plain):
        return ("health", None) if DIET_CONDITION.search(plain) else ("other", OFF_TOPIC)
    health = bool(HEALTH.search(plain)) or _mentions_medicine_or_test(plain)
    other = bool(OTHER.search(plain) or TASK.search(plain))
    if health and other:
        # "me torcí el tobillo jugando al fútbol" (health) or "resuelve este ejercicio de
        # matemáticas" (not): the model decides
        return ("other", OFF_TOPIC) if TASK.search(plain) else ("unsure", None)
    if health:
        return "health", None
    if other:
        return "other", OFF_TOPIC
    if health_conversation and len(plain.split()) <= FOLLOW_UP_MAX_WORDS:
        return "health", None  # "¿y con miel?" after a health answer
    return "unsure", None


def health_conversation(history: list[dict]) -> bool:
    """True if the model has already answered something in this conversation (only health
    questions reach it), so short follow-ups can pass."""
    fixed = {OFF_TOPIC, GREETING, THANKS, VET}
    return any(m.get("role") == "assistant" and m.get("content") not in fixed for m in history)
