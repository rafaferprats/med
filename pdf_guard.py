"""Security layer for uploaded lab reports: only real, passive PDF files get through.

Three rings, cheapest first:

1. check_upload(), in the server process, on the raw bytes (microseconds):
   size; "%PDF-1.x" / "%PDF-2.x" at byte 0 and "%%EOF" at the end; declared type and
   extension; files that are also something else (a ZIP or HTML hidden in a PDF); active
   content (JavaScript, launching programs, embedded files, forms that send data...), also
   when the name is obfuscated with #xx escapes ("/J#61vaScript").
2. analyse_isolated(), in a separate, throw-away process: lower decompression limits,
   time and CPU limits, and a check of the PDF structure (inspect()), which also sees what
   is hidden inside compressed object streams. If a crafted PDF hangs or crashes the reader,
   only that process dies.
3. Limits per account (uploads per hour, in main.py) and at most MAX_PARALLEL readings
   at the same time.

The PDF is never stored, never served back and never opened by a browser. Only the
results read from it leave the worker process.
"""
import os
import re
import threading
from typing import Optional

MAX_PDF_MB = int(os.getenv("MAX_PDF_MB", "10"))
MIN_PDF_BYTES = 200
MAX_PAGES = int(os.getenv("MAX_PDF_PAGES", "30"))
PARSE_TIMEOUT = float(os.getenv("PDF_PARSE_TIMEOUT", "20"))   # seconds for the whole reading
MAX_PARALLEL = int(os.getenv("PDF_MAX_PARALLEL", "2"))
QUEUE_TIMEOUT = 30                                            # seconds waiting for a free slot
DECOMPRESS_LIMIT = 20_000_000                                 # bytes per stream (pypdf: 75 MB)

ALLOWED_TYPES = {"application/pdf", "application/x-pdf", "application/acrobat", "application/octet-stream", ""}
HEADER = re.compile(rb"%PDF-[12]\.\d")
# Names that make a PDF do something (run code, open programs or files, send data)
ACTIVE = re.compile(rb"/(JavaScript|JS|Launch|EmbeddedFiles?|RichMedia|XFA|SubmitForm|ImportData|GoToE|Sound|Movie)"
                    rb"(?![A-Za-z0-9])")
ACTIVE_NAMES = {"/JavaScript", "/JS", "/Launch", "/EmbeddedFile", "/EmbeddedFiles", "/RichMedia", "/XFA",
                "/SubmitForm", "/ImportData", "/GoToE", "/Sound", "/Movie"}
NAME_ESCAPE = re.compile(rb"#([0-9A-Fa-f]{2})")
# Other formats glued to the PDF. Looked for only where they must be, so random bytes inside
# images or compressed text can't trigger them: the end of a ZIP (also .docx, .apk...) in the
# last 64 KB, HTML or scripts at the start or the end.
ZIP_END = re.compile(rb"PK\x05\x06")
MARKUP = re.compile(rb"<\s*(script|html|iframe|body)\b|<\?php", re.I)
# Binary blocks (images, compressed text): skipped when looking for active names
STREAMS = re.compile(rb"stream\r?\n.*?endstream", re.S)

_slots = threading.BoundedSemaphore(MAX_PARALLEL)


class PdfRejected(Exception):
    """The file is not accepted. message is for the user; reason is a short code for the logs."""

    def __init__(self, status: int, message: str, reason: str):
        super().__init__(message)
        self.status, self.message, self.reason = status, message, reason


NOT_PDF = "Solo se aceptan analíticas en PDF. Este archivo no es un PDF válido."
DAMAGED = "El PDF parece incompleto o dañado. Descárgalo de nuevo y vuelve a subirlo."
ACTIVE_CONTENT = ("Este PDF contiene elementos que no son de una analítica (código, archivos incrustados o "
                  "formularios). Por seguridad no lo abro. Descarga la analítica de nuevo desde el portal del "
                  "laboratorio, o imprímela como PDF, y vuelve a subirla.")


