"""Block D: the interface with strange model output (thousands of variants, no model needed).

From real answers (block A, or built-in examples), it generates broken tables, missing
sections, every streaming cut, injected HTML and huge texts, renders each one with the real
static/app.js and checks: no crash, no injected code runs, and the safety checks still hold.
"""
import asyncio
import json
import time
from pathlib import Path

from common import Browser, TestServer, log

SEEDS = [
    "**Resumen:** Dolor de cabeza tensional.\n\n**Posibles causas:** Estrés.\n\n**Qué puedes hacer:** Descansa.\n\n"
    "**Medicación:**\n| Medicamento | Para qué sirve | Precaución |\n|---|---|---|\n| Paracetamol | Dolor | Ok |\n"
    "| Ibuprofeno | Dolor e inflamación | Con comida |\nEvitar: nada en particular.\n\n**Señales de alarma:** Si empeora.",
    "**Resumen:** Alergia.\n\n**Medicación:**\nLoratadina | Antihistamínico | Puede dar algo de sueño | Evitar: Ibuprofeno.\n\n"
    "**Señales de alarma:** Si te cuesta respirar.",
    "**Resumen:** Diarrea.\n\n**Medicación:** No hace falta medicación.\n\n**Señales de alarma:** Sangre en heces.",
    "**Resumen:** Fiebre.\n\n**Qué puedes hacer:** Toma paracetamol (hasta 1 gramo) o ibuprofeno 400 mg cada 8 horas.\n\n"
    "**Medicación:**\n- Paracetamol: para la fiebre\n- Ibuprofeno: para la fiebre\n\n**Señales de alarma:** Más de 3 días.",
]

