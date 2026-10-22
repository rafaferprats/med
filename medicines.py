"""Checks medicine names against CIMA, the official database of AEMPS (Spain).

The model confidently invents what unknown medicines are (it called the contraceptive
Cerazet an antiepileptic, and described "Intragerm", which does not exist). So when a
question asks about a specific medicine, we look its name up and add the official facts,
or the fact that it is not authorised in Spain, to the message the model sees.

Only the medicine name is sent to CIMA, never the question.
"""
import json
import re
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path

import httpx

CIMA = "https://cima.aemps.es/cima/rest"
TIMEOUT = 4.0
# Every brand and active ingredient name in CIMA, so a medicine is recognised wherever it
# appears in the question ("me han dado cerazet, ¿qué es?"), not only after "qué es".
INDEX_PATH = Path(__file__).resolve().parent / "data" / "cima_index.json"
_index: set[str] = set()

# Questions that are clearly about one named medicine
_PATTERNS = [
    r"\bqu[eé] es (?:el |la |un |una )?(?P<name>[\w\-]+)",
    r"\bpara qu[eé] (?:sirve|se usa|vale) (?:el |la )?(?P<name>[\w\-]+)",
    r"\b(?:me han recetado|me ha recetado|me han mandado|me ha mandado|me recetaron|me han dado|"
    r"me ha dado|me dieron|estoy tomando|tomo|me tomo|puedo tomar|"
    r"he tomado|conoces)\s+(?:el |la |un |una )?(?P<name>[\w\-]+)",
    r"\bwhat is (?P<name>[\w\-]+)",
    r"(?P<name>[\w\-]+)\s*,?\s*¿?\s*(?:qu[eé] es|para qu[eé] sirve)\b",
]
# Words those patterns can catch that are not medicines
_NOT_MEDICINES = {
    "esto", "eso", "esta", "este", "algo", "nada", "mejor", "bueno", "malo", "normal", "dos",
    "tres", "una", "uno", "pastilla", "pastillas", "medicamento", "medicamentos", "medicacion",
    "antibiotico", "antibioticos", "analgesico", "algun", "alguna", "alguno", "mucho", "poco",
    "ahora", "hoy", "agua", "alcohol", "cafe", "vitaminas", "vitamina", "suplementos", "dolor",
    "fiebre", "tension", "colesterol", "diabetes", "hipotiroidismo", "ansiedad", "asma",
    "cancer", "covid", "gripe", "resfriado", "this", "that", "it", "a", "the",
    # Product names in CIMA that are also everyday words in health questions
    "suero", "glucosa", "sodio", "potasio", "calcio", "hierro", "magnesio", "zinc", "acido",
    "cloruro", "oxigeno", "lactato", "bicarbonato", "sangre", "leche", "crema", "gel",
    "solucion", "jarabe", "gotas", "polvo", "spray", "parche", "aceite", "miel", "limon",
    "manzanilla", "valeriana", "melatonina", "insulina", "hormona", "vacuna",
    # Common words that follow "tomo", "puedo tomar"... in everyday questions
    # ("que tomo hasta que vaya al dentista" was answered "No conozco «hasta»")
    "hasta", "para", "cuando", "mientras", "antes", "despues", "luego", "siempre", "nunca",
    "tambien", "todo", "todos", "todas", "cada", "otra", "otro", "mismo", "misma", "porque",
    "como", "donde", "desde", "durante", "sobre", "entre", "contra", "segun", "aunque", "pues",
    "entonces", "ademas", "solo", "sola", "bien", "mal", "mucha", "muchas", "muchos", "poca",
    "pocas", "pocos", "nada", "menos", "demasiado", "suficiente", "tanto", "tanta", "algun",
    "ninguno", "ninguna", "cualquier", "cuanto", "cuanta", "cuantos", "cuantas", "dosis",
    "comprimido", "comprimidos", "capsula", "capsulas", "sobre", "sobres", "jarabe", "pomada",
    "ahora", "manana", "noche", "dia", "dias", "semana", "veces", "algo", "esto", "aquello",
}


