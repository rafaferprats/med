"""Synthetic lab reports (invented data) as PDF files, with the expected reading of each one.

They copy the formats of real labs: columns, dotted leaders, brackets, the range before the
unit, flags (H, L, *), decimal commas, "250.000 /mm3", mmol/L units, urine tests, several
pages with repeated headers... Used by tests/test_labs.py and eval/run_lab_eval.py, and to
try the feature by hand:

    ./venv/bin/python eval/lab_reports.py      # writes eval/labs/*.pdf

The PDFs are written by hand (a few PDF objects with the standard Helvetica font), so no
extra library is needed.
"""
from pathlib import Path

OUT = Path(__file__).resolve().parent / "labs"


# ---------- Minimal PDF writer ----------

def _escape(text: str) -> bytes:
    raw = text.encode("cp1252")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def make_pdf(pages: list[list[tuple]]) -> bytes:
    """pages: lists of (x, y, size, text, bold) items, or ("rect",) for a drawing only."""
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    bold = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
    pages_id = len(objects) + 1 + 2 * len(pages)  # reserved: after every page and its content
    page_ids = []
    for items in pages:
        stream = b""
        for item in items:
            if item[0] == "rect":
                stream += b"0.8 g 60 300 480 400 re f 0 g\n"
                continue
            x, y, size, text, is_bold = item
            stream += b"BT /%s %d Tf %d %d Td (" % (b"F2" if is_bold else b"F1", size, x, y) + _escape(text) + b") Tj ET\n"
        content = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        page_ids.append(add(
            b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 595 842] /Contents %d 0 R "
            b"/Resources << /Font << /F1 %d 0 R /F2 %d 0 R >> >> >>" % (pages_id, content, font, bold)))
    kids = b" ".join(b"%d 0 R" % p for p in page_ids)
    assert add(b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, len(page_ids))) == pages_id
    catalog = add(b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id)

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, catalog, xref)
    return bytes(out)


# ---------- Report layouts ----------

HEADER = [
    "LABORATORIO DE ANÁLISIS CLÍNICOS (DATOS DE PRUEBA)",
    "Paciente: Persona de Prueba        DNI: 00000000T        NHC: 123456",
    "Fecha de nacimiento: 12/05/1980        Edad: 45 años        Sexo: {sex}",
    "Nº petición: 2026-004512        Fecha de extracción: 14/10/2026 08:32",
    "Médico solicitante: Dra. Ejemplo        Centro: C.S. Ejemplo, Tel. 912 345 678",
]


def _row(style: str, name: str, value: str, unit: str, ref: str, flag: str = "", prev: str = ""):
    if style == "columns":
        cells = [(50, name), (235, f"{value} {flag}".strip()), (305, unit), (395, ref), (505, prev)]
    elif style == "brackets":
        cells = [(50, name), (225, f"{value} {flag}".strip()), (290, unit), (380, f"[{ref}]" if ref else "")]
    elif style == "ref_first":
        cells = [(50, name), (225, value), (300, ref), (410, unit)]
    elif style == "flag_first":
        cells = [(50, name), (220, flag), (240, value), (310, unit), (395, ref)]
    elif style == "leaders":
        dots = "." * max(3, 34 - len(name))
        cells = [(50, f"{name.upper()} {dots} {value} {unit}   ({ref})" if ref else f"{name.upper()} {dots} {value} {unit}")]
    elif style == "colon":
        cells = [(50, f"{name}: {value} {unit}" + (f"      ({ref})" if ref else ""))]
    else:
        raise ValueError(style)
    return [c for c in cells if c[1]]


COLUMN_TITLES = {
    "columns": [(50, "Prueba"), (235, "Resultado"), (305, "Unidades"), (395, "Valores de referencia")],
    "brackets": [(50, "Determinación"), (225, "Resultado"), (290, "Unidades"), (380, "Intervalo")],
    "ref_first": [(50, "Prueba"), (225, "Resultado"), (300, "Referencia"), (410, "Unidades")],
    "flag_first": [(50, "Prueba"), (240, "Resultado"), (310, "Unidades"), (395, "Valores de referencia")],
}


