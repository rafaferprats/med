import asyncio
import io
from contextlib import asynccontextmanager
import secrets
import uuid
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from ollama import AsyncClient
from PIL import Image, ImageOps
from pydantic import BaseModel

import auth
import billing
import labs
import pdf_guard
from config import BILLING_MODE, COOKIE_NAME, FREE_IMAGES, FREE_QUESTIONS, GUEST_COOKIE_NAME, PLANS, TOKEN_DAYS
from db import init_db
from questions import prepare, split_context
from safety import EMERGENCY_MESSAGE, detect_emergency
import security
import stripe_billing

MODEL = "medgemma1.5:4b"
OPTIONS = {
    "temperature": 0.3,
    "top_p": 0.9,
    "top_k": 64,
    "num_ctx": 8192,        # prevents Ollama from truncating the context
    "num_predict": 2000,    # max tokens, including the model's hidden thinking
    "repeat_penalty": 1.1,  # fewer repetitions
    # No "stop" sequences: they also fire inside the model's hidden reasoning and left
    # some answers empty. Stray context lines and JSON blocks are removed when the
    # answer is shown instead (renderAnswer in static/app.js).
}
# Shown instead of an empty answer (for example when the reasoning uses all num_predict)
EMPTY_ANSWER = (
    "Lo siento, no he podido responder a esto. Prueba a reformular la pregunta "
    "o a hacerla más concreta."
)
MAX_MESSAGES = 8  # last messages (user + assistant) sent to the model
# Kept short and without example cases: a longer prompt full of examples (headaches,
# ibuprofeno with paracetamol...) made the model answer those examples instead of the
# question it was asked. No bracketed examples or lettered formats either: the model
# copied them at the end of its answers ("[Contexto: adulto]", "J) No hay contexto...").
SYSTEM_PROMPT = {
    "role": "system",
    "content": (
        "You are an informative medical assistant for people in Spain. Always answer in "
        "Spanish from Spain (castellano), using 'tú', whatever language the question is in. "
        "Start directly with the answer. Never write English, JSON or code.\n"
        "Answer exactly the question that was asked.\n"
        "For symptoms or a health problem, use exactly these headings:\n"
        "**Resumen:** 1-2 sentences.\n"
        "**Posibles causas:** possible causes.\n"
        "**Qué puedes hacer:** practical steps.\n"
        "**Medicación:** a table | Medicamento | Para qué sirve | Precaución | with 1-3 "
        "over-the-counter medicines that help with this problem, then one line "
        "'Evitar: ...' (or 'Evitar: nada en particular.'). No doses and no prescription-only "
        "medicines. If no medicine helps, write 'No hace falta medicación.'\n"
        "**Señales de alarma:** when to see a doctor or go to the emergency room.\n"
        "About 200 words plus the table, and nothing after Señales de alarma.\n"
        "For what a medicine is or what it is for, or where your information comes from: "
        "2-4 sentences, without headings or table.\n"
        "For a symptom with no details at all: one sentence, then 2-4 numbered questions to "
        "learn more, and one line saying to seek urgent care if it is severe.\n"
        "If it may be an emergency, say first to call 112.\n"
        "You may get extra instructions with details about the person or official facts "
        "about a medicine: follow them, but never mention them or copy them into the "
        "answer.\n"
        "Healthy adults may take paracetamol and ibuprofeno together, each within its own "
        "maximum dose. You answer from general medical knowledge learned in training: you "
        "cannot look up or cite documents, so never invent sources. If you are not sure, say "
        "so. You do not replace a healthcare professional."
    ),
}
# Used instead of SYSTEM_PROMPT when the conversation includes an image: the fixed
# four headings stopped the model from doing image tasks such as locating anatomy
# or listing the values of a lab report.
IMAGE_SYSTEM_PROMPT = {
    "role": "system",
    "content": (
        "You are an informative medical assistant that can read medical images. "
        "Always respond in Spanish from Spain (castellano), using 'tú'. Start directly "
        "with the answer: no greeting or preamble ('Hola', 'Claro', 'De acuerdo').\n"
        "Write plain text only: never JSON, code blocks, links or coordinates, unless the "
        "person asks for coordinates.\n"
        "If the person asks for a specific task or format (for example coordinates, "
        "a list of values, a comparison), do exactly that task in that format first.\n"
        "Otherwise, say what kind of image it is, describe the main findings and what "
        "they could mean, and suggest next steps. Be clear and concise: about 200 words "
        "maximum.\n"
        "Only describe what is actually visible; if the image is unclear or you are not "
        "sure, say so. Do not make up objects or findings.\n"
        "When comparing a value with a reference range, check each one carefully: "
        "'< X' means the value should be below X and '> X' means it should be above X.\n"
        "If any finding could be urgent, say clearly at the start that the person "
        "should go to the emergency department now.\n"
        "You do not replace a healthcare professional."
    ),
}

