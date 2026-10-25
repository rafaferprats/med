"""Reads a lab report PDF and classifies every value in code, without the model.

1. extract_lines(): the text of a digital PDF (pypdf). Scanned PDFs have no text.
2. parse(): finds lines like "Glucosa  105  mg/dL  70 - 110", in the formats labs use
   (decimal commas, "< 200", "[70-110]", "250.000 /mm3", flags such as "H" or "*"...).
3. classify(): high / low / normal against the lab's own range (or the adult range in
   lab_tests.py when the report has none), plus values that can be dangerous.

The model never reads the PDF: it only gets the classified results to write a summary, and
NumberGuard removes any sentence of that summary with numbers that aren't in the results.
Of the personal data in the PDF only the sex and the age are read (patient()), to pick the
right reference range; nothing else (name, ID, address) is parsed, and nothing is stored.
"""
import io
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Optional

from lab_tests import ANALYTES, BY_ID, CRITICAL_MESSAGE

# Order of the groups in the report: as they appear in lab_tests.py
GROUPS = list(dict.fromkeys(a["group"] for a in ANALYTES)) + ["Otros"]
SEX_KEYS = {"male": "m", "female": "f", "m": "m", "f": "f"}

MAX_PAGES = 30
MIN_RESULTS = 3  # fewer recognised tests than this: not a lab report


class LabError(Exception):
    """A PDF that can't be read as a lab report; the message is shown to the user."""


# ---------- Text ----------

def fold(text: str) -> str:
    """Lowercase without accents, keeping the same length (positions stay valid)."""
    out = []
    for ch in text:
        base = unicodedata.normalize("NFD", ch)[0].lower()
        out.append(base if len(base) == 1 else ch)
    return "".join(out)


def open_pdf(data: bytes):
    """The pages of the PDF (at most MAX_PAGES). Raises LabError if it can't be opened."""
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise LabError("El PDF está protegido con contraseña. Descárgalo de nuevo sin contraseña o "
                           "imprímelo como PDF y vuelve a subirlo.")
        return list(reader.pages[:MAX_PAGES])
    except LabError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError, OSError):
        raise LabError("No se ha podido abrir el PDF. Comprueba que el archivo no esté dañado.")


def page_lines(pages) -> list[list[str]]:
    """Lines of text of some pages, with two extraction modes (labs lay out columns
    differently: one mode usually keeps them apart better than the other)."""
    from pypdf.errors import PdfReadError

    layouts = []
    try:
        for mode in ("layout", "plain"):
            text = "\n".join((p.extract_text(extraction_mode=mode) or "") for p in pages)
            layouts.append([line.rstrip() for line in text.splitlines() if line.strip()])
    except (PdfReadError, ValueError, KeyError, TypeError, OSError):
        raise LabError("No se ha podido abrir el PDF. Comprueba que el archivo no esté dañado.")
    return layouts


def extract_lines(data: bytes) -> list[list[str]]:
    return page_lines(open_pdf(data))


# ---------- Is it a medical lab report? ----------
# Checked on the first pages, before reading the rest: anything else stops here.

FIRST_PAGES = 3
LAB_WORDS = re.compile(
    r"\b(laboratorio|analisis clinicos|analitica|hemograma|bioquimica|hematologia|serie roja|serie blanca|"
    r"formula leucocitaria|valores? de referencia|intervalos? de referencia|rangos? de referencia|"
    r"valores normales|resultados?|unidades|determinacion|muestra|suero|plasma|sangre|orina|sistematico|"
    r"extraccion|perfil lipidico|coagulacion|hormonas|validado|facultativo|bioquimic[oa])\b")
MEDICAL_WORDS = re.compile(
    r"\b(paciente|medic[oa]|doctor[a]?|diagnostico|tratamiento|hospital|centro de salud|consulta|"
    r"historia clinica|informe|alta|ingreso|receta|posologia|enfermedad|sintomas|exploracion|"
    r"servicio de|urgencias|radiologia|ecografia|resonancia|cita|farmaco|medicamento|comprimidos?)\b")
UNITS = re.compile(r"(mg/dl|g/dl|mmol/l|meq/l|\bu/l|ui/l|ng/ml|pg/ml|[µu]g/dl|[µu]ui/ml|x\s?10\s?\^?\s?[369]"
                   r"|/mm3|\bfl\b|mm/h|mg/l|ml/min|/[µu]l)", re.I)

NOT_MEDICAL = ("Este archivo no es una analítica: no parece un documento médico. He detenido el análisis. "
               "Sube el PDF de tu analítica de sangre u orina, como el que se descarga del portal del laboratorio "
               "o de tu centro de salud.")