def build_index() -> int:
    """Downloads every product name from CIMA (about 130 pages) and saves the index."""
    with httpx.Client(timeout=20) as http:
        total = http.get(f"{CIMA}/medicamentos", params={"pagina": 1}).json()["totalFilas"]
        pages = range(1, total // 200 + 2)

        def page(n):
            return http.get(f"{CIMA}/medicamentos", params={"pagina": n}).json()["resultados"]

        with ThreadPoolExecutor(max_workers=4) as pool:
            rows = [r for chunk in pool.map(page, pages) for r in chunk]
    words = set()
    for r in rows:
        first = re.split(r"[^\w]", _plain(r.get("nombre", "")))[0]
        ingredient = _plain((r.get("vtm") or {}).get("nombre", "")).split(" ")[0]
        words.update(w for w in (first, ingredient) if len(w) >= 4)
    words -= _NOT_MEDICINES
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(json.dumps(sorted(words), ensure_ascii=False))
    return len(words)


def load_index() -> None:
    """Loads the index, building it in the background the first time."""
    global _index
    if INDEX_PATH.exists():
        _index = set(json.loads(INDEX_PATH.read_text()))
        return

    def build():
        try:
            build_index()
            load_index()
        except (httpx.HTTPError, ValueError, KeyError):
            pass  # no internet: the phrase patterns below still work

    threading.Thread(target=build, daemon=True).start()


def _plain(text: str) -> str:
    """Lowercase without accents, to compare names."""
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn")


def candidate_names(message: str) -> list[str]:
    names = []
    # Any word that is a medicine or active ingredient name in CIMA
    for word in re.findall(r"[\w\-]+", message):
        if _plain(word) in _index and len(word) >= 4 and word not in names:
            names.append(word)
    # Words in phrases that are clearly about a medicine, also unknown ones (to say so)
    for pattern in _PATTERNS:
        for m in re.finditer(pattern, message, re.IGNORECASE):
            name = m.group("name").strip("-")
            if len(name) >= 4 and _plain(name) not in _NOT_MEDICINES and name not in names:
                names.append(name)
    return names[:3]


@lru_cache(maxsize=512)
def lookup(name: str) -> dict | None:
    """Official facts for a medicine name, {} if CIMA has none, None if CIMA is unreachable."""
    try:
        with httpx.Client(timeout=TIMEOUT) as http:
            found = http.get(f"{CIMA}/medicamentos", params={"nombre": name}).json()
            key = _plain(name)
            # Products whose name starts with the word, or whose active ingredient is the word
            matches = [r for r in found.get("resultados", [])
                       if _plain(r.get("nombre", "")).startswith(key)
                       or _plain((r.get("vtm") or {}).get("nombre", "")) == key]
            if not matches:
                # Active ingredients that no product is named after (penicilina): search
                # by ingredient instead
                found = http.get(f"{CIMA}/medicamentos", params={"practiv1": name}).json()
                matches = [r for r in found.get("resultados", [])
                           if key in _plain((r.get("vtm") or {}).get("nombre", ""))]
            if not matches:
                return {}
            best = next((r for r in matches if r.get("comerc")), matches[0])
            detail = http.get(f"{CIMA}/medicamento", params={"nregistro": best["nregistro"]}).json()
    except (httpx.HTTPError, ValueError, KeyError):
        return None
    atcs = sorted(detail.get("atcs") or [], key=lambda a: len(a.get("codigo", "")))
    # An active ingredient (ibuprofeno) has many presentations, some with prescription and
    # some without; a brand name (Cerazet) is one product
    generic = any(key in _plain((r.get("vtm") or {}).get("nombre", "")) for r in matches)
    rx = {bool(r.get("receta")) for r in matches}
    return {
        # One concrete product only when there is no doubt; with several presentations
        # (Nolotil: capsules and injection) naming one misleads the model
        "product": "" if generic or len(matches) > 1 else best.get("nombre", ""),
        "active": detail.get("pactivos") or (best.get("vtm") or {}).get("nombre", ""),
        "group": atcs[0]["nombre"].capitalize() if atcs else "",
        "prescription": rx.pop() if len(rx) == 1 else None,
    }


def unknown_answer(names: list[str]) -> str:
    """Fixed answer for medicines that are not in CIMA. Written in code, because the model
    either invented what they were or answered something unrelated."""
    listed = ", ".join(f"«{n}»" for n in names)
    one = len(names) == 1
    return (
        f"**No conozco {listed}.** {'No aparece' if one else 'No aparecen'} en **CIMA**, la base "
        f"oficial de medicamentos autorizados en España, así que no puedo decirte qué "
        f"{'es' if one else 'son'} ni para qué {'sirve' if one else 'sirven'} sin arriesgarme a "
        f"inventarlo.\n\n"
        f"- **Revisa el nombre** en la caja, el prospecto o la receta: puede haber una letra "
        f"cambiada.\n"
        f"- **Pregunta a tu farmacéutico o a tu médico**, que pueden confirmarlo al momento.\n"
        f"- Si es un producto comprado fuera de España o por internet, pregunta antes de tomarlo."
    )


def analyse(message: str) -> tuple[str, list[str]]:
    """(lines with official facts to append for the model, names that are not in CIMA)."""
    lines, unknown = [], []
    # The [Contexto: ...] line lists the person's allergies and medicines: they get official
    # facts if CIMA knows them, but are never reported as unknown (that is only for names
    # the person asks about)
    context = "\n".join(l for l in message.splitlines() if l.lstrip().startswith("[Contexto"))
    question = "\n".join(l for l in message.splitlines() if not l.lstrip().startswith("[Contexto"))
    asked = candidate_names(question)
    for name in asked + [n for n in candidate_names(context) if n not in asked]:
        info = lookup(name)
        if info is None:
            continue  # CIMA unreachable: say nothing rather than something wrong
        if not info:
            if name in asked:
                unknown.append(name)
        else:
            rx = {True: "necesita receta médica", False: "no necesita receta",
                  None: "según la presentación, con o sin receta"}[info["prescription"]]
            product = f"{info['product']}; " if info["product"] else ""
            group = f"; grupo: {info['group']}" if info["group"] else ""
            lines.append(
                f"[Datos oficiales de CIMA (AEMPS) sobre «{name}»: {product}principio "
                f"activo: {info['active'].lower()}{group}; {rx}. Explica qué es usando solo "
                f"estos datos, aunque creas saber otra cosa.]"
            )
    return "\n".join(lines), unknown


def facts_for(message: str) -> str:
    return analyse(message)[0]


load_index()

if __name__ == "__main__":
    print("medicine names in the index:", build_index())