FUZZ_JS = r"""
const seeds = SEEDS;
const ctxs = [
  {who: 'me', sex: '', age: '', weight: '', pregnant: false, breastfeeding: false, allergies: '', meds: ''},
  {who: 'me', sex: 'female', age: '31', weight: '', pregnant: true, breastfeeding: false, allergies: '', meds: ''},
  {who: 'child', sex: '', age: '4', weight: '16', pregnant: false, breastfeeding: false, allergies: '', meds: ''},
  {who: 'me', sex: '', age: '72', weight: '', pregnant: false, breastfeeding: false, allergies: '', meds: 'Sintrom'},
  {who: 'me', sex: '', age: '', weight: '', pregnant: false, breastfeeding: false, allergies: 'penicilina', meds: 'asma'},
];
const SUGG = [null, [["Paracetamol", "Alivia el dolor"], ["Ibuprofeno", "Alivia el dolor"]], [["Suero oral de rehidratación", "Repone sales"]]];
let seed = 12345;
const rnd = (n) => { seed = (seed * 1103515245 + 12345) % 2147483648; return seed % n; };
const pick = (a) => a[rnd(a.length)];
const mutations = [
  ['sin barra inicial', (t) => t.replace(/^\|\s*/gm, '')],
  ['sin separador', (t) => t.replace(/^\|?\s*-{3}.*$/gm, '')],
  ['sin cabecera', (t) => t.replace(/^\| Medicamento.*$/gm, '')],
  ['filas en una línea', (t) => t.replace(/\|\n\|/g, '| ')],
  ['columna vacía extra', (t) => t.replace(/\|$/gm, '| |')],
  ['Evitar dentro de la fila', (t) => t.replace(/\|\nEvitar:/, '| Evitar:')],
  ['título y tabla en una línea', (t) => t.replace('**Medicación:**\n', '**Medicación:** ')],
  ['Evitar en negrita', (t) => t.replace(/^Evitar:/m, '**Evitar:**')],
  ['Evitar No hace falta', (t) => t.replace(/^Evitar:.*$/m, 'Evitar: No hace falta medicación.')],
  ['sin Señales de alarma', (t) => t.replace(/\*\*Señales de alarma:\*\*.*$/s, '')],
  ['dosis en el texto', (t) => t.replace('**Qué puedes hacer:**', '**Qué puedes hacer:** Toma 400 mg de ibuprofeno cada 8 horas y 1 gramo de paracetamol.')],
  ['dosis en gramos en la tabla', (t) => t.replace('| Ok |', '| No más de 4 gramos al día |')],
  ['líneas borradas al azar', (t) => t.split('\n').filter(() => rnd(5)).join('\n')],
  ['bloque JSON', (t) => t + '\n```json\n{"medicamentos": [{"nombre": "X", "dosis": "500 mg"}]}\n```'],
  ['eco del contexto', (t) => '[Contexto: para mí · embarazada]\n' + t],
  ['caracteres raros', (t) => t.replace(/a/g, () => pick(['a', 'á', '​', '*', '|', '`']))],
  ['texto enorme', (t) => t.repeat(60)],
];
const injections = [
  '<script>window.__xss=1</script>', '<img src=x onerror="window.__xss=1">', '[pincha](javascript:window.__xss=1)',
  '**<b onmouseover="window.__xss=1">negrita</b>**', '| <svg onload="window.__xss=1"> | x | y |', '<iframe src="javascript:window.__xss=1">',
];
const result = {total: 0, crashes: 0, xss: 0, violations: {}, examples: []};
const note = (kind, what, detail) => {
  result.violations[kind] = (result.violations[kind] || 0) + 1;
  if (result.examples.length < 40) result.examples.push({kind, what, detail});
};
function run(text, what) {
  result.total++;
  const ctx = pick(ctxs), suggested = pick(SUGG);
  window.__xss = 0;
  let r;
  try { r = window.__battery.check(text, ctx, {suggested, short_ok: true}); }
  catch (e) { result.crashes++; note('crash', what, String(e).slice(0, 160)); return; }
  if (window.__xss) { result.xss++; note('xss', what, text.slice(0, 120)); }
  for (const [kind, detail] of r.violations) {
    if (kind === 'no_table' || kind === 'empty_answer') continue;  // expected with deleted text
    // "caracteres raros" breaks the words themselves ("Evit​r"): no real answer looks like that
    if (kind === 'unsafe_unwarned' && what.includes('caracteres raros')) continue;
    note(kind, what, detail);
  }
}
for (const s of seeds) {
  for (let cut = 1; cut < s.length; cut += 7) run(s.slice(0, cut), 'streaming cortado');   // every streaming moment
  for (const [name, fn] of mutations) run(fn(s), name);
  for (let i = 0; i < 25; i++) {   // combined mutations
    let t = s; const names = [];
    for (let k = 0; k < 3; k++) { const [n, fn] = pick(mutations); if (n !== 'texto enorme') { t = fn(t); names.push(n); } }
    run(t, names.join(' + '));
  }
  for (const inj of injections) {
    run(s.replace('**Resumen:**', '**Resumen:** ' + inj), 'inyección: ' + inj.slice(0, 30));
    run(s + '\n' + inj, 'inyección al final');
  }
}
await new Promise(r => setTimeout(r, 500));  // onerror handlers run asynchronously
result.xss += window.__xss ? 1 : 0;
return result;
"""


async def fuzz(server: TestServer, seeds: list[str]) -> dict:
    script = (Path(__file__).parent / "page_checks.js").read_text()
    async with Browser() as b:
        await b.goto(server.url)
        await b.js(script + "\nreturn 1;")
        return await b.js(FUZZ_JS.replace("SEEDS", json.dumps(seeds)), timeout=900)


def run_block(server: TestServer, real_answers: list[str] = ()) -> dict:
    seeds = SEEDS + [t for t in real_answers if "Medicaci" in t][:30]
    log(f"D: variantes raras a partir de {len(seeds)} respuestas…")
    t0 = time.time()
    res = asyncio.run(fuzz(server, seeds))
    log(f"D: {res['total']} variantes · {res['crashes']} errores · {res['xss']} inyecciones ejecutadas · "
        f"{sum(res['violations'].values())} avisos de seguridad")
    res["seconds"] = time.time() - t0
    return res