NOT_LAB = ("Este PDF parece un documento médico, pero no una analítica (por ejemplo un informe, una receta o una "
           "prueba de imagen). He detenido el análisis: de momento solo explico analíticas de sangre y orina. "
           "Si tienes dudas sobre el documento, pregúntame en el chat.")
LAB_UNREADABLE = ("Parece una analítica, pero no he podido leer sus valores. Si quieres, escríbeme los valores "
                  "que te preocupan (nombre, resultado y rango) y te los explico.")


def medical_signals(layouts: list[list[str]]) -> dict:
    """What makes a text look like a lab report: tests the app knows, lab vocabulary and units."""
    text = fold("\n".join(max(layouts, key=len)))
    known = max(sum(1 for r in parse(lines) if r.analyte) for lines in layouts)
    return {
        "known_tests": known,
        "lab_words": len(set(LAB_WORDS.findall(text))),
        "units": len(UNITS.findall(text)),
        "medical_words": len(set(MEDICAL_WORDS.findall(text))),
    }


def check_medical(layouts: list[list[str]]) -> dict:
    """Raises LabError, with a different message for a non-medical document and for a medical
    one that is not a lab report. Returns the signals (for logs and tests)."""
    s = medical_signals(layouts)
    is_lab = s["known_tests"] >= MIN_RESULTS or (s["known_tests"] >= 1 and s["units"] >= 2 and s["lab_words"] >= 2)
    if is_lab:
        return s
    if s["medical_words"] >= 2 or s["lab_words"] >= 3 or s["known_tests"] >= 1:
        raise LabError(NOT_LAB)
    raise LabError(NOT_MEDICAL)


# ---------- Numbers and units ----------

NUM = r"\d+(?:[.,]\d+)*"
# Units that contain digits are hidden before looking for the value and the range
UNIT_WITH_DIGITS = re.compile(
    r"(?:x\s?)?10\s?(?:\^|e|E|\*\*|\*)?\s?(?:3|6|9|12)\s?/?\s?(?:mm3|mm³|[µμu]?[lL])\b"  # "x 10^6 µl" too
    r"|/\s?mm[3³]|\bmm[3³]|/\s?1[.,]73\s?m[2²]?|/\s?24\s?h\b|\bm[2²]\b"
    r"|(?:x\s?)?10\s?[³⁶⁹]\s?/?\s?(?:mm3|mm³|[µμu]?[lL])\b"
)
FLAG = re.compile(r"^\s*(?:\*+|[HLAB]\b|[↑↓]|\(\+\)|\(-\)|alto\b|bajo\b)", re.I)
BETWEEN = re.compile(rf"({NUM})\s*(?:[-–—]|\ba\b|\bhasta\b)\s*({NUM})", re.I)
UPPER = re.compile(rf"(?:<=?|≤|\bmenor (?:de|que)\b|\binferior(?: a)?\b|\binf\.?|\bm[aá]x(?:imo)?\.?:?|\bhasta\b)\s*({NUM})", re.I)
LOWER = re.compile(rf"(?:>=?|≥|\bmayor (?:de|que)\b|\bsuperior(?: a)?\b|\bsup\.?|\bm[ií]n(?:imo)?\.?:?)\s*({NUM})", re.I)
VALUE = re.compile(rf"(?<![\w§.,])([<>]=?|≤|≥)?\s?({NUM})(?=[HL*↑↓]?(?:[^\w§]|$))")
QUAL = re.compile(r"(?<!\w)(negativ[oa]s?|positiv[oa]s?|indicios|trazas|ausentes?|presentes?|normal|"
                  r"no se observan?|escas[oa]s|abundantes?|\+{1,4})(?!\w)", re.I)
QUAL_NORMAL = re.compile(r"negativ|ausente|normal|no se observa|escas", re.I)
UNIT_TOKEN = re.compile(r"(?:%|[a-zA-Zµμ§/][\w§/µμ.·^*%²³-]*)")
NOT_UNITS = {"a", "hasta", "de", "y", "h", "l", "*", "alto", "bajo", "menor", "mayor", "que", "inferior",
             "superior", "valores", "referencia", "ref", "vr", "v.r."}


def parse_number(text: str, big: bool = False) -> float:
    s = text.strip()
    if "," in s and "." in s:          # 1.234,5
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:                     # 4,5
        s = s.replace(",", ".")
    elif big and re.fullmatch(r"\d{1,3}(?:\.\d{3})+", s):  # 250.000 platelets
        s = s.replace(".", "")
    return float(s)


def unit_key(unit: str) -> str:
    u = fold(unit).replace("μ", "u").replace("µ", "u").replace("³", "3").replace("²", "2")
    u = re.sub(r"\s|\^|\*", "", u)
    u = re.sub(r"10e(\d)", r"10\1", u)
    u = re.sub(r"/1[.,]?73m?2?$", "", u)
    u = re.sub(r"^(x?10(?:3|6|9|12))(?=u?l$|mm3$)", r"\1/", u)  # "x 10³ µl" = "x10³/µl"
    return u.rstrip("/.")


