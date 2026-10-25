"""HTML report of the full battery."""
import html
from collections import Counter
from datetime import datetime

KIND_ES = {
    "dose_outside_table": "Dosis fuera de la tabla revisada", "unsafe_unwarned": "Medicamento contraindicado sin aviso",
    "contradiction": "Contradicción en la medicación", "empty_avoid": "«Evitar» vacío", "broken_table": "Tabla rota",
    "no_table": "Sin tabla de medicamentos", "junk": "Texto que no debería verse", "english": "Respuesta en inglés",
    "render_error": "Error al pintar la respuesta", "empty_answer": "Respuesta vacía", "http_error": "Error del servidor",
    "health_refused": "Pregunta de salud rechazada", "emergency_without_112": "Urgencia sin 112",
    "refused_wrongly": "Seguimiento rechazado", "no_reviewed_doses": "Seguimiento sin pauta revisada",
    "doses_for_child_or_pregnancy": "Dosis a niño o embarazada", "not_refused": "Otro tema no rechazado",
    "context_lost": "Se pierde el contexto", "crash": "Error de la web", "xss": "Código inyectado ejecutado",
}
e = html.escape


def pct(a, b):
    return f"{100 * a / b:.0f} %" if b else "—"


def card(title, value, sub, good):
    color = "var(--ok)" if good else "var(--bad)"
    return f'<div class="card"><div class="k">{e(title)}</div><div class="v" style="color:{color}">{e(value)}</div><div class="s">{e(sub)}</div></div>'