# ---------- Ring 1: the raw bytes ----------

def check_upload(filename: Optional[str], content_type: Optional[str], data: bytes) -> None:
    """Cheap checks before any parsing. Raises PdfRejected."""
    if len(data) > MAX_PDF_MB * 1024 * 1024:
        raise PdfRejected(413, f"El PDF ocupa más de {MAX_PDF_MB} MB.", "too_big")
    if len(data) < MIN_PDF_BYTES:
        raise PdfRejected(400, NOT_PDF if not data.startswith(b"%PDF") else DAMAGED, "too_small")
    name = (filename or "").lower()
    if name and not name.endswith(".pdf"):
        raise PdfRejected(415, NOT_PDF, "extension")
    ctype = (content_type or "").split(";")[0].strip().lower()
    if ctype not in ALLOWED_TYPES:
        raise PdfRejected(415, NOT_PDF, f"content_type:{ctype[:40]}")
    if not HEADER.match(data):  # at byte 0: nothing may come before the header
        raise PdfRejected(415, NOT_PDF, "header")
    if b"%%EOF" not in data[-2048:]:
        raise PdfRejected(400, DAMAGED, "no_eof")
    # Everything below looks only outside the binary blocks, where random bytes can't match
    plain = STREAMS.sub(b"stream endstream", data)
    if ZIP_END.search(plain[-65_600:]) or MARKUP.search(plain[:1024]) or MARKUP.search(plain[-1024:]):
        raise PdfRejected(415, NOT_PDF, "polyglot")
    # Active content in the PDF's dictionaries. "/J#61vaScript" is "/JavaScript": the escapes
    # are undone first. Inside compressed object streams it is found by inspect(), in ring 2.
    if ACTIVE.search(NAME_ESCAPE.sub(lambda m: bytes([int(m.group(1), 16)]), plain)):
        raise PdfRejected(422, ACTIVE_CONTENT, "active_content")


# ---------- Ring 2: the structure, inside the worker ----------

def _walk(obj, seen: set, depth: int = 0) -> Optional[str]:
    """First active name found in a PDF object tree (limited depth and size)."""
    from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject, NameObject

    if depth > 40 or len(seen) > 50_000:
        return None
    if isinstance(obj, IndirectObject):
        if obj.idnum in seen:
            return None
        seen.add(obj.idnum)
        try:
            obj = obj.get_object()
        except Exception:
            return None
    if isinstance(obj, NameObject):
        return str(obj) if str(obj) in ACTIVE_NAMES else None
    if isinstance(obj, DictionaryObject):
        for k, v in obj.items():
            if k in ACTIVE_NAMES:
                return k
            if k == "/Parent":  # avoid walking back up the page tree
                continue
            found = _walk(v, seen, depth + 1)
            if found:
                return found
    elif isinstance(obj, ArrayObject):
        for v in obj:
            found = _walk(v, seen, depth + 1)
            if found:
                return found
    return None


def inspect(reader) -> None:
    """Checks the parsed PDF: pages, encryption and active content anywhere in its objects
    (catalog, names, open actions, forms, pages, annotations), including compressed object
    streams that ring 1 cannot see."""
    from pypdf.errors import LimitReachedError  # noqa: F401  (imported so the worker fails fast if missing)

    if reader.is_encrypted:
        return  # labs.extract_lines explains how to remove the password
    pages = len(reader.pages)
    if pages > MAX_PAGES:
        raise PdfRejected(422, f"El PDF tiene {pages} páginas. Una analítica suele tener pocas: sube solo la "
                               f"analítica (máximo {MAX_PAGES} páginas).", "too_many_pages")
    found = _walk(reader.trailer["/Root"], set())
    if found:
        raise PdfRejected(422, ACTIVE_CONTENT, f"active_structure:{found}")