def render(report: dict) -> bytes:
    if report.get("scanned"):
        return make_pdf([[("rect",)]])
    pages = []
    for page_rows in report["pages"]:
        items, y = [], 800
        for line in (HEADER if report.get("header", True) else []):
            items.append((50, y, 9 if y < 800 else 11, line.format(sex=report.get("sex_label", "")), y == 800))
            y -= 14
        y -= 10
        style = report.get("style", "columns")
        for x, title in COLUMN_TITLES.get(style, []):
            items.append((x, y, 9, title, True))
        y -= 18
        for row in page_rows:
            if isinstance(row, str):  # section title or free text
                y -= 6
                items.append((50, y, 10, row, row.isupper()))
                y -= 16
                continue
            for x, text in _row(style, *row):
                items.append((x, y, 9, text, False))
            y -= 14
        items.append((50, 40, 8, f"Página {len(pages) + 1} de {len(report['pages'])}. Valores marcados con * o H/L "
                                  "fuera del intervalo de referencia.", False))
        pages.append(items)
    return make_pdf(pages)


# ---------- The reports ----------
# expect: {analyte id: (value, status)} with optional third item: "slight" or "critical".
# Results not listed in expect are not checked one by one, but every abnormal result must be listed.

REPORTS = [
    {
        "file": "01_ferropenia_columnas.pdf", "sex": "female", "sex_label": "Mujer", "style": "columns",
        "pages": [[
            "HEMOGRAMA",
            ("Hematíes", "4,52", "x10^6/µL", "4,00 - 5,20"),
            ("Hemoglobina", "11,2", "g/dL", "12,0 - 16,0", "*"),
            ("Hematocrito", "34,8", "%", "36,0 - 46,0", "*"),
            ("VCM", "77,0", "fL", "80,0 - 100,0", "*"),
            ("HCM", "24,8", "pg", "27,0 - 33,0", "*"),
            ("CHCM", "32,2", "g/dL", "32,0 - 36,0"),
            ("ADE", "15,8", "%", "11,5 - 14,5", "*"),
            ("Leucocitos", "6,80", "x10^3/µL", "4,00 - 11,00"),
            ("Neutrófilos %", "58,2", "%", "40,0 - 75,0"),
            ("Linfocitos %", "31,0", "%", "20,0 - 45,0"),
            ("Plaquetas", "312", "x10^3/µL", "150 - 400"),
            "BIOQUÍMICA",
            ("Glucosa", "92", "mg/dL", "70 - 110"),
            ("Creatinina", "0,78", "mg/dL", "0,50 - 1,10"),
            ("Hierro", "38", "µg/dL", "50 - 170", "*"),
            ("Ferritina", "8", "ng/mL", "15 - 150", "*"),
            ("TSH", "2,10", "µUI/mL", "0,40 - 4,00"),
        ]],
        "expect": {
            "hematies": (4.52, "normal"), "hemoglobina": (11.2, "low", "slight"), "hematocrito": (34.8, "low", "slight"),
            "vcm": (77.0, "low", "slight"), "hcm": (24.8, "low"), "chcm": (32.2, "normal"), "ade": (15.8, "high"),
            "leucocitos": (6.8, "normal"), "neutrofilos_pct": (58.2, "normal"), "linfocitos_pct": (31.0, "normal"),
            "plaquetas": (312, "normal"), "glucosa": (92, "normal"), "creatinina": (0.78, "normal"),
            "hierro": (38, "low"), "ferritina": (8, "low"), "tsh": (2.1, "normal"),
        },
        "mentions": ["hierro", "anemia"],
    },
    {
        "file": "02_lipidos_corchetes.pdf", "sex": "male", "sex_label": "Hombre", "style": "brackets",
        "pages": [[
            "PERFIL LIPÍDICO Y METABÓLICO",
            ("Colesterol total", "238", "mg/dL", "< 200", "H"),
            ("Colesterol HDL", "38", "mg/dL", "> 40", "L"),
            ("Colesterol LDL", "162", "mg/dL", "< 130", "H"),
            ("Triglicéridos", "190", "mg/dL", "< 150", "H"),
            ("Glucosa", "104", "mg/dL", "70-100", "H"),
            ("Hemoglobina A1c", "5.9", "%", "4.0-5.6", "H"),
            ("Ácido úrico", "7.8", "mg/dL", "3.5-7.2", "H"),
            ("GOT/AST", "28", "U/L", "0-40"),
            ("GPT/ALT", "52", "U/L", "0-41", "H"),
            ("GGT", "45", "U/L", "0-60"),
            ("Filtrado glomerular (CKD-EPI)", ">90", "mL/min/1.73m2", "> 60"),
        ]],
        "expect": {
            "colesterol": (238, "high"), "hdl": (38, "low", "slight"), "ldl": (162, "high"), "trigliceridos": (190, "high"),
            "glucosa": (104, "high", "slight"), "hba1c": (5.9, "high", "slight"), "urico": (7.8, "high", "slight"),
            "got": (28, "normal"), "gpt": (52, "high"), "ggt": (45, "normal"), "filtrado": (90, "normal"),
        },
        "mentions": ["colesterol", "LDL"],
    },
    {
        "file": "03_infeccion_rango_antes.pdf", "sex": "male", "sex_label": "Hombre", "style": "ref_first",
        "pages": [[
            "HEMATOLOGÍA",
            ("Hemoglobina", "15,1", "g/dL", "13,5-17,5"),
            ("Leucocitos", "12,40", "x10^3/µL", "4,00-11,00"),
            ("Neutrófilos", "9,10", "x10^3/µL", "2,00-7,50"),
            ("Linfocitos", "2,20", "x10^3/µL", "1,00-4,00"),
            ("Plaquetas", "280", "x10^3/µL", "150-400"),
            ("VSG", "35", "mm/h", "0-15"),
            "BIOQUÍMICA",
            ("Proteína C reactiva", "48,5", "mg/L", "0,0-5,0"),
            ("Sodio", "139", "mmol/L", "135-145"),
            ("Potasio", "4,3", "mmol/L", "3,5-5,1"),
        ]],
        "expect": {
            "hemoglobina": (15.1, "normal"), "leucocitos": (12.4, "high"), "neutrofilos": (9.1, "high"),
            "linfocitos": (2.2, "normal"), "plaquetas": (280, "normal"), "vsg": (35, "high"), "pcr": (48.5, "high"),
            "sodio": (139, "normal"), "potasio": (4.3, "normal"),
        },
        "mentions": ["infección"],
    },
    {
        "file": "04_valores_criticos.pdf", "sex": "male", "sex_label": "Hombre", "style": "columns",
        "pages": [[
            "HEMOGRAMA",
            ("Hemoglobina", "6,5", "g/dL", "13,5 - 17,5", "*"),
            ("Plaquetas", "15", "x10^3/µL", "150 - 400", "*"),
            ("Leucocitos", "5,20", "x10^3/µL", "4,00 - 11,00"),
            "BIOQUÍMICA",
            ("Glucosa", "42", "mg/dL", "70 - 110", "*"),
            ("Sodio", "128", "mmol/L", "135 - 145", "*"),
            ("Potasio", "6,8", "mmol/L", "3,5 - 5,1", "*"),
            ("Creatinina", "2,4", "mg/dL", "0,7 - 1,3", "*"),
        ]],
        "expect": {
            "hemoglobina": (6.5, "low", "critical"), "plaquetas": (15, "low", "critical"),
            "leucocitos": (5.2, "normal"), "glucosa": (42, "low", "critical"), "sodio": (128, "low"),
            "potasio": (6.8, "high", "critical"), "creatinina": (2.4, "high"),
        },
        "critical": 4,
        "mentions": ["urgencias"],
    },
    {
        "file": "05_miles_por_mm3.pdf", "sex": "female", "sex_label": "Mujer", "style": "columns",
        "pages": [[
            "HEMOGRAMA",
            ("Hematíes", "4.350.000", "/mm3", "4.000.000 - 5.200.000"),
            ("Hemoglobina", "12,8", "g/dL", "12,0 - 16,0"),
            ("Leucocitos", "3.200", "/mm3", "4.000 - 11.000", "*"),
            ("Neutrófilos", "1.100", "/mm3", "2.000 - 7.500", "*"),
            ("Linfocitos", "1.800", "/mm3", "1.000 - 4.000"),
            ("Plaquetas", "245.000", "/mm3", "150.000 - 400.000"),
            "BIOQUÍMICA",
            ("Glucosa", "88", "mg/dL", "70 - 110"),
            ("Vitamina B12", "180", "pg/mL", "200 - 900", "*"),
            ("Ácido fólico", "2,1", "ng/mL", "3,0 - 20,0", "*"),
        ]],
        "expect": {
            "hematies": (4350000, "normal"), "hemoglobina": (12.8, "normal"), "leucocitos": (3200, "low"),
            "neutrofilos": (1100, "low"), "linfocitos": (1800, "normal"), "plaquetas": (245000, "normal"),
            "glucosa": (88, "normal"), "b12": (180, "low", "slight"), "folato": (2.1, "low"),
        },
        "mentions": ["B12"],
    },
    {
        "file": "06_tiroides_dos_puntos.pdf", "sex": "female", "sex_label": "Mujer", "style": "colon",
        "pages": [[
            "HORMONAS Y VITAMINAS",
            ("TSH", "7,85", "µUI/mL", "0,40 - 4,00"),
            ("T4 libre", "0,82", "ng/dL", "0,90 - 1,70"),
            ("25-OH Vitamina D", "14,2", "ng/mL", "30 - 100"),
            ("Vitamina B12", "420", "pg/mL", "200 - 900"),
            ("Ferritina", "45", "ng/mL", "15 - 150"),
            ("Calcio", "9,4", "mg/dL", "8,5 - 10,5"),
            ("Magnesio", "1,9", "mg/dL", "1,7 - 2,4"),
        ]],
        "expect": {
            "tsh": (7.85, "high"), "t4": (0.82, "low", "slight"), "vitd": (14.2, "low"), "b12": (420, "normal"),
            "ferritina": (45, "normal"), "calcio": (9.4, "normal"), "magnesio": (1.9, "normal"),
        },
        "mentions": ["tiroides", "vitamina D"],
    },
    {
        "file": "07_orina.pdf", "sex": "male", "sex_label": "Hombre", "style": "columns",
        "pages": [[
            "BIOQUÍMICA",
            ("Glucosa", "96", "mg/dL", "70 - 110"),
            ("Urea", "34", "mg/dL", "10 - 50"),
            ("Creatinina", "0,95", "mg/dL", "0,70 - 1,30"),
            "SISTEMÁTICO DE ORINA",
            ("pH", "6,0", "", "5,0 - 8,0"),
            ("Densidad", "1.025", "", "1.005 - 1.030"),
            ("Glucosa", "Negativo", "", "Negativo"),
            ("Proteínas", "Indicios", "", "Negativo"),
            ("Leucocitos", "Positivo (+++)", "", "Negativo"),
            ("Nitritos", "Positivo", "", "Negativo"),
            ("Hematíes", "Negativo", "", "Negativo"),
            ("Cuerpos cetónicos", "Negativo", "", "Negativo"),
        ]],
        "expect": {
            "glucosa": (96, "normal"), "urea": (34, "normal"), "creatinina": (0.95, "normal"),
            "ph_orina": (6.0, "normal"), "densidad_orina": (1.025, "normal"), "glucosa_orina": (None, "normal"),
            "proteinas_orina": (None, "positive"), "leucocitos_orina": (None, "positive"),
            "nitritos": (None, "positive"), "sangre_orina": (None, "normal"), "cetonas": (None, "normal"),
        },
        "mentions": ["orina"],
    },
    {
        "file": "08_mayusculas_puntos.pdf", "sex": "male", "sex_label": "Hombre", "style": "leaders",
        "pages": [[
            "BIOQUÍMICA GENERAL",
            ("Glucosa", "98", "mg/dl", "74 - 106"),
            ("Urea", "41", "mg/dl", "17 - 43"),
            ("Creatinina", "1,45", "mg/dl", "0,70 - 1,20"),
            ("Filtrado glomerular (CKD-EPI)", "52", "mL/min/1.73m2", "> 60"),
            ("GOT", "22", "U/L", "5 - 34"),
            ("GPT", "19", "U/L", "0 - 55"),
            ("Fosfatasa alcalina", "88", "U/L", "40 - 150"),
            ("Bilirrubina total", "1,9", "mg/dl", "0,2 - 1,2"),
            ("Potasio", "5,4", "mmol/L", "3,5 - 5,1"),
            ("PSA total", "5,6", "ng/ml", "0 - 4"),
            ("Homocisteína", "18", "µmol/L", "5 - 15"),
        ]],
        "expect": {
            "glucosa": (98, "normal"), "urea": (41, "normal"), "creatinina": (1.45, "high"),
            "filtrado": (52, "low"), "got": (22, "normal"), "gpt": (19, "normal"), "fa": (88, "normal"),
            "bilirrubina": (1.9, "high"), "potasio": (5.4, "high", "slight"), "psa": (5.6, "high"),
            "?homocisteina": (18, "high"),
        },
        "mentions": ["riñón"],
    },
    {
        "file": "09_sin_rangos.pdf", "sex": "female", "sex_label": "Mujer", "style": "columns",
        "pages": [[
            "BIOQUÍMICA",
            ("Hemoglobina", "13,1", "g/dL", ""),
            ("Glucosa", "132", "mg/dL", ""),
            ("Colesterol total", "215", "mg/dL", ""),
            ("Colesterol LDL", "98", "mg/dL", ""),
            ("Colesterol HDL", "62", "mg/dL", ""),
            ("Triglicéridos", "120", "mg/dL", ""),
            ("Hierro", "70", "µg/dL", ""),
        ]],
        "expect": {
            "hemoglobina": (13.1, "normal"), "glucosa": (132, "high"), "colesterol": (215, "high", "slight"),
            "ldl": (98, "normal"), "hdl": (62, "normal"), "trigliceridos": (120, "normal"), "hierro": (70, "normal"),
        },
        "mentions": ["glucosa"],
    },
    {
        "file": "10_varias_paginas_mmol.pdf", "sex": "male", "sex_label": "Hombre", "style": "flag_first",
        "pages": [
            ["BIOQUÍMICA",
             ("Glucosa", "7,8", "mmol/L", "3,9 - 5,6", "H"),
             ("Colesterol total", "6,2", "mmol/L", "< 5,2", "H"),
             ("Colesterol LDL", "4,1", "mmol/L", "< 3,4", "H"),
             ("Potasio", "3,2", "mmol/L", "3,5 - 5,1", "L"),
             ("Creatinina", "88", "µmol/L", "62 - 106"),
             ("INR", "1,0", "", "0,8 - 1,2")],
            ["HEMATOLOGÍA",
             ("Hemoglobina", "14,2", "g/dL", "13,5 - 17,5"),
             ("Leucocitos", "7,1", "x10^9/L", "4,0 - 11,0"),
             ("Plaquetas", "210", "x10^9/L", "150 - 400"),
             ("Proteína C reactiva", "< 0,5", "mg/L", "< 5,0")],
        ],
        "expect": {
            "glucosa": (7.8, "high"), "colesterol": (6.2, "high"), "ldl": (4.1, "high"), "potasio": (3.2, "low", "slight"),
            "creatinina": (88, "normal"), "inr": (1.0, "normal"), "hemoglobina": (14.2, "normal"),
            "leucocitos": (7.1, "normal"), "plaquetas": (210, "normal"), "pcr": (0.5, "normal"),
        },
        "mentions": ["glucosa"],
    },
]