START_THINK = "<unused94>"
END_THINK = "<unused95>"


class ThinkFilter:
    """Removes <unused94>...<unused95> blocks, also when streaming."""

    def __init__(self):
        self.buf = ""
        self.thinking = False

    @staticmethod
    def _partial(text: str, marker: str) -> int:
        for n in range(min(len(text), len(marker) - 1), 0, -1):
            if marker.startswith(text[-n:]):
                return n
        return 0

    def feed(self, text: str) -> str:
        self.buf += text
        out = ""
        while True:
            if not self.thinking:
                i = self.buf.find(START_THINK)
                if i != -1:
                    out += self.buf[:i]
                    self.buf = self.buf[i + len(START_THINK):]
                    self.thinking = True
                    continue
                keep = self._partial(self.buf, START_THINK)
                cut = len(self.buf) - keep
                out += self.buf[:cut]
                self.buf = self.buf[cut:]
                return out
            else:
                j = self.buf.find(END_THINK)
                if j != -1:
                    self.buf = self.buf[j + len(END_THINK):]
                    self.thinking = False
                    continue
                keep = self._partial(self.buf, END_THINK)
                self.buf = self.buf[len(self.buf) - keep:]
                return out

    def flush(self) -> str:
        rest = "" if self.thinking else self.buf
        self.buf = ""
        return rest


# Special tokens the model sometimes writes as text (seen with images)
SPECIAL_TOKENS = ("<start_of_image>", "<end_of_image>", "<start_of_turn>", "<end_of_turn>")


def strip_tokens(text: str) -> str:
    for token in SPECIAL_TOKENS:
        text = text.replace(token, "")
    return text


def clean(text: str) -> str:
    f = ThinkFilter()
    return strip_tokens(f.feed(text) + f.flush()).strip()


# No automatic API docs (/docs, /redoc): the app is reachable from the internet
RENEW_EVERY = 600  # seconds between checks for test subscriptions that must be charged again


@asynccontextmanager
async def lifespan(app):
    async def renew_loop():
        while True:
            await asyncio.to_thread(billing.renew_due)
            await asyncio.sleep(RENEW_EVERY)
    task = asyncio.create_task(renew_loop())
    yield
    task.cancel()


app = FastAPI(title="MedGemma local API", docs_url=None, redoc_url=None, openapi_url=None,
              lifespan=lifespan)
client = AsyncClient()  # http://localhost:11434
# Conversations in memory: {session_id: {"user_id": ..., "history": [...]}}.
# user_id is the account id, or "guest:<id>" for a visitor without an account.
sessions: dict[str, dict] = {}
init_db()


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None


class Credentials(BaseModel):
    email: str
    password: str


class CheckoutRequest(BaseModel):
    plan_id: str


class LabSummaryRequest(BaseModel):
    session_id: str


MAX_SESSIONS = 2000      # conversations kept in memory; the oldest are dropped
MAX_HISTORY = 60         # messages kept per conversation (the model only sees the last 8)


