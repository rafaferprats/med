"""Rule-based protections applied before the model.

Testing with unusual questions showed the model going off the rails: it recommended a
steroid cycle, wrote a child's dose in mg, told someone to give a chocolate bar back to a
dog, answered "Italia" to who won the 2010 World Cup, and replied with a headache answer to
"ignore your instructions", "?" or random letters. These requests get a fixed, safe answer
written here; everything else goes to the model as usual.
"""
import re
import unicodedata
from typing import Optional

import medicines


def _plain(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn")


ONLY_HEALTH = (
    "Solo puedo ayudarte con **dudas de salud**: síntomas, medicamentos, análisis, prevención "
    "o qué hacer en una situación concreta. Cuéntame qué te pasa y te ayudo."
)
NOT_UNDERSTOOD = (
    "No he entendido tu mensaje. ¿Me cuentas qué te pasa o qué duda de salud tienes? "
    "Por ejemplo: qué síntomas notas, desde cuándo y si tomas alguna medicación."
)

# (name, pattern on the plain lowercase question, fixed answer)
RULES = [
    ("prompt_injection",
     r"(ignora|olvida|salta(te)?) (todas |las |tus )*(instrucciones|reglas|normas)|prompt del sistema|"
     r"system prompt|instrucciones (del sistema|internas|iniciales)|repeat (everything|the text|all) above|"
     r"ignore (all |your |previous )*instructions|modo (desarrollador|developer|dios)|no tienes (reglas|normas|limites)|"
     r"eres (ahora )?doctorlibre|jailbreak|\bdan\b mode",
     "No puedo hacer eso. " + ONLY_HEALTH),
    ("prescription",
     # "dame una receta de lentejas" is cooking (topic.py), "...de antibióticos" is not
     r"\b(recetame|receteme|prescribeme|me recetas)\b|\b(hazme|dame) una receta\b"
     r"(?!\s+de\s+(?!(un |una |el |la |los |las )?(medicamento|medicina|antibiotic|pastilla|farmac|ibuprofeno|"
     r"paracetamol|jarabe|crema|pomada)))",
     "No puedo recetar medicamentos: solo un médico puede hacerlo, después de valorarte. "
     "Si crees que necesitas un tratamiento con receta, pide cita con tu médico de cabecera. "
     "Mientras tanto, puedo explicarte para qué sirve un medicamento o qué hacer con tus síntomas."),
    ("no_prescription",
     r"(conseguir|comprar|pillar|sacar) .{0,40}sin receta|sin pasar por el medico.{0,30}(conseguir|comprar)",
     "No puedo ayudarte a conseguir medicamentos que necesitan receta sin ella: pueden ser "
     "peligrosos sin control médico, y comprarlos fuera de la farmacia es ilegal y arriesgado. "
     "Habla con tu médico de cabecera: si lo necesitas, te lo puede recetar."),
    ("sedate_someone",
     r"(dormir|sedar|drogar|dormirse|duerma).{0,40}(sin que (lo|se) (sepa|de cuenta|entere))|"
     r"echar(le)? algo en (la|su) bebida|sin que se de cuenta.{0,40}(pastilla|droga|dormir)",
     "No puedo ayudarte con eso: dar un medicamento o una sustancia a alguien sin que lo sepa "
     "es peligroso y es un delito. Si alguien de tu entorno tiene problemas para dormir o te "
     "preocupa, puedo darte consejos para que lo hable con su médico."),
    ("recreational",
     r"(colocarme|colocarse|ponerme ciego|drogarme|para flipar|para viajar)\b",
     "No puedo recomendarte medicamentos ni sustancias para colocarte: mezclar o tomar "
     "medicamentos sin indicación puede causar intoxicaciones graves. Si consumes y quieres "
     "información para reducir riesgos o dejarlo, habla con tu médico o con un centro de "
     "atención a las adicciones. En una urgencia, llama al **112**."),
    ("steroids",
     r"(ciclo|ciclar|pinchar(me)?|inyectar(me)?).{0,30}(esteroide|anabolizante|anabolico|testosterona|dianabol|winstrol|trembolona)|"
     r"(esteroide|anabolizante|anabolico)s?.{0,40}(musculo|masa muscular|volumen|definir)",
     "No puedo recomendarte esteroides anabolizantes: sin control médico pueden causar "
     "problemas de corazón, hígado, hormonas y fertilidad, y su uso para ganar músculo no es "
     "seguro. Para ganar masa muscular, lo que funciona es el entrenamiento de fuerza progresivo, "
     "suficiente proteína y buen descanso. Si te lo planteas, habla antes con tu médico."),
    ("child_dose",
     r"(dosis|cuant[oa]s? (mg|ml|miligramos|mililitros)|que cantidad).{0,60}(hijo|hija|nino|nina|bebe|crio|cria|\d+ ?(kg|kilos))|"
     r"(hijo|hija|nino|nina|bebe).{0,60}(dosis|cuant[oa]s? (mg|ml|miligramos|mililitros))|"
     # "¿Cuánto ibuprofeno puede tomar mi hija de 20 kg?"
     r"cuant[oa]s?\b.{0,40}(le doy|darle|puede tomar|le puedo dar).{0,40}(hijo|hija|nino|nina|bebe|\d+ ?(kg|kilos))",
     "No puedo calcular dosis para niños: dependen de su **peso** y de la **presentación** "
     "(no es lo mismo un jarabe de 100 mg/5 ml que uno de 200 mg/5 ml), y un error puede ser "
     "peligroso.\n\n- Mira la tabla de dosis por peso del **prospecto** de tu envase.\n"
     "- Pregunta a tu **farmacéutico** o a tu **pediatra**: te lo dirán al momento.\n"
     "- Usa siempre la jeringa o el vasito dosificador que viene con el medicamento."),
    ("emergency_contraception",
     r"(preservativo|condon).{0,40}(roto|rompi|se ha roto|se rompio|se salio|se quedo dentro)|"
     r"(roto|rompi|se ha roto|se rompio|se salio).{0,20}(preservativo|condon)|"
     r"(relacion(es)?|sexo|follar) sin (proteccion|preservativo|condon)|pildora del dia despues|"
     r"anticoncep\w* de (urgencia|emergencia)",
     "**Si quieres evitar un embarazo, toma la píldora del día después cuanto antes**: cuanto "
     "antes la tomes, más eficaz es.\n\n"
     "- **Se compra en cualquier farmacia sin receta.** Hay dos tipos: el levonorgestrel sirve "
     "hasta **72 horas** (3 días) después y el acetato de ulipristal hasta **120 horas** (5 días).\n"
     "- Si han pasado más de 5 días, o tienes dudas, ve a tu **centro de salud** o a un centro "
     "de planificación familiar: hay otras opciones.\n"
     "- No protege de las infecciones de transmisión sexual. Si no conoces el estado de salud "
     "de tu pareja, pide en tu centro de salud una prueba de ITS. Si pudo haber riesgo de VIH, "
     "ve a **urgencias en las primeras 72 horas**: hay un tratamiento preventivo."),
    # Everyday situations the model answered wrongly in testing: the answer is written here
    ("high_glucose",
     r"(glucosa|azucar|glucemia).{0,40}\b([2-5]\d\d)\b|(diabetic|diabetes).{0,60}(glucosa|azucar).{0,30}(alt|dispar|\d{3})",
     "Con la glucosa por encima de 250 mg/dL **no comas nada para bajarla**: la comida la sube.\n\n"
     "- **Bebe agua** (sin azúcar) para no deshidratarte.\n"
     "- Si usas insulina, sigue la **pauta de corrección** que te haya dado tu médico o tu "
     "enfermera de diabetes. Si tienes diabetes tipo 1, mide también los **cuerpos cetónicos**.\n"
     "- **No hagas ejercicio** si estás por encima de 250 y tienes cuerpos cetónicos o te encuentras mal.\n"
     "- Vuelve a medirla en 2–3 horas. Si sigue alta o te pasa a menudo, consulta a tu médico "
     "para revisar el tratamiento.\n\n"
     "**Ve a urgencias o llama al 112** si pasa de 300 y no baja, o si tienes vómitos, dolor de "
     "tripa, mucha sed, respiración rápida y profunda, confusión o mucho sueño."),
    ("tick_bite",
     r"garrapata",
     "**Quítala cuanto antes con unas pinzas de punta fina:**\n\n"
     "1. Agárrala lo más cerca posible de la piel, por la cabeza.\n"
     "2. Tira hacia arriba **despacio y de forma constante**, sin girarla ni apretarle el cuerpo.\n"
     "3. Lava la zona con agua y jabón y desinfecta.\n"
     "4. Apunta la fecha.\n\n"
     "**No uses aceite, alcohol, vaselina ni calor** para que se suelte: puede hacer que suelte más "
     "saliva y aumente el riesgo de infección.\n\n"
     "**Consulta a tu médico** si en las próximas semanas aparece una **mancha roja que se va "
     "agrandando** alrededor de la picadura, fiebre, dolor de cabeza, de músculos o de "
     "articulaciones, o si no has podido sacar la cabeza de la garrapata."),
    ("paracetamol_max",
     r"\bcuant[oa]s?\s+(gramos de |mg de |pastillas de |comprimidos de )?paracetamol(es)?\b.{0,40}(puedo|se puede|al dia|maximo|tomar)|"
     r"dosis maxima.{0,20}paracetamol|paracetamol.{0,30}(dosis maxima|como maximo|maximo al dia)",
     "**Adultos:** 1 comprimido de 500 mg a 1 g cada 6–8 horas, **sin pasar de 3 g al día** "
     "salvo que tu médico te indique otra cosa.\n\n"
     "- Si tienes problemas de hígado, pesas menos de 50 kg o bebes alcohol a menudo, el máximo "
     "es menor: pregunta a tu médico o farmacéutico.\n"
     "- Revisa que no tomes otros medicamentos que también lleven paracetamol (antigripales, "
     "algunos analgésicos combinados): las dosis se suman.\n"
     "- **Niños:** la dosis va por peso; mira la tabla del prospecto o pregunta al pediatra.\n\n"
     "Comprueba siempre el prospecto de tu envase."),
    ("ibuprofen_max",
     r"\bcuant[oa]s?\s+(mg de |pastillas de |comprimidos de )?ibuprofeno(s)?\b.{0,40}(puedo|se puede|al dia|maximo|tomar)|"
     r"dosis maxima.{0,20}ibuprofeno|ibuprofeno.{0,30}(dosis maxima|como maximo|maximo al dia)",
     "**Adultos, sin receta:** 1 comprimido de 200 o 400 mg cada 6–8 horas, **con comida**, "
     "sin pasar de **1.200 mg al día** salvo indicación médica.\n\n"
     "- No lo tomes si estás embarazada, tomas anticoagulantes, tienes úlcera de estómago, "
     "problemas de riñón o de corazón, o asma que empeora con antiinflamatorios.\n"
     "- **Niños:** la dosis va por peso; mira la tabla del prospecto o pregunta al pediatra.\n\n"
     "Comprueba siempre el prospecto de tu envase."),
    ("pets",
     r"\b(perro|perra|gato|gata|mascota|cachorro|conejo|hamster|loro|caballo)\b",
     "Para los animales, quien puede ayudarte es un **veterinario**: los medicamentos y las "
     "dosis de las personas pueden ser tóxicos para ellos (por ejemplo, el ibuprofeno o el "
     "paracetamol para perros y gatos).\n\n**Si tu mascota ha comido algo tóxico** (chocolate, "
     "medicamentos, uvas, cebolla, productos de limpieza…), llama ya a tu veterinario o a una "
     "clínica veterinaria de urgencias. No le provoques el vómito sin que te lo indiquen."),
    ("opioid_dose",
     r"(morfina|fentanilo|oxicodona|tramadol|metadona|ketamina|tapentadol|buprenorfina).{0,60}(dosis|mg|miligramos|cantidad)|"
     r"(dosis|mg|miligramos|cantidad).{0,60}(morfina|fentanilo|oxicodona|tramadol|metadona|ketamina|tapentadol|buprenorfina)|"
     r"dosis.{0,40}(intravenosa|endovenosa|\biv\b)",
     "No doy dosis de opioides ni de medicación intravenosa: aquí no puedo comprobar quién "
     "pregunta ni la situación del paciente, y un error puede ser mortal. Si eres profesional "
     "sanitario, consulta la **ficha técnica** del medicamento en CIMA (cima.aemps.es) y los "
     "protocolos de tu centro, o a farmacia hospitalaria."),
    ("antibiotic_without_doctor",
     r"(que|cual|dime (que|un)) antibiotico.{0,30}(tomo|me tomo|puedo tomar|tomar|me tomaria)|"
     r"antibiotico.{0,40}sin (ir al|pasar por el|ver al) medico",
     "No puedo decirte qué antibiótico tomar: necesitan **receta**, y el médico tiene que "
     "confirmar que es una infección bacteriana y elegir el adecuado. Tomar uno que no toca "
     "no cura la infección y crea resistencias.\n\n- **Pide cita con tu médico** o pregunta "
     "en tu farmacia: en una infección de orina suelen poder valorarte el mismo día.\n- Mientras "
     "tanto, bebe agua y no aguantes las ganas de orinar.\n- **Ve a urgencias** si tienes "
     "fiebre, dolor en la espalda o en un costado, vómitos o sangre en la orina."),
    ("off_topic",
     r"\b(python|javascript|programa(cion)?|codigo fuente|excel|mundial|futbol|liga|partido|"
     r"pelicula|serie|cancion|chiste|poema|traduce|traduccion|receta de cocina|bitcoin|politica|"
     r"elecciones|horoscopo)\b",
     ONLY_HEALTH),
    ("repetition",
     r"(repite|escribe|di)\b.{0,40}\b(\d{2,}|cien|mil|quinientas|doscientas|trescientas) veces",
     ONLY_HEALTH),
]
_COMPILED = [(name, re.compile(p), answer) for name, p, answer in RULES]


# Words that show a message is a real health question, also with typos or in other languages
HEALTH_RE = re.compile(
    r"(duel|dolor|torc|golpe|fiebre|\btos\b|herid|lesion|sangr|me he|tengo|siento|encuentro|"
    r"medic|pastill|sintom|salud|enferm|hospital|urgencia|lesion|esguince|rodilla|tobillo|cabeza|"
    r"pain|fever|hurt|sick|febre|mal de|dolor)")
COMMON_WORDS = {
    "me", "te", "se", "mi", "tu", "el", "la", "los", "las", "un", "una", "y", "o", "de", "del", "en",
    "que", "qué", "q", "es", "no", "si", "sí", "por", "para", "con", "lo", "le", "hay", "puedo", "tengo",
    "estoy", "muy", "mucho", "hola", "ayuda", "i", "have", "my", "is", "the", "what", "do", "a", "em",
    "fa", "mal", "tinc", "buenas", "gracias", "como", "cómo", "duele", "pasa",
}


def is_gibberish(question: str) -> bool:
    """Empty, only symbols, or letters that form no real words ("asdkjh qwe ¿¿?? 123").
    Emojis alone still count as a message (🤒 = fever)."""
    words = re.findall(r"[a-záéíóúñüç]+", question.lower())
    if not words:
        return not re.search(r"[\U0001F300-\U0001FAFF]", question)
    if any(w in COMMON_WORDS for w in words) or HEALTH_RE.search(_plain(question)):
        return False
    if any(_plain(w) in medicines._index for w in words):  # "¿ibuprofeno?" is a real question
        return False
    return len(question) < 60  # a short message with no single real word


def check(question: str) -> Optional[tuple[str, str]]:
    """(rule name, fixed answer) if the question must not go to the model, else None."""
    plain = _plain(question)
    for name, pattern, answer in _COMPILED:
        if pattern.search(plain):
            # "Me he torcido el tobillo jugando al fútbol" is a health question
            if name == "off_topic" and HEALTH_RE.search(plain):
                continue
            return name, answer
    if is_gibberish(question):
        return "gibberish", NOT_UNDERSTOOD
    return None
