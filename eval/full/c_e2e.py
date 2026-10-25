"""Block C: the app used like a person would, in Chrome (desktop and phone), real model.

Each flow returns its steps with ok/fail and a screenshot. Flows:
visitor to Premium · daily free question · photo from the phone · lab report PDFs ·
the lab buttons · phone layout · lost connection · emergency.
"""
import asyncio
import base64
import io
import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone

from PIL import Image

from common import RESULTS, ROOT, Browser, TestServer, log

SHOTS = RESULTS / "battery_shots"
WAIT_IDLE = ("for (let i = 0; i < 400 && (state.controller || ![...document.querySelectorAll('.msg.bot .content')].length); i++) "
             "await new Promise(r => setTimeout(r, 300));")


class Flow:
    def __init__(self, name):
        self.name, self.steps = name, []

    def step(self, what, ok, detail=""):
        self.steps.append({"what": what, "ok": bool(ok), "detail": str(detail)[:300]})
        return ok


async def shot(b: Browser, flow: Flow, label: str):
    SHOTS.mkdir(parents=True, exist_ok=True)
    path = SHOTS / f"{flow.name.replace(' ', '_')}_{len(flow.steps)}.jpg"
    await b.screenshot(path, quality=60)
    flow.steps.append({"what": f"captura: {label}", "ok": True, "shot": path.name})


async def ask(b: Browser, text: str):
    await b.js(f"send({json.dumps(text)}); await new Promise(r => setTimeout(r, 800)); {WAIT_IDLE} return 1;")
    return await b.js("const m = [...document.querySelectorAll('.msg.bot')].at(-1); return m ? m.innerText : '';")


async def visitor_to_premium(server: TestServer) -> Flow:
    f = Flow("visitante a Premium")
    async with Browser() as b:
        await b.goto(server.url)
        f.step("portada de visitante con botón de analítica en rojo",
               await b.js("return !els.signupBtn.hidden && $('lab-btn').classList.contains('off');"))
        a = await ask(b, "Me duele la cabeza desde ayer, ¿qué puedo hacer?")
        f.step("primera pregunta gratis respondida", len(a) > 150 and "Crea una cuenta" in a, a[:120])
        a = await ask(b, "¿Y si además tengo fiebre?")
        f.step("segunda pregunta pide registrarse", await b.js("return !els.auth.hidden;"), a[:120])
        await shot(b, f, "pide registrarse")
        await b.js("els.authEmail.value = 'bateria-c1@example.com'; els.authPassword.value = 'battery-pass-123'; "
                   "els.authForm.requestSubmit(); await new Promise(r => setTimeout(r, 1500)); " + WAIT_IDLE + " return 1;")
        me = await b.js("return state.me;")
        f.step("registro por el formulario y la pregunta pendiente se responde",
               me and me["plan"] == "free" and me["remaining"]["text"] == 3, me and me["remaining"])
        for i in range(3):
            await ask(b, f"Pregunta de salud número {i + 1}: ¿es bueno dormir la siesta?")
        me = await b.js("return state.me;")
        f.step("contador a 0 tras 4 preguntas", me["remaining"]["text"] == 0, me["remaining"])
        a = await ask(b, "¿Y beber agua con limón?")
        f.step("la 5.ª pregunta abre los planes", await b.js("return !els.plans.hidden;"), a[:120])
        await shot(b, f, "ventana de planes")
        await b.js("await checkout('weekly', document.createElement('button')); return 1;")
        me = await b.js("await refreshMe(); return state.me;")
        f.step("Premium de prueba activado", me["plan"] == "premium", me["plan"])
        await b.js("els.plans.hidden = true; return 1;")
        a = await ask(b, "¿Y beber agua con limón por la mañana?")
        f.step("con Premium vuelve a responder", len(a) > 100 and "Hazte Premium" not in a[:60]
               and "no he podido responder" not in a, a[:100])
        f.step("botón de analítica en verde con Premium", await b.js("return $('lab-btn').classList.contains('on');"))
    return f


async def daily_question(server: TestServer) -> Flow:
    f = Flow("pregunta diaria")
    email = "bateria-c2@example.com"
    server.make_user(email)
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
    with sqlite3.connect(server.dir / "app.db") as db:
        db.execute("UPDATE users SET free_text_used = 4, free_pack_done_on = ? WHERE email = ?", (yesterday, email))
    async with Browser() as b:
        await b.goto(server.url)
        await b.js(f"await fetch('auth/login', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, "
                   f"body: JSON.stringify({{email: '{email}', password: 'battery-pass-123'}})}}); await refreshMe(); return 1;")
        me = await b.js("return state.me;")
        f.step("al día siguiente: 1 pregunta gratis", me["remaining"]["text"] == 1 and me["text_period"] == "day", me)
        a = await ask(b, "¿Es bueno caminar media hora al día?")
        f.step("la pregunta diaria se responde", len(a) > 100, a[:100])
        await ask(b, "¿Y correr?")
        f.step("la segunda del día abre los planes", await b.js("return !els.plans.hidden && /de hoy/.test(els.plansReason.textContent);"))
    return f