# PDFs that must be refused, with a word the message must contain
BAD_REPORTS = [
    {"file": "11_escaneado.pdf", "scanned": True, "error": "escaneo"},
    {"file": "12_no_es_analitica.pdf", "style": "colon", "header": False, "error": "no parece un documento médico",
     "pages": [["FACTURA", "Taller de bicicletas Ejemplo. Factura nº 2026/123.",
                "Concepto: reparación de bicicleta y cambio de cadena.",
                "Importe: 45,00 euros. IVA (21%): 9,45 euros. Total: 54,45 euros.",
                "Forma de pago: tarjeta. Gracias por su visita."]]},
    {"file": "13_informe_de_alta.pdf", "style": "colon", "header": False, "error": "parece un documento médico",
     "pages": [["INFORME DE ALTA HOSPITALARIA", "Servicio de Medicina Interna. Hospital Ejemplo.",
                "Paciente de 67 años que ingresa por neumonía adquirida en la comunidad.",
                "Exploración: buen estado general, crepitantes en base derecha.",
                "Diagnóstico principal: neumonía. Tratamiento al alta: amoxicilina-clavulánico 875/125 mg cada 8 horas "
                "durante 7 días.", "Control por su médico de atención primaria en una semana."]]},
    {"file": "14_receta.pdf", "style": "colon", "header": False, "error": "parece un documento médico",
     "pages": [["RECETA MÉDICA", "Médico: Dra. Ejemplo. Nº colegiado 28/12345.",
                "Medicamento: Omeprazol 20 mg, 28 cápsulas.", "Posología: 1 comprimido al día antes del desayuno.",
                "Duración del tratamiento: 4 semanas."]]},
    {"file": "15_documento_largo.pdf", "style": "colon", "header": False, "error": "no parece un documento médico",
     "pages": [[f"Capítulo {n}. Manual de instrucciones de la lavadora modelo X-{n}00.",
                "Conecte la toma de agua y el desagüe antes del primer uso.",
                f"Programa {n}: algodón a 40 grados, 1200 revoluciones, 2 horas y {n} minutos."] for n in range(1, 26)]},
]


def build(out: Path = OUT) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for report in REPORTS + BAD_REPORTS:
        path = out / report["file"]
        path.write_bytes(render(report))
        paths.append(path)
    return paths


if __name__ == "__main__":
    for p in build():
        print(p)