# ---------- Results ----------

@dataclass
class Result:
    name: str                       # display name
    raw_name: str                   # as written in the report
    analyte: Optional[str]          # id in lab_tests.ANALYTES, or None if unknown
    group: str
    value: Optional[float] = None
    qualifier: str = ""             # "<" or ">" for results like "< 0,5"
    text_value: str = ""            # as written ("13,2", "Negativo")
    unit: str = ""
    low: Optional[float] = None     # reference range in the report's units
    high: Optional[float] = None
    ref_text: str = ""
    ref_source: str = ""            # "lab", "default" or ""
    ref_note: str = ""              # why the default range may not fit this person
    status: str = "unknown"         # normal, low, high, positive, unknown
    slight: bool = False            # within 10% of the limit
    critical: bool = False
    critical_text: str = ""
    what: str = ""
    meaning: str = ""               # what this high/low value can mean
    line: str = field(default="", repr=False)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("line")
        return d


def _build_matchers():
    blood, urine = [], []
    for a in ANALYTES:
        for alias in a["aliases"]:
            pattern = re.compile(r"^[\s*•·>\-–]*" + re.escape(alias) + r"(?![a-z0-9])")
            (urine if a.get("urine") else blood).append((len(alias), pattern, a))
    blood.sort(key=lambda t: -t[0])
    urine.sort(key=lambda t: -t[0])
    return blood, urine


BLOOD, URINE = _build_matchers()
URINE_HEADER = re.compile(r"\b(orina|sistematico|sedimento|urinalisis|tira reactiva|anormales y sedimento)\b")
BLOOD_HEADER = re.compile(r"^\s*(hematologia|hemograma|bioquimica|serie roja|serie blanca|formula leucocitaria|"
                          r"hormonas|perfil|coagulacion|inmunologia|lipidos|metabolismo|vitaminas|suero)")
# Lines with numbers that are never results
NOT_RESULT = re.compile(r"\b(fecha|nacimiento|edad|dni|nif|nie|nhc|historia|sip|cip|tarjeta|telefono|tlf|"
                        r"pagina|peticion|muestra|n[ºo°]|codigo|postal|hora|dr\.?|dra\.?|doctor|medico|"
                        r"solicitante|paciente|registro|episodio|cama|direccion|calle|tel|telf|centro|factura|"
                        r"importe|total|iva)\b")
GENERIC_NAME = re.compile(r"^\s*([A-Za-zÁÉÍÓÚÜÑáéíóúüñ][A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9 ()/,+%-]{1,48}?)\s*(?:\.{2,}|:|\s{2,})\s*(?=[<>]?\s?\d)")


def _match_name(folded: str, in_urine: bool):
    tables = (URINE, BLOOD) if in_urine else (BLOOD,)
    for table in tables:
        for _, pattern, analyte in table:
            m = pattern.match(folded)
            if m:
                if in_urine and table is BLOOD and any(fold(analyte["name"]).startswith(x) for x in
                                                       ("leucocitos", "hematies", "glucosa", "proteinas", "bilirrubina")):
                    continue
                return analyte, m.end()
    return None, 0


CALCITRIOL = re.compile(r"\W*1\s*[,.]\s*25\s*-?\s*(?:\(\s*oh\s*\)\s*2|di\s*-?\s*hidroxi\w*|dihydroxy\w*)(?:\s*-?\s*(?:vitamina|vit\.?)\s*d\d?)?")


# "Número total leucocitos en sangre", "% eosinófilos en sangre", "Porcentaje hemoglobina glicosilada"
NAME_PREFIX = re.compile(r"^\s*(?:numero total(?: de)?|recuento(?: total)?(?: de)?|porcentaje(?: de)?|%)\s*(?=[a-z])")


