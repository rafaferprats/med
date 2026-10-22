"""Security layer for uploaded PDFs (pdf_guard.py), with real malicious files.

Runs without Ollama:
    ./venv/bin/python tests/test_pdf_guard.py
"""
import io
import os
import sys
import tempfile
import time
import zipfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.update({"DB_PATH": str(Path(tempfile.mkdtemp()) / "test.db"), "LAB_UPLOADS_PER_HOUR": "12",
                   "QUESTIONS_PER_MINUTE": "1000", "SIGNUPS_PER_HOUR": "1000"})
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))

from fastapi.testclient import TestClient  # noqa: E402
from pypdf import PdfReader, PdfWriter  # noqa: E402
from pypdf.generic import (ArrayObject, DictionaryObject, NameObject, NumberObject,  # noqa: E402
                           TextStringObject)

import labs  # noqa: E402
import main  # noqa: E402
import pdf_guard  # noqa: E402
from lab_reports import REPORTS, render  # noqa: E402

results = []
GOOD = render(REPORTS[0])


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))


def rejected(data, filename="analitica.pdf", ctype="application/pdf", isolated=True):
    """(status, reason) if the guard rejects the file, or None if it gets through."""
    try:
        pdf_guard.check_upload(filename, ctype, data)
        if isolated:
            pdf_guard.analyse_isolated(data)
        return None
    except pdf_guard.PdfRejected as e:
        return e.status, e.reason
    except labs.LabError as e:
        return 422, "lab:" + str(e)[:40]


# ---------- Malicious and broken files ----------

def write(writer: PdfWriter) -> bytes:
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def from_good() -> PdfWriter:
    return PdfWriter(clone_from=PdfReader(io.BytesIO(GOOD)))


def with_js() -> bytes:
    w = from_good()
    w.add_js("app.alert('hola');")
    return write(w)


def with_launch() -> bytes:
    w = from_good()
    w._root_object[NameObject("/OpenAction")] = DictionaryObject({
        NameObject("/S"): NameObject("/Launch"), NameObject("/F"): TextStringObject("calc.exe")})
    return write(w)


def with_attachment() -> bytes:
    w = from_good()
    w.add_attachment("factura.exe", b"MZ\x90\x00" + b"\x00" * 200)
    return write(w)


def with_submit_form() -> bytes:
    w = from_good()
    page = w.pages[0]
    annot = DictionaryObject({
        NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Link"),
        NameObject("/Rect"): ArrayObject([NumberObject(0), NumberObject(0), NumberObject(50), NumberObject(50)]),
        NameObject("/A"): DictionaryObject({NameObject("/S"): NameObject("/SubmitForm"),
                                            NameObject("/F"): TextStringObject("https://ejemplo.invalid/robar")}),
    })
    page[NameObject("/Annots")] = ArrayObject([w._add_object(annot)])
    return write(w)


def obfuscated_js() -> bytes:
    return with_js().replace(b"/JavaScript", b"/J#61vaScript").replace(b"/JS ", b"/J#53 ")


