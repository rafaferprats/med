"""Rule-based emergency detection. It never calls the model and is never behind the paywall."""
import re

# Each pattern is a red flag on its own. English and Spanish.
RED_FLAGS = [
    # Heart attack
    r"chest pain.*(arm|jaw|sweat)", r"(arm|jaw).*chest pain",
    r"dolor (en el |de )?pecho.*(brazo|mand[ií]bula|sud)", r"(brazo|mand[ií]bula).*dolor (en el |de )?pecho",
    # "me duele (mucho) el pecho y se me va al brazo": a few words may come in between
    r"(duele|dolor)\w*(\s+\w+){0,3}\s+(el |en el |de )?pecho.*(apriet|oprim|mand[ií]bula|sud|brazo|hombro|espalda)",
    r"pecho.*(apriet|oprim)", r"(apriet|oprim)\w*.{0,25}\bpecho", r"opresi[oó]n (en el |de |del )?pecho",
    # Strong chest pain on its own
    r"(duele|dolor)\w*\s+(mucho|muy fuerte|fuerte|intens\w*|horrible|insoportable)\s+(el |en el |de )?pecho",
    r"dolor (muy )?(fuerte|intenso|horrible|insoportable) (en el |de )?pecho",
    # Breathing
    r"(can'?t|cannot|hard to|trouble|difficulty) breath", r"not breathing",
    r"(no puedo|me cuesta|dificultad (para|al)) respirar", r"no respira",
    r"(lips?|tongue|throat).*(swell|swollen)", r"(labios?|lengua|garganta).*hinch",
    # Stroke
    r"(face|facial).*(droop|can'?t move)", r"slurred speech", r"speech is slurred",
    r"(cara|boca).*(torcid|ca[ií]d|no puede mover)", r"(torcid|ca[ií]d)\w*.{0,15}(cara|boca)",
    r"habla (arrastrad|rar|confus|mal)", r"no (puede|puedo) hablar", r"no (le|me) salen las palabras",
    r"se le (cae|ha ca[ií]do|tuerce|ha torcido) (un lado de |media )?(la )?(cara|boca)",
    r"(no habla bien|habla mal|no puede hablar|no le salen las palabras|no se le entiende)",
    r"(no puede mover|no siente|no puedo mover|no siento) (el |un |la |medio )?(brazo|pierna|lado|cuerpo)",
    # Head injury with warning signs, swallowed objects, poisoning, overdose
    r"golpe en la cabeza.*(vomit|v[oó]mit|desmay|confus|dormid|sangr)", r"(vomit|v[oó]mit)\w*.*golpe en la cabeza",
    r"(se ha tragado|se ha comido|ha ingerido|se trag[oó]|me he tragado) (una |un |la |el )?(pila|im[aá]n|lej[ií]a|producto de limpieza|veneno)",
    r"(se ha tomado|me he tomado|ha tomado|se tom[oó]) (un bote|una caja|un bl[ií]ster|muchas|todas las)\w*( entero| entera)? (de )?(pastillas|comprimidos|medicamentos)?",
    r"(duele|dolor)\w*.*(abajo a la derecha|parte baja derecha).*(fiebre|v[oó]mit)",
    # Fever in a baby under 3 months: always urgent (the model called it "común y leve")
    r"(beb[eé]|reci[eé]n nacid[oa]|neonato).{0,40}(\b(1|2|un|dos)\s+mes(es)?\b|\b\d+\s+semanas|\bd[ií]as\b).{0,60}(fiebre|calentura|3[89]([,.]\d)?\s*(°|º|grados)?)",
    r"(fiebre|calentura|3[89]([,.]\d)?\s*(°|º|grados)).{0,60}(beb[eé]|reci[eé]n nacid[oa]).{0,30}(\b(1|2|un|dos)\s+mes(es)?\b|\b\d+\s+semanas|\bd[ií]as\b)",
    # Brain bleed / meningitis
    r"worst headache", r"peor dolor de cabeza",
    # Loss of consciousness, seizures, bleeding
    r"(passed out|fainted|unconscious|unresponsive|seizure)",
    r"(desmay|inconsciente|perdi[oó] el conocimiento|convulsi)",
    r"(bleeding (a lot|heavily|won'?t stop)|vomiting blood|coughing up blood)",
    r"(sangra (mucho|sin parar)|vomit\w* sangre|tos\w* (con )?sangre)",
    # Self-harm
    r"(suicid|kill myself|end my life|want to die)",
    r"(suicid|matarme|quitarme la vida|quiero morir)",
    r"(para morir|dosis letal|sobredosis|acabar con (mi|todo)|no quiero vivir|no quiero seguir viviendo|"
    r"mejor no estar aqu[ií]|mejor estar muert|desaparecer para siempre|hacerme da[ñn]o|autolesi|cortarme las venas)",
]
_RED_FLAG_RE = re.compile("|".join(f"(?:{p})" for p in RED_FLAGS), re.IGNORECASE)

EMERGENCY_MESSAGE = (
    "**Esto puede ser una urgencia.** Llama ahora al **112** o ve a urgencias del hospital "
    "más cercano. No esperes a una respuesta online.\n\n"
    "Si estás pensando en el suicidio o en hacerte daño, llama al **024** (gratuito, "
    "24 horas) o al 112."
)


def detect_emergency(text: str) -> bool:
    return bool(_RED_FLAG_RE.search(text or ""))