def parse_line(line: str, in_urine: bool = False) -> Optional[Result]:
    folded = fold(line)
    prefix = NAME_PREFIX.match(folded)
    pct_prefix = bool(prefix and re.search(r"%|porcentaje", prefix.group(0)))
    if prefix:
        line, folded = line[prefix.end():], folded[prefix.end():]
    lead = CALCITRIOL.match(folded)
    if lead and lead.group(0).strip():  # "1,25-Dihidroxi vitamina D  70,90 ..." (name starts with digits)
        line = "Vitamina D 1,25-dihidroxi " + line[lead.end():].lstrip(" :")
        folded = fold(line)
    if NOT_RESULT.search(folded) and not _match_name(folded, in_urine)[0]:
        return None
    analyte, end = _match_name(folded, in_urine)
    if analyte:
        raw_name = line[:end].strip(" *•·>-–")
    else:
        m = GENERIC_NAME.match(line)
        if not m or NOT_RESULT.search(fold(m.group(1))):
            return None
        raw_name, end = m.group(1).strip(), m.end(1)
    rest = line[end:]
    # "Vitamina D: 1,25 Dihidroxi  70,90  20 - 79": the "1,25" is part of the name, not the value.
    # And 1,25-dihydroxy vitamin D (calcitriol) is not the usual vitamin D test (25-OH, the
    # body's reserve): it has its own ranges and meaning, so it is not explained as that one.
    calcitriol = CALCITRIOL.search(fold(line))
    if calcitriol:
        cut = CALCITRIOL.match(fold(rest))
        if cut:
            rest = rest[cut.end():]
        elif calcitriol.end() > end:
            rest = line[calcitriol.end():]
        if not analyte or analyte["id"] == "vitd":
            analyte, raw_name = None, "Vitamina D 1,25-dihidroxi (calcitriol)"
    # "(ayunas)", "(CKD-EPI)", "%" right after the name, then leader dots and colons
    rest = re.sub(r"^\s*\([^)]*\)", "", rest)
    rest = re.sub(r"^\s*(?:25\s?-?\s?oh|25\s?-?\s?hidroxi)\b", "", rest, flags=re.I)  # "Vitamina D 25-OH"
    pct_in_name = bool(re.match(r"^\s*%", rest)) or "%" in raw_name or pct_prefix
    rest = re.sub(r"^\s*%", "", rest)
    rest = re.sub(r"^[\s.:_…·]+", " ", rest)

    masks: list[str] = []

    def hide(m):
        masks.append(m.group(0))
        return f"§{len(masks) - 1}§"

    masked = UNIT_WITH_DIGITS.sub(hide, rest)
    big = bool(analyte and analyte.get("big"))

    qual = QUAL.search(masked)
    val = VALUE.search(masked)
    if qual and (not val or qual.start() < val.start()):
        result = Result(name=analyte["name"] if analyte else raw_name, raw_name=raw_name,
                        analyte=analyte["id"] if analyte else None,
                        group=analyte["group"] if analyte else "Otros", text_value=qual.group(1), line=line)
        result.status = "normal" if QUAL_NORMAL.search(qual.group(1)) else "positive"
        return result if analyte else None
    if not val:
        return None
    try:
        value = parse_number(val.group(2), big)
    except ValueError:
        return None
    after = masked[val.end():]
    while True:  # flags after the value: H, L, *, ↑...
        f = FLAG.match(after)
        if not f or not f.group(0).strip():
            break
        after = after[f.end():]

    low = high = None
    ref_text = ""
    ref_span = None
    candidates = []
    for kind, rx in (("between", BETWEEN), ("upper", UPPER), ("lower", LOWER)):
        m = rx.search(after)
        if m:
            candidates.append((m.start(), kind, m))
    if candidates:
        candidates.sort(key=lambda c: (c[0], c[1] != "between"))
        _, kind, m = candidates[0]
        try:
            if kind == "between":
                low, high = parse_number(m.group(1), big), parse_number(m.group(2), big)
                if low > high:
                    low, high = high, low
            elif kind == "upper":
                high = parse_number(m.group(1), big)
            else:
                low = parse_number(m.group(1), big)
        except ValueError:
            low = high = None
        ref_span = (m.start(), m.end())
        ref_text = _restore(m.group(0), masks).strip()

    unit = ""
    unit_area = after if ref_span is None else after[:ref_span[0]] + " " + after[ref_span[1]:]
    for tok in UNIT_TOKEN.finditer(unit_area):
        t = tok.group(0).strip(".")
        if t.lower() in NOT_UNITS or (len(t) == 1 and t.isalpha() and t not in "gL"):
            continue
        unit = _restore(t, masks)
        break
    if analyte is None and (low is None and high is None):
        return None  # unknown test without a range: probably not a result

    if analyte and analyte.get("pct") and (unit == "%" or pct_in_name):
        analyte = BY_ID[analyte["pct"]]
    elif analyte and analyte.get("pct") and not unit and high is not None and high > analyte["ref"][1] * 3:
        analyte = BY_ID[analyte["pct"]]  # no unit, but the range is clearly a percentage
    if analyte is None and "hemoglobina" in fold(raw_name) and unit_key(unit) == "mmol/mol":
        raw_name = "Hemoglobina glicosilada (HbA1c) en mmol/mol"  # "Concentración hemoglobina | glicosilada (A1c)"
    if unit == "%" and analyte and analyte["unit"] != "%" and not analyte.get("pct"):
        unit = ""
    return Result(
        name=analyte["name"] if analyte else raw_name, raw_name=raw_name,
        analyte=analyte["id"] if analyte else None, group=analyte["group"] if analyte else "Otros",
        value=value, qualifier=(val.group(1) or "").strip(), text_value=val.group(2), unit=unit,
        low=low, high=high, ref_text=ref_text, ref_source="lab" if ref_text else "", line=line,
    )