def build_raw(objects: list[bytes], root: int = 1, xref_stream: bool = False) -> bytes:
    """A PDF from raw object bodies (numbered from 1), with a classic xref table."""
    out = bytearray(b"%PDF-1.5\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, root, xref)
    return bytes(out)


def page_objects(content: bytes, extra: bytes = b"") -> list[bytes]:
    return [
        b"<< /Type /Catalog /Pages 2 0 R " + extra + b">>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        b"/Resources << /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >> >>",
        content,
    ]


def js_in_object_stream() -> bytes:
    """The JavaScript action lives inside a compressed object stream (PDF 1.5): invisible to a
    scan of the raw bytes, so only the structure check (ring 2) can find it."""
    text = b"BT /F1 12 Tf 50 800 Td (Glucosa 92 mg/dL 70 - 110) Tj ET"
    action = b"<< /S /JavaScript /JS (app.alert\\(1\\)) >>"
    inner = b"5 0 " + action
    first = len(b"5 0 ")
    packed = zlib.compress(inner)
    objs = page_objects(b"<< /Length %d >>\nstream\n" % len(text) + text + b"\nendstream", b"/OpenAction 5 0 R ")
    objs.append(None)  # 5: inside the object stream
    objs.append(b"<< /Type /ObjStm /N 1 /First %d /Filter /FlateDecode /Length %d >>\nstream\n" % (first, len(packed))
                + packed + b"\nendstream")
    # Cross-reference stream (needed to find object 5 inside object 6)
    out = bytearray(b"%PDF-1.5\n%\xe2\xe3\xcf\xd3\n")
    offsets = {}
    for i, body in enumerate(objs, 1):
        if body is None:
            continue
        offsets[i] = len(out)
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref_num = len(objs) + 1
    offsets[xref_num] = len(out)
    rows = b"\x00\x00\x00\x00\x00\xff\xff"
    for i in range(1, xref_num + 1):
        if i == 5:
            rows += b"\x02" + (6).to_bytes(4, "big") + (0).to_bytes(2, "big")
        else:
            rows += b"\x01" + offsets[i].to_bytes(4, "big") + (0).to_bytes(2, "big")
    out += (b"%d 0 obj\n<< /Type /XRef /Size %d /W [1 4 2] /Root 1 0 R /Length %d >>\nstream\n"
            % (xref_num, xref_num + 1, len(rows)) + rows + b"\nendstream\nendobj\n")
    out += b"startxref\n%d\n%%%%EOF\n" % offsets[xref_num]
    return bytes(out)


def decompression_bomb() -> bytes:
    """A page whose content is 200 MB of zeros, compressed into a few hundred KB."""
    comp = zlib.compressobj(9)
    chunk = b"\x00" * 1_000_000
    packed = b"".join(comp.compress(chunk) for _ in range(200)) + comp.flush()
    return build_raw(page_objects(b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(packed)
                                  + packed + b"\nendstream"))


def random_bytes_pdf() -> bytes:
    """A harmless PDF whose image data happens to contain '/JS', 'PK\\x05\\x06' and '<html':
    must NOT be rejected (those bytes are inside a binary block)."""
    noise = os.urandom(3000) + b"/JS /Launch PK\x05\x06 <html><script>" + os.urandom(3000)
    text = b"BT /F1 12 Tf 50 800 Td (Glucosa 92 mg/dL 70 - 110) Tj ET"
    objs = page_objects(b"<< /Length %d >>\nstream\n" % len(text) + text + b"\nendstream")
    objs.append(b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 /BitsPerComponent 8 /ColorSpace /DeviceGray "
                b"/Length %d >>\nstream\n" % len(noise) + noise + b"\nendstream")
    return build_raw(objs)


def passes_both_rings(data) -> bool:
    try:
        pdf_guard.check_upload("analitica.pdf", "application/pdf", data)
        pdf_guard.inspect(PdfReader(io.BytesIO(data)))
        return True
    except pdf_guard.PdfRejected:
        return False


def many_pages() -> bytes:
    w = from_good()
    for _ in range(40):
        w.add_blank_page(595, 842)
    return write(w)


def encrypted() -> bytes:
    w = from_good()
    w.encrypt("secreta")
    return write(w)


def zip_polyglot() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("leeme.txt", "hola")
        z.comment = b"%%EOF"
    return GOOD + buf.getvalue()


def test_guard():
    t0 = time.time()
    check("PDF bueno pasa todo", rejected(GOOD) is None)
    check("PDF bueno reescrito y comprimido por pypdf pasa",
          rejected(write(from_good())) is None)
    check("bytes al azar dentro de una imagen no lo bloquean", passes_both_rings(random_bytes_pdf()))
    cases = [
        ("archivo de texto con .pdf", b"hola, esto no es un pdf" * 20, 415, "header"),
        ("PNG renombrado a .pdf", b"\x89PNG\r\n\x1a\n" + os.urandom(500), 415, "header"),
        ("HTML renombrado a .pdf", b"<html><script>alert(1)</script></html>" * 10, 415, "header"),
        ("bytes antes de %PDF (BOM)", b"\xef\xbb\xbf" + GOOD, 415, "header"),
        ("PDF truncado", GOOD[: len(GOOD) // 2], 400, "no_eof"),
        ("archivo vacío", b"", 400, "too_small"),
        ("PDF con ZIP pegado (políglota)", zip_polyglot(), 415, "polyglot"),
        ("PDF que empieza con HTML", b"%PDF-1.4\n<html><body>x</body></html>\n" + GOOD[9:], 415, "polyglot"),
        ("PDF con JavaScript", with_js(), 422, "active_content"),
        ("PDF con JavaScript ofuscado (#61)", obfuscated_js(), 422, "active_content"),
        ("PDF que lanza un programa", with_launch(), 422, "active_content"),
        ("PDF con un archivo .exe incrustado", with_attachment(), 422, "active_content"),
        ("PDF con formulario que envía datos", with_submit_form(), 422, "active_content"),
        ("JavaScript escondido en un bloque comprimido", js_in_object_stream(), 422, "active_structure:/JavaScript"),
        ("PDF de 41 páginas", many_pages(), 422, "too_many_pages"),
    ]
    for name, data, status, reason in cases:
        got = rejected(data)
        check(f"rechaza: {name}", got == (status, reason), f"obtenido {got}, esperado {(status, reason)}")
    check("JavaScript en bloque comprimido: los bytes no lo delatan (lo ve el anillo 2)",
          rejected(js_in_object_stream(), isolated=False) is None)
    got = rejected(encrypted())
    check("rechaza: PDF con contraseña, explicando qué hacer", got and got[0] == 422 and "contrase" in got[1], str(got))
    check("rechaza: tipo declarado text/html", rejected(GOOD, ctype="text/html") == (415, "content_type:text/html"))
    check("rechaza: extensión .exe", rejected(GOOD, filename="analitica.exe") == (415, "extension"))
    check("acepta: tipo application/octet-stream (algunos móviles)", rejected(GOOD, ctype="application/octet-stream") is None)
    big = GOOD + b"0" * (pdf_guard.MAX_PDF_MB * 1024 * 1024)
    check("rechaza: más de 10 MB", rejected(big, isolated=False) == (413, "too_big"))

    t = time.time()
    got = rejected(decompression_bomb())
    check("bomba de descompresión (200 MB): rechazada sin colgar", got is not None and got[0] == 422 and time.time() - t < 15,
          f"{got}, {time.time() - t:.1f} s")
    timeout, pdf_guard.PARSE_TIMEOUT = pdf_guard.PARSE_TIMEOUT, 0.01
    try:
        got = rejected(GOOD)
    finally:
        pdf_guard.PARSE_TIMEOUT = timeout
    check("un PDF que tarda demasiado se corta (proceso eliminado)", got == (422, "timeout"), str(got))
    check("después de todo eso, un PDF bueno se sigue leyendo", rejected(GOOD) is None)
    print(f"   ({time.time() - t0:.1f} s en total)")


# ---------- Through the API ----------

def test_api():
    c = TestClient(main.app)
    assert c.post("/auth/register", json={"email": "guard@example.com", "password": "password123"}).status_code == 204

    def up(data, name="analitica.pdf", ctype="application/pdf"):
        return c.post("/labs/analyze", files={"file": (name, data, ctype)})

    r = up(with_js())
    check("API: PDF con JavaScript da 422 con explicación", r.status_code == 422 and "código" in r.json()["detail"])
    check("API: un PDF rechazado no gasta la analítica gratis", c.get("/me").json()["remaining"]["lab"] == 1)
    r = up(b"\x89PNG\r\n\x1a\n" + os.urandom(500), name="foto.png", ctype="image/png")
    check("API: una imagen en el endpoint de PDF da 415", r.status_code == 415)
    r = up(decompression_bomb())
    check("API: bomba de descompresión da 422", r.status_code == 422)
    r = up(GOOD)
    check("API: después, un PDF bueno funciona", r.status_code == 200 and r.json()["counts"]["abnormal"] == 7)
    codes = [up(GOOD).status_code for _ in range(10)]
    check("API: límite de subidas por hora (429)", codes[-1] == 429, str(codes))


if __name__ == "__main__":
    test_guard()
    test_api()
    failed = [(n, d) for n, ok, d in results if not ok]
    for n, d in failed:
        print(f"FALLA: {n}\n       {d}")
    print(f"pdf guard tests: {len(results) - len(failed)}/{len(results)} OK")
    sys.exit(1 if failed else 0)