def get_history(session_id: Optional[str], user_id):
    """Returns the user's conversation; someone else's session id is treated as unknown."""
    sid = session_id or str(uuid.uuid4())
    session = sessions.get(sid)
    if session is None or session["user_id"] != user_id:
        if session is not None:
            sid = str(uuid.uuid4())
        while len(sessions) >= MAX_SESSIONS:
            sessions.pop(next(iter(sessions)))
        session = sessions[sid] = {"user_id": user_id, "history": []}
    if len(session["history"]) > MAX_HISTORY:
        del session["history"][:-MAX_HISTORY // 2]
    return sid, session["history"]


def usage_headers(user_id: int) -> dict:
    """Remaining free questions for today, so the UI can update its counter."""
    status = billing.status(auth.user_by_id(user_id))
    if status["remaining"] is None:
        return {"X-Usage-Text": "unlimited", "X-Usage-Images": "unlimited"}
    return {
        "X-Usage-Text": str(status["remaining"]["text"]),
        "X-Usage-Images": str(status["remaining"]["image"]),
    }


def check_quota(user, kind: str, message: str) -> tuple[bool, bool]:
    """Returns (emergency, counted). Counts the question, or raises 402 if none are left.

    An emergency with no questions left is not counted and must get the fixed
    emergency message (emergency_response) instead: it never hits the paywall.
    """
    emergency = detect_emergency(message)
    if emergency and not billing.has_quota(user, kind):
        return True, False
    billing.consume(user, kind)
    return emergency, True


def emergency_response(sid: str, history: list[dict], user_id: int) -> StreamingResponse:
    history.append({"role": "assistant", "content": EMERGENCY_MESSAGE})
    return StreamingResponse(
        iter([EMERGENCY_MESSAGE]),
        media_type="text/plain; charset=utf-8",
        headers={"X-Session-Id": sid, "X-Emergency": "1", **usage_headers(user_id)},
    )


def prepare_image(raw: bytes) -> bytes:
    """Converts any image to RGB JPEG, max 1024 px."""
    try:
        img = Image.open(io.BytesIO(raw))
        img = ImageOps.exif_transpose(img)
        img = img.convert("RGB")
        img.thumbnail((1024, 1024))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        return buf.getvalue()
    except Exception:
        raise HTTPException(
            status_code=400, detail="No se ha podido leer la imagen. Usa PNG o JPG."
        )


def build_messages(history: list[dict]) -> list[dict]:
    """Sends only the last MAX_MESSAGES messages and only the latest image."""
    recent = history[-MAX_MESSAGES:]
    # Make sure the context always starts with a user message
    while recent and recent[0]["role"] != "user":
        recent = recent[1:]
    history = recent
    last_img = max(
        (i for i, m in enumerate(history) if m.get("images")), default=-1
    )
    msgs = []
    for i, m in enumerate(history):
        m = dict(m)
        notes = m.pop("notes", None)
        if m.get("images") and i != last_img:
            m.pop("images")
        # Context, medicine rules and CIMA facts for the latest question go as a separate
        # instruction (see questions.py): inside the question the model repeated them
        if notes and i == len(history) - 1:
            msgs.append({"role": "system", "content": notes})
        msgs.append(m)
    system = IMAGE_SYSTEM_PROMPT if last_img != -1 else SYSTEM_PROMPT
    return [system] + msgs


async def ask(history: list[dict], on_fail=None) -> str:
    try:
        resp = await client.chat(
            model=MODEL, messages=build_messages(history), options=OPTIONS
        )
    except Exception as e:
        history.pop()
        if on_fail:
            on_fail()
        raise HTTPException(status_code=502, detail=f"Error de Ollama: {e}")
    text = clean(resp.message.content)
    if not text:
        history.pop()
        if on_fail:
            on_fail()
        return EMPTY_ANSWER
    history.append({"role": "assistant", "content": text})
    return text


def stream_response(
    sid: str, history: list[dict], on_fail=None, headers: Optional[dict] = None
) -> StreamingResponse:
    """Streams the answer. on_fail runs if the model produced no visible text."""
    async def generator():
        full = ""
        f = ThinkFilter()
        try:
            async for chunk in await client.chat(
                model=MODEL,
                messages=build_messages(history),
                options=OPTIONS,
                stream=True,
            ):
                visible = strip_tokens(f.feed(chunk.message.content))
                if not full:
                    visible = visible.lstrip()
                if visible:
                    full += visible
                    yield visible
            tail = f.flush()
            if tail:
                full += tail
                yield tail
            if not full.strip():
                # Not stored or counted (see finally), but the person gets a clear message
                yield EMPTY_ANSWER
        except Exception as e:
            print(f"[ERROR Ollama] {e!r}")  # shown in the uvicorn terminal
            yield f"\n[Error de Ollama: {e}]"
        finally:
            if full:
                history.append({"role": "assistant", "content": full})
            else:
                history.pop()
                if on_fail:
                    on_fail()

    return StreamingResponse(
        generator(),
        media_type="text/plain; charset=utf-8",
        headers={"X-Session-Id": sid, **(headers or {})},
    )


@app.get("/health")
async def health():
    return {"status": "ok", "model": MODEL}


# --- Accounts and plans ---

def login_response(user_id: int, request: Request) -> Response:
    # A visitor who signs up or logs in keeps the conversation they started as a guest
    guest_id = request.cookies.get(GUEST_COOKIE_NAME)
    if guest_id:
        for session in sessions.values():
            if session["user_id"] == f"guest:{guest_id}":
                session["user_id"] = user_id
    response = Response(status_code=204)
    response.set_cookie(
        COOKIE_NAME, auth.create_token(user_id),
        max_age=TOKEN_DAYS * 86400, httponly=True, samesite="lax",
        secure=security.is_https(request),  # through the HTTPS tunnel, never sent over plain HTTP
    )
    return response


@app.post("/auth/register")
async def register(creds: Credentials, request: Request):
    security.check_signup_allowed(request)
    return login_response(auth.create_user(creds.email, creds.password), request)


@app.post("/auth/login")
async def login(creds: Credentials, request: Request):
    key = security.check_login_allowed(request, creds.email)  # 5 failures -> 15 min wait
    try:
        user_id = auth.authenticate(creds.email, creds.password)
    except HTTPException:
        security.limiter.hit(key, security.LOGIN_WINDOW)
        raise
    security.limiter.reset(key)
    return login_response(user_id, request)


@app.post("/auth/logout")
async def logout(request: Request):
    token = request.cookies.get(COOKIE_NAME)
    if token:
        auth.delete_token(token)
    response = Response(status_code=204)
    response.delete_cookie(COOKIE_NAME)
    return response


@app.get("/me")
async def me(user=Depends(auth.current_user)):
    return billing.status(user)


@app.get("/guest")
async def guest_status(request: Request):
    """Free questions left for a visitor without an account."""
    return {"remaining": billing.guest_remaining(request.cookies.get(GUEST_COOKIE_NAME))}


@app.get("/plans")
async def plans():
    public = ("name", "price_eur", "interval", "best_value")
    return {"billing_mode": BILLING_MODE,
            "plans": {pid: {k: p[k] for k in public if k in p} for pid, p in PLANS.items()},
            "free": {"text": FREE_QUESTIONS, "image": FREE_IMAGES}}


# Plain "def": the Stripe calls block, so FastAPI runs these in a worker thread
@app.post("/billing/checkout")
def checkout(req: CheckoutRequest, user=Depends(auth.current_user)):
    """Test mode: subscribes and returns the new status. Stripe: returns {"checkout_url"}."""
    return billing.checkout(user, req.plan_id)


@app.post("/billing/cancel")
def cancel_subscription(user=Depends(auth.current_user)):
    billing.set_cancel(user, True)
    return billing.status(auth.user_by_id(user["id"]))


@app.post("/billing/resume")
def resume_subscription(user=Depends(auth.current_user)):
    billing.set_cancel(user, False)
    return billing.status(auth.user_by_id(user["id"]))


@app.get("/billing/payments")
async def list_payments(user=Depends(auth.current_user)):
    return billing.payments(user["id"])


@app.post("/billing/webhook")
async def stripe_webhook(request: Request):
    """Stripe tells us here about payments, renewals and cancellations."""
    event = stripe_billing.verify(await request.body(), request.headers.get("stripe-signature"))
    await asyncio.to_thread(stripe_billing.handle_event, event)
    return {"received": True}


# --- Chat ---

@app.post("/chat")
async def chat_endpoint(req: ChatRequest, request: Request, user=Depends(auth.current_user)):
    security.check_message(req.message)
    security.check_question_rate(request, f"user:{user['id']}")
    emergency, counted = check_quota(user, "text", req.message)
    sid, history = get_history(req.session_id, user["id"])
    history.append((await asyncio.to_thread(prepare, req.message)).entry)
    if not counted:
        history.append({"role": "assistant", "content": EMERGENCY_MESSAGE})
        return {"session_id": sid, "response": EMERGENCY_MESSAGE, "emergency": True}
    answer = await ask(history, on_fail=lambda: billing.refund(user["id"], "text"))
    return {"session_id": sid, "response": answer, "emergency": emergency}


def fixed_response(sid: str, history: list[dict], message: str, answer: str,
                   headers: Optional[dict] = None) -> StreamingResponse:
    """An answer written in code instead of by the model (it is not counted)."""
    history.append({"role": "user", "content": message})
    history.append({"role": "assistant", "content": answer})
    return StreamingResponse(
        iter([answer]), media_type="text/plain; charset=utf-8",
        headers={"X-Session-Id": sid, **(headers or {})},
    )


SIGNUP_MESSAGE = (
    f"Crea una cuenta gratis para seguir preguntando: {FREE_QUESTIONS} preguntas y "
    f"{FREE_IMAGES} imagen gratis, sin tarjeta."
)


def guest_chat(req: ChatRequest, request: Request, entry: dict) -> StreamingResponse:
    """Visitors without an account get GUEST_QUESTIONS answers, then are asked to sign up.
    Emergencies are always answered."""
    guest_id = request.cookies.get(GUEST_COOKIE_NAME) or secrets.token_urlsafe(16)
    owner = f"guest:{guest_id}"
    security.check_question_rate(request, owner)
    emergency = detect_emergency(req.message)
    counted = billing.guest_consume(guest_id)
    if not counted and not emergency:
        raise HTTPException(
            status_code=401, detail=SIGNUP_MESSAGE, headers={"X-Signup-Required": "1"}
        )
    sid, history = get_history(req.session_id, owner)
    history.append(entry)
    if not counted:
        history.append({"role": "assistant", "content": EMERGENCY_MESSAGE})
        response = StreamingResponse(
            iter([EMERGENCY_MESSAGE]), media_type="text/plain; charset=utf-8",
            headers={"X-Session-Id": sid, "X-Emergency": "1"},
        )
    else:
        headers = {"X-Guest": "1"}
        if emergency:
            headers["X-Emergency"] = "1"
        response = stream_response(
            sid, history, on_fail=lambda: billing.guest_refund(guest_id), headers=headers
        )
    response.set_cookie(
        GUEST_COOKIE_NAME, guest_id, max_age=365 * 86400, httponly=True, samesite="lax",
        secure=security.is_https(request),
    )
    return response


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest, request: Request, user=Depends(auth.optional_user)):
    security.check_message(req.message)
    question = split_context(req.message)[1]
    if detect_emergency(question):
        # Possible emergency: the answer is written in code, free and for everyone. The model
        # answered a heart attack with "relájate y toma un vaso de agua", a stroke with
        # "intenta calmarlo" and an overdose with advice about gas.
        owner = user["id"] if user else f"guest:{request.cookies.get(GUEST_COOKIE_NAME, '')}"
        sid, history = get_history(req.session_id, owner)
        headers = {"X-Emergency": "1", **(usage_headers(user["id"]) if user else {})}
        return fixed_response(sid, history, question, EMERGENCY_MESSAGE, headers)
    prep = await asyncio.to_thread(prepare, req.message)
    if prep.fixed_answer and not detect_emergency(req.message):
        # Medicine that does not exist, or "what is X?" for a real one: answered from code,
        # with the official CIMA facts, and not counted
        owner = user["id"] if user else f"guest:{request.cookies.get(GUEST_COOKIE_NAME, '')}"
        sid, history = get_history(req.session_id, owner)
        headers = usage_headers(user["id"]) if user else {}
        return fixed_response(sid, history, prep.entry["content"], prep.fixed_answer, headers)
    if user is None:
        return guest_chat(req, request, prep.entry)
    security.check_question_rate(request, f"user:{user['id']}")
    emergency, counted = check_quota(user, "text", req.message)
    sid, history = get_history(req.session_id, user["id"])
    history.append(prep.entry)
    if not counted:
        return emergency_response(sid, history, user["id"])
    headers = usage_headers(user["id"])
    if emergency:
        headers["X-Emergency"] = "1"
    return stream_response(
        sid, history, on_fail=lambda: billing.refund(user["id"], "text"), headers=headers
    )