def _restore(text: str, masks: list[str]) -> str:
    return re.sub(r"§(\d+)§", lambda m: masks[int(m.group(1))], text)


def parse(lines: list[str]) -> list[Result]:
    results: list[Result] = []
    seen: set[str] = set()
    in_urine = False
    for line in lines:
        folded = fold(line).strip()
        if URINE_HEADER.search(folded) and not re.search(r"\d", folded):
            in_urine = True
            continue
        if BLOOD_HEADER.match(folded) and not re.search(r"\d", folded):
            in_urine = False
            continue
        r = parse_line(line, in_urine)
        if r is None:
            continue
        key = r.analyte or fold(r.raw_name)
        if key in seen:  # the same test twice (repeated header or summary): keep the first
            continue
        seen.add(key)
        results.append(r)
    return results


# ---------- Classification ----------

def _factor(result: Result, analyte: dict) -> Optional[float]:
    """Factor from the report's unit to the canonical one; None if unknown."""
    key = unit_key(result.unit)
    units = analyte.get("units", {})
    for k in (key, key.removeprefix("x"), "x" + key):
        if k in units:
            return units[k]
    if analyte.get("big") and result.value is not None:
        if analyte["unit"].startswith("x10⁶"):
            return 1e-6 if result.value > 1e5 else 1 if result.value < 20 else None
        return 0.001 if result.value > 1000 else 1 if result.value < 2000 else None
    if not result.unit:
        # No unit: assume the canonical one only if the lab range looks like the usual one
        default = _default_range(analyte, "")
        if default and result.high and default[1]:
            ratio = default[1] / result.high
            return 1 if 0.6 <= ratio <= 1.6 else None
        return None
    return None


def _default_range(analyte: dict, sex: str):
    ref = analyte.get("ref")
    sex = SEX_KEYS.get(sex, "")
    if isinstance(ref, dict):
        if sex in ref:
            return ref[sex]
        # Unknown sex: the stricter limits, so a value high for a woman is not called normal
        lows = [r[0] for r in ref.values() if r[0] is not None]
        highs = [r[1] for r in ref.values() if r[1] is not None]
        return (max(lows) if lows else None, min(highs) if highs else None)
    return ref


# "Sexo: Mujer", "Sexo: F", "Varón", "Sexo/Edad: M / 52 años"...
SEX_LINE = re.compile(r"\bsexo\b\W{0,3}(?:/\s*edad\W{0,3})?(mujer|hombre|varon|femenino|masculino|female|male|[fhvm])\b")
SEX_WORD = re.compile(r"\b(mujer|hombre|varon|femenino|masculino)\b")
AGE_YEARS = re.compile(r"\b(\d{1,3})\s*anos\b")
AGE = re.compile(r"\bedad\b\D{0,12}?\b(\d{1,3})\b(?![/.-]\d)")
BIRTH = re.compile(r"(?:nacimiento|f\.?\s*nac\.?|fecha nac\w*|nacid[oa])\D{0,12}?(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})")
REPORT_DATE = re.compile(r"(?:fecha|extraccion|recepcion|emision|informe)\D{0,25}?(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})")
SEX_VALUE = {"mujer": "f", "femenino": "f", "female": "f", "f": "f",
             "hombre": "m", "varon": "m", "masculino": "m", "male": "m", "h": "m", "v": "m"}


def patient(lines: list[str]) -> dict:
    """{"sex": "m"|"f"|"", "age": int|None} from the header of the report. "Sexo: M" is left
    out: Spanish labs use it for "mujer" and others for "masculino"."""
    head = [fold(l) for l in lines[:60]]
    sex = ""
    for line in head:
        m = SEX_LINE.search(line)
        if m and m.group(1) in SEX_VALUE:
            sex = SEX_VALUE[m.group(1)]
            break
    if not sex:
        words = {SEX_VALUE[w] for line in head if "sexo" in line or "paciente" in line for w in SEX_WORD.findall(line)}
        sex = words.pop() if len(words) == 1 else ""
    age = None
    for line in head:
        b = BIRTH.search(line)
        if b:
            year = int(b.group(3)) + (1900 if len(b.group(3)) == 2 else 0)
            if len(b.group(3)) == 2 and year < 1925:
                year += 100
            ref = next((REPORT_DATE.search(l) for l in head if REPORT_DATE.search(l) and not BIRTH.search(l)), None)
            from datetime import date
            try:
                born = date(year, int(b.group(2)), int(b.group(1)))
                when = date(int(ref.group(3)), int(ref.group(2)), int(ref.group(1))) if ref else date.today()
                age = when.year - born.year - ((when.month, when.day) < (born.month, born.day))
            except ValueError:
                age = None
            break
    if age is None:
        for line in head:
            if not ("edad" in line or "paciente" in line or "sexo" in line):
                continue
            m = AGE_YEARS.search(line) or AGE.search(line)  # "Edad 30/05/1992 (28 años)": the 28
            if m:
                age = int(m.group(1))
                break
    if age is not None and not 0 <= age <= 120:
        age = None
    return {"sex": sex, "age": age}