async def phone_photo(server: TestServer) -> Flow:
    f = Flow("foto desde el móvil")
    server.make_user("bateria-c3@example.com", premium=True)
    big = Image.open(ROOT / "eval" / "images" / "06_derm_shingles.jpg").convert("RGB").resize((4000, 3000))
    buf = io.BytesIO()
    big.save(buf, "JPEG", quality=95)
    data = base64.b64encode(buf.getvalue()).decode()
    async with Browser(390, 844, 3, True) as b:
        await b.goto(server.url)
        await b.js("await fetch('auth/login', {method: 'POST', headers: {'Content-Type': 'application/json'}, "
                   "body: JSON.stringify({email: 'bateria-c3@example.com', password: 'battery-pass-123'})}); await refreshMe(); return 1;")
        res = await b.js(f"""
          const bytes = Uint8Array.from(atob({json.dumps(data)}), (c) => c.charCodeAt(0));
          const file = new File([bytes], 'IMG_2026.jpg', {{type: 'image/jpeg'}});
          let sent = 0; const orig = window.fetch;
          window.fetch = (u, o) => {{ if (String(u).includes('chat/image')) sent = o.body.get('image').size; return orig(u, o); }};
          const dt = new DataTransfer(); dt.items.add(file); els.file.files = dt.files; els.file.dispatchEvent(new Event('change'));
          await new Promise(r => setTimeout(r, 300));
          send('¿Qué es esto que me ha salido en la piel?'); await new Promise(r => setTimeout(r, 800)); {WAIT_IDLE}
          window.fetch = orig;
          return {{original: file.size, sent, answer: [...document.querySelectorAll('.msg.bot')].at(-1).innerText}};""")
        f.step(f"foto de {res['original'] // 1024} KB enviada reducida ({res['sent'] // 1024} KB)", 0 < res["sent"] < 1_500_000)
        f.step("la foto se analiza", len(res["answer"]) > 80 and "Failed" not in res["answer"], res["answer"][:120])
        await shot(b, f, "respuesta a la foto")
    return f


async def lab_reports(server: TestServer) -> Flow:
    f = Flow("analíticas en PDF")
    server.make_user("bateria-c4@example.com")
    good = base64.b64encode((ROOT / "eval" / "labs" / "01_ferropenia_columnas.pdf").read_bytes()).decode()
    bad = base64.b64encode((ROOT / "eval" / "labs" / "12_no_es_analitica.pdf").read_bytes()).decode()
    async with Browser() as b:
        await b.goto(server.url)
        await b.js("await fetch('auth/login', {method: 'POST', headers: {'Content-Type': 'application/json'}, "
                   "body: JSON.stringify({email: 'bateria-c4@example.com', password: 'battery-pass-123'})}); await refreshMe(); return 1;")
        f.step("botón de analítica en verde con «1 gratis»",
               await b.js("return $('lab-btn').classList.contains('on') && /1 gratis/.test($('lab-btn').innerText);"))
        load = ("const bytes = Uint8Array.from(atob(DATA), (c) => c.charCodeAt(0)); "
                "const dt = new DataTransfer(); dt.items.add(new File([bytes], 'analitica.pdf', {type: 'application/pdf'})); "
                "$('lab-file').files = dt.files; $('lab-file').dispatchEvent(new Event('change')); "
                "await new Promise(r => setTimeout(r, 1500)); " + WAIT_IDLE)
        await b.js(load.replace("DATA", json.dumps(bad)) + " return 1;")
        txt = await b.js("return [...document.querySelectorAll('.msg.bot')].at(-1).innerText;")
        f.step("una factura se rechaza con explicación y no gasta la gratis",
               "No puedo analizar este archivo" in txt and await b.js("return state.me.remaining.lab === 1;"), txt[:120])
        await b.js(load.replace("DATA", json.dumps(good)) + " return 1;")
        lab = await b.js("const l = [...document.querySelectorAll('.lab')].at(-1); return l ? {counts: l.querySelector('.lab-counts').innerText, "
                         "summary: l.querySelector('.lab-summary').innerText} : null;")
        f.step("la analítica se lee (16 valores, 7 fuera de rango)", lab and "16 valores" in lab["counts"] and "7 fuera" in lab["counts"], lab)
        f.step("con resumen del modelo", lab and len(lab["summary"]) > 150, lab and lab["summary"][:100])
        await shot(b, f, "analítica explicada")
        f.step("el botón pasa a rojo tras usar la gratis", await b.js("await refreshMe(); return $('lab-btn').classList.contains('off');"))
        f.step("en rojo lleva a Hazte Premium", await b.js("$('lab-btn').click(); await new Promise(r => setTimeout(r, 800)); return !els.plans.hidden;"))
        res = await b.js("let msg = ''; const a = window.alert; window.alert = (m) => msg = m; "
                         "await setAttachment(new File(['hola, no soy un pdf'], 'falso.pdf', {type: 'application/pdf'})); "
                         "window.alert = a; return {msg, attached: !!state.pdf};")
        f.step("un archivo que no es PDF se frena antes de subirlo", res["msg"] and not res["attached"], res)
    return f


