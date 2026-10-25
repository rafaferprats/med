"""Lab report PDFs: reading, classification, critical values, the number filter and the API.

Runs without Ollama (the model is faked):
    ./venv/bin/python tests/test_labs.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.update({"DB_PATH": str(Path(tempfile.mkdtemp()) / "test.db"), "FREE_LABS": "1",
                   "QUESTIONS_PER_MINUTE": "1000", "SIGNUPS_PER_HOUR": "1000"})
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))

from fastapi.testclient import TestClient  # noqa: E402

import labs  # noqa: E402
import main  # noqa: E402
from lab_reports import BAD_REPORTS, REPORTS, render  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))


def key(r):
    return r["analyte"] or "?" + labs.fold(r["raw_name"]).replace("í", "i")


# ---------- 1. The synthetic reports, value by value ----------

def test_reports():
    for rep in REPORTS:
        a = labs.analyse(render(rep), sex=rep["sex"])
        got = {key(r): r for r in a["results"]}
        for k, exp in rep["expect"].items():
            r = got.get(k)
            name = f"{rep['file']}: {k}"
            if r is None:
                check(name, False, "no leído")
                continue
            value, status = exp[0], exp[1]
            ok = (value is None or (r["value"] is not None and abs(r["value"] - value) < 1e-6)) \
                and r["status"] == status and ("slight" in exp) == r["slight"] and ("critical" in exp) == r["critical"]
            check(name, ok, f"leído {r['value']} {r['status']} slight={r['slight']} critical={r['critical']} "
                            f"unidad={r['unit']!r} rango={r['ref_text']!r}; esperado {exp}")
        extra = [k for k in got if k not in rep["expect"]]
        check(f"{rep['file']}: nada leído de más", not extra, f"de más: {extra}")
        if "critical" in rep:
            check(f"{rep['file']}: {rep['critical']} valores críticos y aviso",
                  len(a["critical"]) == rep["critical"] and a["critical_message"])
        else:
            check(f"{rep['file']}: sin avisos críticos", not a["critical"] and not a["critical_message"])
        # Nothing personal from the header goes into the result
        text = json.dumps(a, ensure_ascii=False)
        check(f"{rep['file']}: sin datos personales", not any(
            s in text for s in ("Persona de Prueba", "00000000T", "123456", "12/05/1980", "Dra. Ejemplo", "912 345 678")))


def test_bad_reports():
    for rep in BAD_REPORTS:
        try:
            labs.analyse(render(rep))
            check(f"{rep['file']}: rechazado", False, "no dio error")
        except labs.LabError as e:
            check(f"{rep['file']}: rechazado con mensaje claro", rep["error"] in str(e), str(e))
    # A document that is not medical stops after the first pages: the rest is never read
    read = []
    original = labs.page_lines

    def counting(pages):
        read.append(len(pages))
        return original(pages)
    labs.page_lines = counting
    try:
        labs.analyse(render(BAD_REPORTS[-1]))
    except labs.LabError:
        pass
    finally:
        labs.page_lines = original
    check("documento de 25 páginas no médico: se detiene tras las primeras", read == [labs.FIRST_PAGES], str(read))
    for rep in REPORTS:
        signals = labs.check_medical(labs.extract_lines(render(rep)))
        check(f"{rep['file']}: reconocida como analítica", signals["known_tests"] >= 3)
    for name, data in (("PDF dañado", b"%PDF-1.4 esto no es un pdf"), ("archivo vacío", b"")):
        try:
            labs.analyse(data)
            check(f"{name}: rechazado", False)
        except labs.LabError:
            check(f"{name}: rechazado", True)


# ---------- 2. Line formats seen in real reports ----------
# (line, analyte, value, status, unit) — status after classify with the line's own range

LINES = [
    ("Glucosa 105 mg/dL 70 - 110", "glucosa", 105, "normal", "mg/dL"),
    ("GLUCOSA.......... 126 mg/dl [74-106]", "glucosa", 126, "high", "mg/dl"),
    ("Glucosa (ayunas)   98   mg/dL   70-100", "glucosa", 98, "normal", "mg/dL"),
    ("Glucemia basal: 64 mg/dl (70 - 110)", "glucosa", 64, "low", "mg/dl"),
    ("Hemoglobina 13,2H g/dL 12,0 - 16,0", "hemoglobina", 13.2, "normal", "g/dL"),
    ("Hemoglobina   10.4  L   g/dL   12.0-15.5", "hemoglobina", 10.4, "low", "g/dL"),
    ("Leucocitos 7.5 10^3/µL 4.0 - 11.0", "leucocitos", 7.5, "normal", "10^3/µL"),
    ("Leucocitos 13,9 * x10e3/uL 4,0 - 10,5", "leucocitos", 13.9, "high", "x10e3/uL"),
    ("Plaquetas 250.000 /mm3 150.000 - 450.000", "plaquetas", 250000, "normal", "/mm3"),
    ("Plaquetas 98 x10^9/L 150 - 400", "plaquetas", 98, "low", "x10^9/L"),
    ("Neutrófilos 3,50 x10^3/µL 2,00 - 7,50", "neutrofilos", 3.5, "normal", "x10^3/µL"),
    ("Neutrófilos % 81,2 % 40 - 75", "neutrofilos_pct", 81.2, "high", "%"),
    ("Linfocitos 18 % 20 - 45", "linfocitos_pct", 18, "low", "%"),
    ("Colesterol total 230 * mg/dL Deseable < 200", "colesterol", 230, "high", "mg/dL"),
    ("Colesterol HDL 52 mg/dL > 40", "hdl", 52, "normal", "mg/dL"),
    ("LDL-Colesterol 145 mg/dL hasta 130", "ldl", 145, "high", "mg/dL"),
    ("Triglicéridos 98 mg/dl < 150", "trigliceridos", 98, "normal", "mg/dl"),
    ("GOT (AST) 45 U/L 5 - 40", "got", 45, "high", "U/L"),
    ("GPT/ALT 23 UI/L 0 a 41", "gpt", 23, "normal", "UI/L"),
    ("Gamma GT 120 U/L 8 - 61", "ggt", 120, "high", "U/L"),
    ("Filtrado glomerular estimado (CKD-EPI) >90 mL/min/1,73m2 >60", "filtrado", 90, "normal", "mL/min/1,73m2"),
    ("FG (CKD-EPI) 48 mL/min/1.73 m2 > 60", "filtrado", 48, "low", "mL/min/1.73 m2"),
    ("Proteína C reactiva <0,5 mg/L 0 - 5", "pcr", 0.5, "normal", "mg/L"),
    ("PCR 3,2 mg/dL 0,0 - 0,5", "pcr", 3.2, "high", "mg/dL"),
    ("TSH 0,02 µUI/mL 0,35 - 4,94", "tsh", 0.02, "low", "µUI/mL"),
    ("T4 libre 1,21 ng/dL 0,70 - 1,48", "t4", 1.21, "normal", "ng/dL"),
    ("Vitamina D 25-OH 18 ng/mL 30 - 100", "vitd", 18, "low", "ng/mL"),
    ("25-OH Vitamina D3 41,5 ng/ml (30,0 - 100,0)", "vitd", 41.5, "normal", "ng/ml"),
    ("Hemoglobina glicosilada (HbA1c) 6,8 % 4,0 - 5,6", "hba1c", 6.8, "high", "%"),
    ("Ferritina 312 ng/mL 15-150", "ferritina", 312, "high", "ng/mL"),
    ("Sodio 141 mEq/L 136 - 146", "sodio", 141, "normal", "mEq/L"),
    ("K+ 3,1 mmol/L 3,5 - 5,1", "potasio", 3.1, "low", "mmol/L"),
    ("Creatinina 1,02 mg/dL 0,67-1,17", "creatinina", 1.02, "normal", "mg/dL"),
    ("Ácido úrico 8,9 mg/dL 3,4 - 7,0", "urico", 8.9, "high", "mg/dL"),
    ("INR 2,6 0,8 - 1,2", "inr", 2.6, "high", ""),
    ("PSA total 1,2 ng/mL 0,0 - 4,0", "psa", 1.2, "normal", "ng/mL"),
    ("Hierro 1.234,5 µg/dL 50 - 170", "hierro", 1234.5, "high", "µg/dL"),
]

NOT_RESULTS = [
    "Paciente: Persona de Prueba   DNI: 12345678Z",
    "Fecha de nacimiento: 01/02/1975   Edad: 50 años",
    "Nº de petición 2026-000123   Fecha: 03/03/2026",
    "Página 1 de 3",
    "Centro de salud Ejemplo. Tel. 912 345 678",
    "C/ Mayor 12, 28001 Madrid",
    "Prueba   Resultado   Unidades   Valores de referencia",
    "Validado por: Dr. Ejemplo   08:45",
]


def test_lines():
    for line, analyte, value, status, unit in LINES:
        r = labs.parse_line(line)
        if r is None:
            check(f"línea: {line}", False, "no leída")
            continue
        labs.classify(r)
        ok = r.analyte == analyte and abs(r.value - value) < 1e-6 and r.status == status and r.unit == unit
        check(f"línea: {line}", ok, f"leído {r.analyte} {r.value} {r.status} unidad={r.unit!r} rango={r.ref_text!r}")
    for line in NOT_RESULTS:
        check(f"no es resultado: {line}", labs.parse_line(line) is None, str(labs.parse_line(line)))


# ---------- 3. Units, default ranges and critical values ----------

def one(line, sex="", child=False):
    return labs.classify(labs.parse_line(line), sex, child)


def test_defaults_and_critical():
    r = one("Glucosa 5,4 mmol/L")
    check("sin rango, mmol/L: usa el rango de adulto convertido", r.status == "normal" and r.ref_source == "default")
    r = one("Glucosa 2,1 mmol/L 3,9 - 5,6")
    check("glucosa 2,1 mmol/L es crítica (38 mg/dL)", r.critical and r.status == "low")
    r = one("Glucosa 7,8 mmol/L 3,9 - 5,6")
    check("glucosa 7,8 mmol/L alta pero no crítica", r.status == "high" and not r.critical)
    r = one("Potasio 7,1 mEq/L")
    check("potasio 7,1 sin rango: alto y crítico", r.status == "high" and r.critical)
    r = one("Hemoglobina 12,5 g/dL", sex="female")
    check("hemoglobina 12,5 en mujer: normal", r.status == "normal")
    r = one("Hemoglobina 12,5 g/dL", sex="male")
    check("hemoglobina 12,5 en hombre: baja", r.status == "low")
    r = one("Hemoglobina 13,0 g/dL")
    check("hemoglobina 13 sin sexo: límite más estricto y aviso", r.status == "low" and r.ref_note, r)
    # GGT 42 in a woman's report was "Normal" (sex unknown → the male limit 60 was used)
    r = one("GGT 42 U/L")
    check("GGT 42 sin sexo: alto con el límite estricto", r.status == "high" and r.ref_note, r)
    check("GGT 42 en mujer: alto", one("GGT 42 U/L", sex="female").status == "high")
    check("GGT 42 en hombre: normal", one("GGT 42 U/L", sex="male").status == "normal")
    # "Vitamina D: 1,25 Dihidroxi 70,90": the 1,25 of the name was read as the value (low)
    for line in ["Vitamina D: 1,25 Dihidroxi   70,90 pg/mL   20 - 79", "1,25-Dihidroxi vitamina D  70,90  pg/mL  20 - 79",
                 "1,25 (OH)2 Vitamina D3   70.9   20-79"]:
        r = labs.parse_line(line)
        check(f"calcitriol: valor 70,9 y normal ({line})", r and r.value == 70.9 and labs.classify(r).status == "normal", r)
        check(f"calcitriol no se explica como la reserva de vitamina D ({line})", r and r.analyte is None, r)
    check("vitamina D 25-OH sigue igual", labs.parse_line("Vitamina D 25-OH  18 ng/mL  30 - 100").analyte == "vitd")
    # Sex and age written in the report pick the range
    check("sexo y edad del informe", labs.patient(["Paciente: X", "Sexo: Mujer   Edad: 52 años"]) == {"sex": "f", "age": 52})
    check("fecha de nacimiento → edad", labs.patient(["Sexo: V", "F. Nac.: 03/04/1970", "Fecha de extracción: 10/10/2026"])
          == {"sex": "m", "age": 56})
    check("«Sexo: M» es ambiguo: no se adivina", labs.patient(["Sexo: M  Edad 30"])["sex"] == "")
    r = one("Fosfatasa alcalina 260 U/L", child=True)
    check("niño sin rango del laboratorio: no se usa el rango de adulto", r.status == "unknown")
    r = one("Plaquetas 12.000 /mm3 150.000 - 400.000")
    check("plaquetas 12.000/mm3: críticas", r.critical)
    r = one("Leucocitos 5.200 /mm3 4.000 - 11.000")
    check("leucocitos 5.200/mm3: normales, no críticos", r.status == "normal" and not r.critical)
    r = one("Glucosa 45 mg/dL 70 - 110 (resultado comprobado)")
    check("crítico también con texto después", r.critical)
    r = one("Sodio 130 mmol/L 135 - 145")
    check("sodio 130: bajo, no 'ligeramente'", r.status == "low" and not r.slight)
    r = one("Colesterol total 205 mg/dL < 200")
    check("colesterol 205: ligeramente alto", r.status == "high" and r.slight)
    r = one("Glucosa 95 mg/dL")
    check("explicación fija en cada resultado", r.what and r.status == "normal")
    r = one("Ferritina 6 ng/mL 15 - 150")
    check("explicación de valor bajo", "hierro" in r.meaning)


# ---------- 4. Number filter for the model's summary ----------

def guard_run(analysis, chunks):
    g = labs.NumberGuard(analysis)
    out = "".join(g.feed(c) for c in chunks) + g.flush()
    return out, g.removed


def test_number_guard():
    a = labs.analyse(render(REPORTS[0]), sex="female")  # hemoglobina 11,2; ferritina 8...
    text = ("**Resumen:** Tienes la hemoglobina en 11,2, algo baja. La ferritina está en 3 y eso es grave. "
            "Repite la analítica en 3 meses.\n**Preguntas para tu médico:**\n1. ¿Necesito hierro?\n"
            "2. ¿Debo tomar 250 mg al día?\n3. ¿Cuándo repito el análisis?\nLlama al 112 si te mareas.")
    chunks = [text[i:i + 7] for i in range(0, len(text), 7)]  # streamed in small pieces
    out, removed = guard_run(a, chunks)
    check("filtro: mantiene valores reales del informe", "11,2" in out)
    check("filtro: quita un valor inventado", "ferritina está en 3" not in out)
    check("filtro: quita una dosis inventada", "250 mg" not in out)
    check("filtro: mantiene plazos y la lista numerada", "3 meses" in out and "1. ¿Necesito hierro?" in out and "3. ¿Cuándo" in out)
    check("filtro: mantiene el 112", "112" in out)
    check("filtro: cuenta lo quitado", removed == 2, f"quitadas {removed}: {out!r}")
    out, removed = guard_run(a, ["La HbA1c, la vitamina B12, la T4 y la vitamina D3 (25-OH) están bien. Fin."])
    check("filtro: los números dentro de nombres no son cifras", removed == 0 and "HbA1c" in out, out)
    out, _ = guard_run(a, ["Tus resultados muestran que tienes una anemia leve. Tienes diabetes."])
    check("filtro: suaviza los diagnósticos", "podrías tener una anemia" in out and "Podrías tener diabetes" in out, out)
    high_glucose = labs.analyse(render(REPORTS[1]), sex="male")  # glucosa 104: gets the prediabetes text
    out, removed = guard_run(high_glucose, ["La glucosa entre 100 y 125 en ayunas se llama prediabetes. Otra frase."])
    check("filtro: mantiene las cifras de las explicaciones fijas", "100 y 125" in out and removed == 0, out)
    out, removed = guard_run(a, ["Todo bien. Sin números aquí."])
    check("filtro: texto sin números intacto", out == "Todo bien. Sin números aquí." and removed == 0)


# ---------- 5. API ----------

class FakeChunk:
    def __init__(self, text):
        self.message = type("M", (), {"content": text})()


calls = []


async def fake_chat(**kwargs):
    calls.append(kwargs)

    async def gen():
        for part in ("**Resumen:** Tienes anemia por falta de hierro. ", "Tu ferritina es 2 y la hemoglobina 11,2.\n",
                     "**Preguntas para tu médico:**\n1. ¿Necesito hierro?"):
            yield FakeChunk(part)
    return gen()


main.client.chat = fake_chat


def client_for(email, premium=False):
    c = TestClient(main.app)
    assert c.post("/auth/register", json={"email": email, "password": "password123"}).status_code == 204
    if premium:
        assert c.post("/billing/checkout", json={"plan_id": "monthly"}).status_code == 200
    return c


def upload(c, data, name="analitica.pdf", **form):
    return c.post("/labs/analyze", files={"file": (name, data, "application/pdf")}, data=form)


def test_api():
    pdf = render(REPORTS[0])
    normal_pdf = render({**REPORTS[8], "pages": [[("Hemoglobina", "13,1", "g/dL", "12,0 - 16,0"),
                                                 ("Glucosa", "88", "mg/dL", "70 - 110"),
                                                 ("Creatinina", "0,8", "mg/dL", "0,5 - 1,1")]]})
    anon = TestClient(main.app)
    check("API: sin cuenta pide registrarse", upload(anon, pdf).status_code == 401)

    c = client_for("labs1@example.com")
    check("API: un archivo que no es PDF se rechaza", upload(c, b"hola" * 100, name="a.txt").status_code == 415)
    big = b"%PDF-1.4\n" + b"0" * (main.pdf_guard.MAX_PDF_MB * 1024 * 1024 + 10)
    check("API: un PDF demasiado grande da 413", upload(c, big).status_code == 413)
    r = upload(c, render(BAD_REPORTS[0]))
    check("API: PDF escaneado da 422 con explicación", r.status_code == 422 and "escaneo" in r.json()["detail"])
    check("API: un PDF ilegible no gasta la analítica gratis", c.get("/me").json()["remaining"]["lab"] == 1)

    r = upload(c, pdf, sex="female", context="mujer · 45 años")
    data = r.json()
    check("API: la analítica gratis se lee", r.status_code == 200 and data["counts"]["abnormal"] == 7)
    check("API: la respuesta no lleva datos personales",
          "Persona de Prueba" not in r.text and "00000000T" not in r.text)
    check("API: se gasta la analítica gratis", c.get("/me").json()["remaining"]["lab"] == 0)
    check("API: la segunda pide Premium (402)", upload(c, pdf).status_code == 402)

    calls.clear()
    s = c.post(f"/labs/{data['lab_id']}/summary", json={"session_id": data["session_id"]})
    check("API: el resumen llega por streaming", s.status_code == 200 and "Resumen" in s.text)
    check("API: el filtro quita la cifra inventada del modelo", "ferritina es 2" not in s.text and "¿Necesito hierro?" in s.text)
    sent = json.dumps(calls[0]["messages"], ensure_ascii=False) if calls else ""
    check("API: el modelo no recibe datos personales ni el PDF",
          calls and "Persona de Prueba" not in sent and "00000000T" not in sent and "%PDF" not in sent)
    prompt = calls[0]["messages"][1]["content"] if calls else ""
    check("API: el modelo no recibe los valores del informe",
          calls and not any(v in prompt for v in ("11,2", "34,8", "77,0", "24,8", "15,8", "38", "4,52", "312")), prompt[:300])
    history = main.sessions[data["session_id"]]["history"]
    check("API: la analítica queda en la conversación para seguir preguntando",
          len(history) == 2 and "Ferritina: 8 ng/mL" in history[0]["content"] and history[1]["role"] == "assistant")

    other = client_for("labs2@example.com")
    check("API: otra cuenta no puede ver el resumen",
          other.post(f"/labs/{data['lab_id']}/summary", json={"session_id": data["session_id"]}).status_code == 404)

    p = client_for("labs3@example.com", premium=True)
    codes = [upload(p, pdf).status_code for _ in range(3)]
    check("API: con Premium, analíticas sin límite", codes == [200, 200, 200])
    d = upload(p, normal_pdf).json()
    calls.clear()
    s = p.post(f"/labs/{d['lab_id']}/summary", json={"session_id": d["session_id"]})
    check("API: todo normal: resumen fijo sin llamar al modelo", "dentro de sus valores" in s.text and not calls)

    d = upload(p, render(REPORTS[3])).json()
    check("API: valores críticos con su aviso", len(d["critical"]) == 4 and "112" in d["critical_message"])
    calls.clear()
    p.post(f"/labs/{d['lab_id']}/summary", json={"session_id": d["session_id"]})
    prompt = calls[0]["messages"][1]["content"] if calls else ""
    check("API: con valores críticos, el modelo recibe la orden de avisar hoy",
          prompt.startswith("IMPORTANTE") and "Potasio" in prompt and "hoy mismo" in prompt)
    s = p.post(f"/labs/{d['lab_id']}/summary", json={"session_id": d["session_id"]})
    check("API: con valores críticos, el resumen empieza con el aviso fijo", s.text.startswith("**Importante:**"))
    check("API: lo que el modelo no nombra se añade al final", "También fuera de rango" in s.text and "Potasio" in s.text, s.text[-200:])
    crit = labs.analyse(render(REPORTS[3]), sex="male")
    out, removed = guard_run(crit, ["Habla con tu médico. Repite la analítica en unas semanas. Bebe agua."])
    check("filtro: con valores críticos quita 'repite en unas semanas'", "semanas" not in out and removed == 1, out)
    out, removed = guard_run(a_normal := labs.analyse(render(REPORTS[0]), sex="female"), ["Repite la analítica en 3 meses."])
    check("filtro: sin valores críticos sí puede proponer repetir", "3 meses" in out, out)


if __name__ == "__main__":
    for test in (test_reports, test_bad_reports, test_lines, test_defaults_and_critical, test_number_guard, test_api):
        test()
    failed = [(n, d) for n, ok, d in results if not ok]
    for n, d in failed:
        print(f"FALLA: {n}\n       {d}")
    print(f"lab tests: {len(results) - len(failed)}/{len(results)} OK")
    sys.exit(1 if failed else 0)