def _slight(distance: float, limit: float, low: Optional[float], high: Optional[float]) -> bool:
    """Just outside the range: within 10% of the limit and, when the range has both ends,
    within a quarter of its width (sodium 128 for 135-145 is not "slightly" low)."""
    if limit <= 0 or distance / limit > 0.10:
        return False
    if low is not None and high is not None and high > low:
        return distance / (high - low) <= 0.25
    return True


def classify(result: Result, sex: str = "", child: bool = False) -> Result:
    analyte = BY_ID.get(result.analyte or "")
    if analyte:
        result.what = analyte["what"]
    if result.status == "positive" or result.value is None:
        if result.status == "positive" and analyte:
            result.meaning = analyte.get("positive") or analyte.get("high", "")
        return result
    low, high = result.low, result.high
    factor = _factor(result, analyte) if analyte else None
    if low is None and high is None and analyte and factor and not child:
        default = _default_range(analyte, sex)
        if default:
            low = None if default[0] in (None, 0) else default[0] / factor
            high = None if default[1] is None else default[1] / factor
            result.low, result.high = low, high
            result.ref_source = "default"
            if isinstance(analyte.get("ref"), dict) and SEX_KEYS.get(sex, "") not in analyte["ref"]:
                result.ref_note = "El rango normal cambia entre hombres y mujeres y no sé tu sexo: uso el límite más estricto."
    v, q = result.value, result.qualifier
    if high is not None and v > high and not q.startswith("<"):
        result.status = "high"
        result.slight = _slight(v - high, high, low, high)
    elif low is not None and low > 0 and v < low and not q.startswith(">"):
        result.status = "low"
        result.slight = _slight(low - v, low, low, high)
    elif low is not None or high is not None:
        result.status = "normal"
    if analyte and result.status in ("high", "low"):
        result.meaning = analyte.get(result.status, "")
    # Values that can be dangerous, in canonical units
    crit = analyte.get("critical") if analyte else None
    if crit and factor and not q:
        canon = v * factor
        if crit[0] is not None and canon < crit[0]:
            result.critical, side = True, "low"
        elif crit[1] is not None and canon > crit[1]:
            result.critical, side = True, "high"
        if result.critical:
            result.critical_text = f"{analyte['name']}: nivel muy {'bajo' if side == 'low' else 'alto'}"
            result.status = side
            result.meaning = analyte.get(side, "")
            result.slight = False
    return result


def analyse(data: bytes, sex: str = "", child: bool = False) -> dict:
    """Reads and classifies a lab report PDF. Raises LabError with a message for the user."""
    pages = open_pdf(data)
    layouts = page_lines(pages[:FIRST_PAGES])
    if sum(len(" ".join(lines)) for lines in layouts) < 40:
        raise LabError("Este PDF no tiene texto: parece un escaneo o una foto. De momento solo puedo leer "
                       "analíticas en PDF digital, como las que se descargan del portal del laboratorio o de tu "
                       "centro de salud.")
    check_medical(layouts)  # not a lab report: stops here, without reading the other pages
    if len(pages) > FIRST_PAGES:
        rest = page_lines(pages[FIRST_PAGES:])
        layouts = [first + more for first, more in zip(layouts, rest)]
    parsed = max((parse(lines) for lines in layouts), key=lambda rs: sum(1 for r in rs if r.analyte))
    if sum(1 for r in parsed if r.analyte) < MIN_RESULTS:
        raise LabError(LAB_UNREADABLE)
    person = patient(max(layouts, key=len))
    sex = person["sex"] or sex  # what the report says wins over the form
    child = child or (person["age"] is not None and person["age"] < 16)
    results = [classify(r, sex, child) for r in parsed]
    order = {g: i for i, g in enumerate(GROUPS)}
    results.sort(key=lambda r: order.get(r.group, len(GROUPS)))
    out = [r.to_dict() for r in results]
    abnormal = [r for r in out if r["status"] in ("high", "low", "positive")]
    return {
        "results": out,
        "counts": {"total": len(out), "abnormal": len(abnormal),
                   "normal": sum(1 for r in out if r["status"] == "normal")},
        "critical": [r["critical_text"] for r in out if r["critical"]],
        "critical_message": CRITICAL_MESSAGE if any(r["critical"] for r in out) else "",
        "child": child,
        "patient": {"sex": sex if sex in ("m", "f") else SEX_KEYS.get(sex, ""), "age": person["age"],
                    "from_report": bool(person["sex"] or person["age"] is not None)},
        "default_ranges": sum(1 for r in out if r["ref_source"] == "default"),
    }