@app.post("/chat/image")
async def chat_image(
    request: Request,
    message: str = Form(...),
    image: UploadFile = File(...),
    session_id: Optional[str] = Form(None),
    user=Depends(auth.current_user),
):
    security.check_message(message)
    security.check_question_rate(request, f"user:{user['id']}")
    raw = await image.read(security.MAX_IMAGE_MB * 1024 * 1024 + 1)
    if len(raw) > security.MAX_IMAGE_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"La imagen ocupa más de {security.MAX_IMAGE_MB} MB.")
    img = prepare_image(raw)  # reject bad files before counting them
    emergency, counted = check_quota(user, "image", message)
    sid, history = get_history(session_id or None, user["id"])
    if not counted:
        history.append({"role": "user", "content": message})
        return emergency_response(sid, history, user["id"])
    prep = await asyncio.to_thread(prepare, message)
    history.append({**prep.entry, "images": [img]})
    headers = usage_headers(user["id"])
    if emergency:
        headers["X-Emergency"] = "1"
    return stream_response(
        sid, history, on_fail=lambda: billing.refund(user["id"], "image"), headers=headers
    )


# --- Lab reports (PDF) ---

MAX_LABS_PER_SESSION = 5
# Shown if the model writes nothing: the table with every value is already on screen
LAB_FALLBACK = (
    "**Resumen:** Arriba tienes cada valor de tu analítica con su explicación. No he podido "
    "preparar el resumen: puedes preguntarme por cualquier valor que te preocupe."
)