def write_report(out: dict, path) -> None:
    parts = []
    cards = []
    if "fast" in out:
        f = out["fast"]
        cards.append(card("Tests rápidos (F/G)", f["summary"].split(" en ")[0], "sin modelo, en cada commit", f["ok"]))
    if "A" in out:
        cases = out["A"]["cases"]
        bad = [c for c in cases if c.get("violations")]
        q = [ok for c in cases for ok in c.get("quality", {}).values()]
        cards.append(card("A · Seguridad (lo que se ve)", pct(len(cases) - len(bad), len(cases)), f"{len(cases) - len(bad)}/{len(cases)} respuestas sin fallos", not bad))
        cards.append(card("A · Calidad del texto", pct(sum(q), len(q)), f"{sum(q)}/{len(q)} comprobaciones", len(q) and sum(q) / len(q) >= 0.95))
    if "B" in out:
        turns = [t for c in out["B"]["conversations"] for t in c["turns"]]
        bad_t = [t for t in turns if t["violations"]]
        cards.append(card("B · Conversaciones", pct(len(turns) - len(bad_t), len(turns)), f"{len(turns) - len(bad_t)}/{len(turns)} mensajes", not bad_t))
    if "C" in out:
        steps = [s for fl in out["C"]["flows"] for s in fl["steps"] if "shot" not in s]
        okc = sum(s["ok"] for s in steps)
        cards.append(card("C · Recorridos en Chrome", pct(okc, len(steps)), f"{okc}/{len(steps)} pasos", okc == len(steps)))
    if "D" in out:
        d = out["D"]
        bad_d = d["crashes"] + d["xss"] + sum(d["violations"].values())
        cards.append(card("D · Salidas raras", f"{d['total']} variantes", f"{d['crashes']} errores · {d['xss']} inyecciones · {sum(d['violations'].values())} avisos", bad_d == 0))
    if "E" in out:
        ch = out["E"]["chosen"]
        last = out["E"]["configs"][str(ch)] if str(ch) in out["E"]["configs"] else out["E"]["configs"][ch]
        w7 = last[-1]
        cards.append(card("E · 7 personas a la vez", f"{w7['first_worst']:.1f} s", f"peor tiempo hasta ver texto (Ollama {ch} a la vez)", w7["errors"] == 0))
    parts.append(f'<div class="cards">{"".join(cards)}</div>')
    for name, tb in out.get("errors", {}).items():
        parts.append(f"<h2 class='bad'>Bloque {e(name)}: terminó con error</h2><pre>{e(tb)}</pre>")

    # A: failures by kind and examples
    if "A" in out:
        cases = out["A"]["cases"]
        kinds = Counter(v[0] for c in cases for v in c.get("violations", []))
        rows = "".join(f"<tr><td>{e(KIND_ES.get(k, k))}</td><td>{n}</td></tr>" for k, n in kinds.most_common())
        parts.append(f"<h2>A · Seguridad sobre {len(cases)} respuestas reales</h2>"
                     f"<p class='muted'>Cada respuesta del modelo real, pintada con la web real y comprobada. {out['A']['seconds'] / 60:.0f} min.</p>"
                     + (f"<table><tr><th>Fallo</th><th>Veces</th></tr>{rows}</table>" if rows else "<p class='ok'>Ningún fallo de seguridad.</p>"))
        qk = Counter(k for c in cases for k, ok in c.get("quality", {}).items() if not ok)
        if qk:
            parts.append("<h3>Calidad: comprobaciones que fallan</h3><table><tr><th>Comprobación</th><th>Veces</th></tr>"
                         + "".join(f"<tr><td>{e(k)}</td><td>{n}</td></tr>" for k, n in qk.most_common()) + "</table>")
        ex = [c for c in cases if c.get("violations")][:40]
        if ex:
            parts.append("<h3>Ejemplos</h3>" + "".join(
                f"<details><summary><b>{e(c['profile'])}</b> · {e(c['question'][:90])} — "
                f"{e(', '.join(KIND_ES.get(v[0], v[0]) for v in c['violations']))}</summary>"
                f"<ul>{''.join(f'<li><b>{e(KIND_ES.get(v[0], v[0]))}:</b> {e(str(v[1]))}</li>' for v in c['violations'])}</ul>"
                f"<pre>{e(c.get('shown', '')[:1500])}</pre></details>" for c in ex))
    if "B" in out:
        parts.append("<h2>B · Conversaciones de varios mensajes</h2>")
        for conv in out["B"]["conversations"]:
            bad = any(t["violations"] for t in conv["turns"])
            turns = "".join(
                f"<li class='{'bad' if t['violations'] else 'ok'}'><b>{e(t['message'])}</b> <span class='muted'>({e(t['expect'])}, {t['seconds']:.0f} s)</span>"
                + (f"<br>{e('; '.join(KIND_ES.get(v[0], v[0]) + ': ' + str(v[1])[:140] for v in t['violations']))}" if t["violations"] else "")
                + f"<pre>{e(t.get('shown', t['text'])[:600])}</pre></li>" for t in conv["turns"])
            parts.append(f"<details {'open' if bad else ''}><summary>{'✘' if bad else '✔'} {e(conv['name'])} · {e(conv['profile'])}</summary><ol>{turns}</ol></details>")
    if "C" in out:
        parts.append("<h2>C · La app en Chrome, como una persona</h2>")
        for fl in out["C"]["flows"]:
            items = "".join(
                (f"<li><img src='battery_shots/{e(s['shot'])}' alt=''></li>" if "shot" in s else
                 f"<li class='{'ok' if s['ok'] else 'bad'}'>{'✔' if s['ok'] else '✘'} {e(s['what'])}"
                 + (f" <span class='muted'>{e(s['detail'])}</span>" if not s["ok"] and s["detail"] else "") + "</li>")
                for s in fl["steps"])
            parts.append(f"<h3>{e(fl['name'])}</h3><ul class='steps'>{items}</ul>")
    if "D" in out:
        d = out["D"]
        parts.append(f"<h2>D · {d['total']} salidas raras del modelo</h2><p>Tablas rotas, secciones que faltan, cada momento del "
                     f"streaming, HTML inyectado y textos enormes. Errores: <b>{d['crashes']}</b> · código inyectado ejecutado: "
                     f"<b>{d['xss']}</b> · avisos: <b>{e(str(d['violations']))}</b></p>"
                     + "".join(f"<p class='muted'>{e(x['kind'])} · {e(x['what'])} · {e(str(x['detail'])[:160])}</p>" for x in d["examples"][:15]))
    if "E" in out:
        parts.append("<h2>E · Varias personas a la vez</h2>")
        for n, waves in out["E"]["configs"].items():
            mx = max(w["total_worst"] for w in waves) or 1
            rows = "".join(
                f"<tr><td>{w['people']}</td><td>{w['first_median']:.1f} s</td><td>{w['first_worst']:.1f} s</td>"
                f"<td>{w['total_median']:.1f} s</td><td>{w['total_worst']:.1f} s</td><td>{w['errors']}</td>"
                f"<td>{(w['memory_mb'] or 0):.0f} MB</td><td><div class='bar'><i style='width:{100 * w['first_worst'] / mx:.0f}%'></i>"
                f"<b style='width:{100 * (w['total_worst'] - w['first_worst']) / mx:.0f}%'></b></div></td></tr>" for w in waves)
            parts.append(f"<h3>Ollama con {n} a la vez{' (elegido)' if str(n) == str(out['E']['chosen']) else ''}</h3>"
                         "<table><tr><th>Personas</th><th>Primeras palabras (mediana)</th><th>Primeras palabras (peor)</th>"
                         "<th>Completa (mediana)</th><th>Completa (peor)</th><th>Errores</th><th>Memoria</th><th></th></tr>" + rows + "</table>")
        qr = out["E"]["quota_race"]
        parts.append(f"<p>Carrera de cuota (dos preguntas a la vez con una gratis): {'✔ bien' if qr['ok'] else '✘ mal'} · códigos {qr['codes']}</p>")
    if "fast" in out:
        parts.append("<h2>F/G · Tests rápidos</h2><pre>" + e("\n".join(out["fast"]["lines"])) + "</pre>")

    when = datetime.fromtimestamp(out["started"]).strftime("%d/%m/%Y %H:%M")
    minutes = f" · {out['seconds'] / 60:.0f} min" if "seconds" in out else " · en curso"
    path.write_text(f"""<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Batería de tests</title><style>
:root {{ --bg:#f6f8fb; --card:#fff; --text:#0f172a; --muted:#64748b; --line:#e2e8f0; --ok:#16a34a; --bad:#dc2626; --teal:#0d9488; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#0b1120; --card:#111a2e; --text:#e2e8f0; --muted:#94a3b8; --line:#22304d; --ok:#4ade80; --bad:#f87171; --teal:#2dd4bf; }} }}
body {{ background:var(--bg); color:var(--text); font:15px/1.5 -apple-system, Segoe UI, sans-serif; margin:0; padding:24px 16px; }}
main {{ max-width:1100px; margin:auto; }} h1 {{ margin:0; }} h2 {{ margin-top:34px; }} .muted {{ color:var(--muted); }}
.cards {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(200px, 1fr)); gap:12px; margin:20px 0; }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:14px; padding:14px; }}
.card .k {{ font-size:13px; color:var(--muted); }} .card .v {{ font-size:26px; font-weight:700; }} .card .s {{ font-size:12.5px; color:var(--muted); }}
table {{ border-collapse:collapse; width:100%; background:var(--card); border-radius:12px; overflow:hidden; font-size:14px; }}
td, th {{ border-bottom:1px solid var(--line); padding:7px 10px; text-align:left; }}
details {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:8px 12px; margin:8px 0; }}
pre {{ white-space:pre-wrap; font-size:12.5px; background:var(--bg); padding:8px; border-radius:8px; max-height:260px; overflow:auto; }}
.ok {{ color:var(--ok); }} .bad {{ color:var(--bad); }} li {{ margin:6px 0; }} li pre {{ color:var(--text); }}
.steps {{ list-style:none; padding:0; }} .steps img {{ max-width:360px; border-radius:10px; border:1px solid var(--line); }}
.bar {{ display:flex; width:220px; height:10px; background:var(--line); border-radius:6px; overflow:hidden; }}
.bar i {{ background:var(--teal); }} .bar b {{ background:color-mix(in srgb, var(--teal) 40%, transparent); }}
</style></head><body><main><h1>Batería de tests completa</h1><p class="muted">{when}{minutes} · MedGemma local</p>
{''.join(parts)}</main></body></html>""", encoding="utf-8")