def _set_limits() -> None:
    try:
        import resource
    except ImportError:  # Windows
        return
    cpu = int(PARSE_TIMEOUT) + 5
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
    try:  # memory: works on Linux (the usual server); macOS ignores it
        resource.setrlimit(resource.RLIMIT_AS, (1536 * 1024 * 1024, 1536 * 1024 * 1024))
    except (ValueError, OSError):
        pass


def _worker() -> None:
    """The throw-away process: reads {"sex", "child"} and the PDF from stdin, writes one JSON
    line with the result to stdout. Started by analyse_isolated() with `python -I`."""
    import io
    import json
    import sys

    _set_limits()
    options = json.loads(sys.stdin.buffer.readline())
    data = sys.stdin.buffer.read()

    def reply(**msg):
        sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    import pypdf

    pypdf.overwrite_configuration(
        zlib_maximum_output_length=DECOMPRESS_LIMIT, lzw_maximum_output_length=DECOMPRESS_LIMIT,
        run_length_maximum_output_length=DECOMPRESS_LIMIT, jbig2_maximum_output_length=DECOMPRESS_LIMIT,
        array_based_stream_maximum_output_length=DECOMPRESS_LIMIT, maximum_declared_stream_length=DECOMPRESS_LIMIT,
        array_maximum_element_count=50_000, dictionary_maximum_element_count=50_000,
    )
    import labs

    try:
        try:
            inspect(pypdf.PdfReader(io.BytesIO(data)))
        except PdfRejected:
            raise
        except Exception:
            pass  # unreadable structure: labs.analyse gives the message for the user
        reply(kind="ok", data=labs.analyse(data, options.get("sex", ""), bool(options.get("child"))))
    except PdfRejected as e:
        reply(kind="rejected", status=e.status, message=e.message, reason=e.reason)
    except labs.LabError as e:
        reply(kind="lab", message=str(e))
    except Exception as e:
        reply(kind="error", message=type(e).__name__)


def analyse_isolated(data: bytes, sex: str = "", child: bool = False) -> dict:
    """labs.analyse() in a throw-away process with a time limit. Blocking: call it from a
    thread. Raises PdfRejected, or labs.LabError with the message for the user."""
    import json
    import subprocess
    import sys

    import labs

    if not _slots.acquire(timeout=QUEUE_TIMEOUT):
        raise PdfRejected(503, "Ahora mismo hay muchas analíticas en cola. Prueba en un minuto.", "busy")
    try:
        header = json.dumps({"sex": sex, "child": child}).encode() + b"\n"
        try:
            done = subprocess.run(
                [sys.executable, "-I", os.path.abspath(__file__), "--worker"],
                input=header + data, capture_output=True, timeout=PARSE_TIMEOUT,
                cwd=os.path.dirname(os.path.abspath(__file__)),
            )
        except subprocess.TimeoutExpired:  # subprocess.run has already killed it
            raise PdfRejected(422, "Este PDF tarda demasiado en procesarse. Si es una analítica, prueba a "
                                   "descargarla de nuevo o a imprimirla como PDF.", "timeout")
    finally:
        _slots.release()
    try:
        message = json.loads(done.stdout.decode().strip().splitlines()[-1])
    except (ValueError, IndexError):  # the worker died: memory or CPU limit, or a crash in the reader
        raise PdfRejected(422, DAMAGED, f"worker_died:{done.returncode}")
    kind = message["kind"]
    if kind == "ok":
        return message["data"]
    if kind == "rejected":
        raise PdfRejected(message["status"], message["message"], message["reason"])
    if kind == "lab":
        raise labs.LabError(message["message"])
    raise PdfRejected(422, DAMAGED, f"error:{message.get('message')}")


if __name__ == "__main__" and "--worker" in __import__("sys").argv:
    # Started with -I: the project folder is not on the path, so it is added by hand
    __import__("sys").path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    _worker()