@app.post("/labs/analyze")
async def labs_analyze(
    request: Request,
    file: UploadFile = File(...),
    context: str = Form(""),
    sex: str = Form(""),
    child: bool = Form(False),
    session_id: Optional[str] = Form(None),
    user=Depends(auth.current_user),
):
    """Reads the PDF and returns every value classified, from code. The PDF is only kept in
    memory while it is read; personal data in it (name, ID, dates) is never parsed."""
    security.check_message(context)
    security.check_question_rate(request, f"user:{user['id']}")
    security.check_lab_uploads(user["id"])
    raw = await file.read(pdf_guard.MAX_PDF_MB * 1024 * 1024 + 1)
    try:
        # Only real, passive PDFs: see pdf_guard.py
        pdf_guard.check_upload(file.filename, file.content_type, raw)
        if not billing.has_quota(user, "lab"):
            billing.consume(user, "lab")  # raises 402 with the message for the plans window
        analysis = await asyncio.to_thread(pdf_guard.analyse_isolated, raw, sex, child)
    except pdf_guard.PdfRejected as e:
        print(f"[pdf_guard] rejected user={user['id']} reason={e.reason} size={len(raw)}")  # never the content
        raise HTTPException(status_code=e.status, detail=e.message)  # not counted
    except labs.LabError as e:
        raise HTTPException(status_code=422, detail=str(e))  # not counted
    billing.consume(user, "lab")
    sid, _ = get_history(session_id or None, user["id"])
    stored = sessions[sid].setdefault("labs", {})
    while len(stored) >= MAX_LABS_PER_SESSION:
        stored.pop(next(iter(stored)))
    lab_id = uuid.uuid4().hex[:12]
    stored[lab_id] = {"analysis": analysis, "context": context, "summarised": False}
    return {"lab_id": lab_id, "session_id": sid, **analysis}