# ---------- Summary by the model ----------

LAB_SYSTEM_PROMPT = (
    "You explain lab test results to people in Spain. Always write in Spanish from Spain "
    "(castellano), using 'tú', in plain words a person without medical training understands. "
    "Start directly, with no greeting.\n"
    "You get the results already read and classified by the app. Never change a classification "
    "and never write numbers, values or ranges: the person already sees them in a table.\n"
    "Use exactly these headings:\n"
    "**Resumen:** 2-3 sentences about the overall picture.\n"
    "**Lo más importante:** one short bullet for EVERY result out of range, starting with its "
    "name, most relevant first, saying in plain words what it can mean. Results that go "
    "together (for example hemoglobina, VCM and ferritina in a lack of iron) can share a bullet "
    "if all their names are written.\n"
    "**Qué puedes hacer:** 2-4 practical, general steps (diet, habits, repeating the test) that "
    "fit these results.\n"
    "**Preguntas para tu médico:** 3-4 questions to take to the appointment.\n"
    "Never give a diagnosis as certain ('tienes anemia'): say what the results 'pueden indicar'. Do not recommend "
    "medicines or doses. About 200 words. Nothing after the questions."
)

STATUS_ES = {"high": "alto", "low": "bajo", "positive": "positivo", "normal": "normal", "unknown": "sin rango"}


def summary_prompt(analysis: dict, context: str = "") -> str:
    """The results for the model, without any number."""
    lines = []
    abnormal = [r for r in analysis["results"] if r["status"] in ("high", "low", "positive")]
    abnormal.sort(key=lambda r: (not r["critical"], r["slight"]))  # dangerous ones first
    for r in abnormal:
        level = "muy " if r["critical"] else "ligeramente " if r["slight"] else ""
        text = f"- {r['name']}: {level}{STATUS_ES[r['status']]}{' (nivel que puede ser peligroso)' if r['critical'] else ''}."
        if r["what"]:
            text += f" Qué mide: {r['what']}"
        if r["meaning"]:
            text += f" Puede indicar: {r['meaning']}"
        lines.append(text)
    normal = [r["name"] for r in analysis["results"] if r["status"] == "normal"]
    prompt = ("Resultados fuera de rango (menciónalos todos):\n" + "\n".join(lines))
    critical = [r["name"] for r in abnormal if r["critical"]]
    if critical:
        names = ", ".join(critical[:-1]) + " y " + critical[-1] if len(critical) > 1 else critical[0]
        verb = "están" if len(critical) > 1 else "está"
        prompt = (f"IMPORTANTE: {names} {verb} en un nivel que puede ser peligroso. Empieza el "
                  "resumen diciendo que debe hablar hoy mismo con su médico, o ir a urgencias si se encuentra mal, "
                  "y pon esos resultados los primeros. No sugieras esperar ni repetir la analítica más adelante.\n\n"
                  + prompt)
    if normal:
        prompt += "\n\nResultados normales: " + ", ".join(normal) + "."
    if context:
        prompt += f"\n\nDatos de la persona: {context}."
    if analysis.get("child"):
        prompt += "\nEs un niño o niña: recuerda que los valores normales cambian con la edad."
    return prompt


def all_normal_summary(analysis: dict) -> str:
    n = analysis["counts"]["normal"]
    return ("**Resumen:** Todos los valores que he podido leer de tu analítica "
            f"({n}) están dentro de sus valores de referencia. Es un buen resultado.\n\n"
            "**Qué puedes hacer:** Sigue con tus hábitos y repite los análisis cuando te diga tu médico.\n\n"
            "**Preguntas para tu médico:**\n"
            "1. ¿Cada cuánto tiempo debo repetir estos análisis?\n"
            "2. ¿Hay alguna prueba que me convenga añadir por mi edad o mis antecedentes?")


CRITICAL_INTRO = (
    "**Importante:** algunos valores están en un nivel que puede ser peligroso. Habla hoy mismo con tu "
    "médico o tu centro de salud. Si te encuentras mal, llama al 112 o ve a urgencias.\n\n"
)
# With critical values, sentences that suggest waiting are removed ("repite la analítica en unas semanas")
WAITING = re.compile(r"(repet|repit|espera|vuelve a hacer|control)\w*[^.\n]{0,60}\b(semanas?|meses|mes|año)\b", re.I)


def critical_intro(analysis: dict) -> str:
    """Fixed first lines of the summary when there are dangerous values (not left to the model)."""
    return CRITICAL_INTRO if analysis["critical"] else ""