async def phone_layout(server: TestServer) -> Flow:
    f = Flow("diseño en el móvil")
    server.make_user("bateria-c5@example.com", premium=True)
    lab = base64.b64encode((ROOT / "eval" / "labs" / "05_miles_por_mm3.pdf").read_bytes()).decode()
    async with Browser(375, 812, 2, True) as b:
        await b.goto(server.url)
        f.step("portada sin desbordar", await b.js("return document.documentElement.scrollWidth <= innerWidth;"))
        await shot(b, f, "portada móvil")
        await b.js("await fetch('auth/login', {method: 'POST', headers: {'Content-Type': 'application/json'}, "
                   "body: JSON.stringify({email: 'bateria-c5@example.com', password: 'battery-pass-123'})}); await refreshMe(); return 1;")
        await ask(b, "Me duele la cabeza, ¿qué puedo tomar?")
        f.step("respuesta con medicación sin desbordar",
               await b.js("return document.documentElement.scrollWidth <= innerWidth && !!document.querySelector('.med-card');"))
        await shot(b, f, "medicación en el móvil")
        await b.js(f"const bytes = Uint8Array.from(atob({json.dumps(lab)}), (c) => c.charCodeAt(0)); "
                   "await setAttachment(new File([bytes], 'a.pdf', {type: 'application/pdf'})); sendLab(''); "
                   "await new Promise(r => setTimeout(r, 1500)); " + WAIT_IDLE + " document.querySelector('.lab details').open = true; return 1;")
        narrow = await b.js("return [...document.querySelectorAll('.lab-table td:last-child')].map(td => td.getBoundingClientRect().width).filter(w => w < 30).length;")
        f.step("tabla de resultados legible (sin columnas aplastadas «N-o-r-m-al»)",
               narrow == 0 and await b.js("return document.documentElement.scrollWidth <= innerWidth;"), f"{narrow} celdas estrechas")
        await shot(b, f, "analítica en el móvil")
        f.step("botones de cabecera visibles", await b.js("const r = $('lab-btn').getBoundingClientRect(); return r.width > 20 && r.right <= innerWidth;"))
    return f


async def connection_and_emergency(server: TestServer) -> Flow:
    f = Flow("conexión y urgencias")
    async with Browser() as b:
        await b.goto(server.url)
        a = await ask(b, "Me duele mucho el pecho y se me va al brazo izquierdo")
        f.step("urgencia: aviso del 112 con banner rojo",
               "112" in a and await b.js("return !!document.querySelector('.msg.bot .emergency');"), a[:100])
        await shot(b, f, "urgencia")
        res = await b.js("const orig = window.fetch; window.fetch = (u, o) => String(u).includes('chat/') ? "
                         "Promise.reject(new TypeError('Failed to fetch')) : orig(u, o); await newChat(); send('hola, me duele la tripa'); "
                         "await new Promise(r => setTimeout(r, 800)); window.fetch = orig; "
                         "return [...document.querySelectorAll('.msg.bot')].at(-1).innerText;")
        f.step("sin conexión: mensaje claro", "Se ha perdido la conexión" in res, res[:100])
    return f


FLOWS = [visitor_to_premium, daily_question, phone_photo, lab_reports, phone_layout, connection_and_emergency]


def run_block(server: TestServer) -> dict:
    log(f"C: {len(FLOWS)} recorridos en Chrome…")
    t0 = time.time()
    flows = []
    for fn in FLOWS:
        try:
            flow = asyncio.run(fn(server))
        except Exception as e:  # a crash in a flow is a failure of that flow, not of the battery
            flow = Flow(fn.__name__)
            flow.step("el recorrido terminó con error", False, repr(e)[:300])
        ok = [s for s in flow.steps if "shot" not in s]
        log(f"   C: «{flow.name}» {sum(s['ok'] for s in ok)}/{len(ok)}")
        flows.append(flow.__dict__)
    return {"flows": flows, "seconds": time.time() - t0}