@app.post("/labs/{lab_id}/summary")
async def labs_summary(lab_id: str, req: LabSummaryRequest, user=Depends(auth.current_user)):
    """Streams the model's summary of an analysed report. Numbers that are not in the report
    are removed (labs.NumberGuard). The results join the conversation for follow-up questions."""
    session = sessions.get(req.session_id)
    if session is None or session["user_id"] != user["id"] or lab_id not in session.get("labs", {}):
        raise HTTPException(status_code=404, detail="Analítica no encontrada. Vuelve a subirla.")
    entry = session["labs"][lab_id]
    analysis, history = entry["analysis"], session["history"]
    if not entry["summarised"]:
        history.append({"role": "user", "content": labs.context_message(analysis)})
        entry["summarised"] = True
    headers = {"X-Session-Id": req.session_id}
    if analysis["counts"]["abnormal"] == 0:
        text = labs.all_normal_summary(analysis)
        history.append({"role": "assistant", "content": text})
        return StreamingResponse(iter([text]), media_type="text/plain; charset=utf-8", headers=headers)
    messages = [
        {"role": "system", "content": labs.LAB_SYSTEM_PROMPT},
        {"role": "user", "content": labs.summary_prompt(analysis, entry["context"])},
    ]

    async def generator():
        intro = labs.critical_intro(analysis)
        if intro:
            yield intro
        full = ""
        think, guard = ThinkFilter(), labs.NumberGuard(analysis)
        try:
            async for chunk in await client.chat(model=MODEL, messages=messages, options=OPTIONS, stream=True):
                visible = guard.feed(strip_tokens(think.feed(chunk.message.content)))
                if not full:
                    visible = visible.lstrip()
                if visible:
                    full += visible
                    yield visible
            tail = guard.feed(think.flush()) + guard.flush()
            if tail.strip():
                full += tail
                yield tail
            if guard.removed:
                print(f"[labs] NumberGuard removed {guard.removed} sentence(s)")
        except Exception as e:
            print(f"[ERROR Ollama] {e!r}")
        if not full.strip():
            full = LAB_FALLBACK
            yield LAB_FALLBACK
        note = labs.missing_note(analysis, full)
        if note:
            full += note
            yield note
        history.append({"role": "assistant", "content": intro + full})

    return StreamingResponse(generator(), media_type="text/plain; charset=utf-8", headers=headers)


@app.delete("/sessions/{session_id}")
async def delete_session(session_id: str, user=Depends(auth.current_user)):
    session = sessions.get(session_id)
    if session is None or session["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Conversación no encontrada")
    del sessions[session_id]
    return {"deleted": session_id}


@app.middleware("http")
async def revalidate_static(request: Request, call_next):
    """Makes browsers check for a newer page, script or style on every load, so an
    update never mixes an old cached page with new code. Also: security headers on
    every response, and requests bigger than the route allows are refused (security.max_body_bytes)."""
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > security.max_body_bytes(request.url.path):
        return Response("Petición demasiado grande", status_code=413)
    response = await call_next(request)
    response.headers.update(security.SECURITY_HEADERS)
    if request.url.path == "/" or request.url.path.endswith((".html", ".js", ".css", ".svg")):
        response.headers["Cache-Control"] = "no-cache"
    return response


# Web interface (static/index.html). Mounted last so the API routes above take priority.
app.mount(
    "/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static"
)