def missing_note(analysis: dict, summary: str) -> str:
    """Results out of range the model's summary didn't name, so none is left out."""
    folded = fold(summary)
    missing = []
    for r in analysis["results"]:
        if r["status"] not in ("high", "low", "positive"):
            continue
        first_word = re.split(r"[ (/]", fold(r["name"]))[0]
        if first_word not in folded:
            missing.append(r["name"])
    if not missing:
        return ""
    return ("\n\n**También fuera de rango:** " + ", ".join(missing) +
            ". Tienes su explicación arriba; coméntalos también con tu médico.")


def context_message(analysis: dict) -> str:
    """The results as a chat message, so follow-up questions can refer to them."""
    rows = []
    for r in analysis["results"]:
        value = f"{r['qualifier']}{r['text_value']} {r['unit']}".strip()
        ref = f" (referencia {r['ref_text']})" if r["ref_text"] else ""
        rows.append(f"- {r['name']}: {value}{ref}, {STATUS_ES[r['status']]}")
    return "He subido mi analítica. Estos son los resultados que ha leído la app:\n" + "\n".join(rows)


TIME_WORDS = r"(?:\s*(?:-|a|o)\s*\d+)?\s*(?:d[ií]as?|semanas?|mes(?:es)?|años?|horas?|veces|minutos?|vasos?|raciones?|piezas?)\b"


# A number inside a name (HbA1c, B12, T4, vitamina D3, 25-OH) is not a value
NUMBER_IN_TEXT = re.compile(r"(?<![A-Za-zÁÉÍÓÚÜÑáéíóúüñ\d.,])\d+(?:[.,]\d+)*(?![\d.,]*\d)(?!\s?-?\s?\(?oh\b)", re.I)
# A diagnosis stated as certain becomes a possibility: "tienes una anemia" -> "podrías tener una anemia"
CERTAIN = re.compile(r"\b(tienes|padeces|sufres) (una |un )?(anemia|diabetes|prediabetes|hipotiroidismo|"
                     r"hipertiroidismo|insuficiencia renal|infecci[oó]n de orina|hepatitis)\b", re.I)


def soften(text: str) -> str:
    def repl(m):
        out = f"podrías tener {m.group(2) or ''}{m.group(3)}"
        return out[0].upper() + out[1:] if m.group(1)[0].isupper() else out
    return CERTAIN.sub(repl, text)


class NumberGuard:
    """Streams the model's summary and drops every sentence or line with a number that is not
    one of the report's values or ranges, or of the explanations the model got (a time like
    '3 meses' and list numbers 1., 2. are allowed). Also softens certain diagnoses (soften)."""

    def __init__(self, analysis: dict):
        self.critical = bool(analysis["critical"])
        self.allowed = set()
        for r in analysis["results"]:
            for v in (r["value"], r["low"], r["high"]):
                if v is not None:
                    self.allowed.add(round(float(v), 3))
            # Numbers in the fixed explanations the model gets ("entre 100 y 125 en ayunas...")
            for num in re.findall(NUM, f"{r['what']} {r['meaning']}"):
                try:
                    self.allowed.add(round(parse_number(num), 3))
                except ValueError:
                    pass
        self.buf = ""
        self.removed = 0

    def _ok(self, segment: str) -> bool:
        if self.critical and WAITING.search(segment):
            return False
        text = re.sub(r"^\s*\d{1,2}[.)]\s", "", segment)        # numbered list
        text = re.sub(r"\b\d+" + TIME_WORDS, "", text, flags=re.I)  # "en 3 meses"
        text = text.replace("112", "")
        for num in NUMBER_IN_TEXT.findall(text):
            try:
                if round(parse_number(num), 3) not in self.allowed:
                    return False
            except ValueError:
                return False
        return True

    def _emit(self, segment: str) -> str:
        if self._ok(segment):
            return soften(segment)
        self.removed += 1
        return "\n" if segment.endswith("\n") else ""

    def feed(self, text: str) -> str:
        self.buf += text
        out = ""
        while True:
            m = re.search(r"\n|(?<=[.!?])\s(?=\S)", self.buf)
            if not m:
                return out
            # "1. " at the start of a line is a list number, not the end of a sentence
            if self.buf[:m.start()].strip().rstrip(".").isdigit() and m.group(0) != "\n":
                nxt = re.search(r"\n|(?<=[.!?])\s(?=\S)", self.buf[m.end():])
                if not nxt:
                    return out
                cut = m.end() + nxt.end()
            else:
                cut = m.end()
            segment, self.buf = self.buf[:cut], self.buf[cut:]
            out += self._emit(segment)

    def flush(self) -> str:
        rest, self.buf = self.buf, ""
        return self._emit(rest) if rest else ""
