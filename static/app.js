// MedGemma local - chat frontend (no dependencies). Talks to the FastAPI endpoints
// in main.py: /health, /chat/stream, /chat/image and DELETE /sessions/{id}.

const $ = (id) => document.getElementById(id);
const els = {
  app: $("app"), chat: $("chat"), welcome: $("welcome"), messages: $("messages"),
  form: $("composer"), input: $("input"), send: $("send"), attach: $("attach"), file: $("file"),
  attachment: $("attachment"), attachmentImg: $("attachment-img"), attachmentName: $("attachment-name"),
  attachmentRemove: $("attachment-remove"), attachmentPdf: $("attachment-pdf"), attachmentHint: $("attachment-hint"), status: $("status"), statusText: $("status-text"),
  newChat: $("new-chat"), drop: $("drop-overlay"),
  usage: $("usage"), upgrade: $("upgrade"), logout: $("logout"),
  auth: $("auth"), authForm: $("auth-form"), authTitle: $("auth-title"), authSub: $("auth-sub"),
  authEmail: $("auth-email"), authPassword: $("auth-password"), authError: $("auth-error"),
  authSubmit: $("auth-submit"),
  plans: $("plans"), plansClose: $("plans-close"), plansReason: $("plans-reason"),
  plansTest: $("plans-test"), planGrid: $("plan-grid"), plansSub: $("plans-sub"),
  loginBtn: $("login-btn"), signupBtn: $("signup-btn"), authClose: $("auth-close"),
};

const MAX_IMAGE_MB = 10;      // what the server accepts (PDFs, and images after shrinking)
const MAX_PHOTO_MB = 40;      // a photo before shrinking it in the browser
const PHOTO_MAX_SIDE = 1600;  // px; the server reduces images to 1024 px anyway

// Phone photos weigh 3-12 MB: sent as they are, they take seconds on a home wifi and a hiccup
// meant "Failed to fetch". They are shrunk here first (JPEG, max 1600 px, right way up): a few
// hundred KB, sent in an instant. If the browser can't decode the file, it is sent unchanged.
async function shrinkPhoto(file) {
  try {
    const bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
    const scale = Math.min(1, PHOTO_MAX_SIDE / Math.max(bitmap.width, bitmap.height));
    if (scale === 1 && file.size < 1.5 * 1024 * 1024 && file.type === "image/jpeg") return file;
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(bitmap.width * scale);
    canvas.height = Math.round(bitmap.height * scale);
    canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    bitmap.close?.();
    const blob = await new Promise((done) => canvas.toBlob(done, "image/jpeg", 0.88));
    if (!blob) return file;
    return new File([blob], (file.name || "foto").replace(/\.\w+$/, "") + ".jpg", { type: "image/jpeg" });
  } catch {
    return file;
  }
}

// Network errors ("Failed to fetch", "Load failed"...) in words a person understands
function isNetworkError(err) {
  return err instanceof TypeError && /fetch|load failed|network/i.test(err.message);
}
const NETWORK_MESSAGE = "Se ha perdido la conexión con el servidor. Comprueba la wifi o los datos y vuelve a intentarlo.";
// Free questions and images a new account gets in total; updated from the server (/plans)
const FREE = { text: 4, image: 1, lab: 1, daily: 1 };
fetch("plans").then((r) => r.json()).then((data) => {
  if (!data.free) return;
  Object.assign(FREE, data.free);
  document.querySelectorAll(".free-text").forEach((el) => (el.textContent = FREE.text));
  document.querySelectorAll(".free-image").forEach((el) => (el.textContent = FREE.image));
  document.querySelectorAll(".free-lab").forEach((el) => (el.textContent = FREE.lab));
  document.querySelectorAll(".free-daily").forEach((el) => (el.textContent = FREE.daily));
}).catch(() => {});
const state = {
  sessionId: null, image: null, imageUrl: null, controller: null, me: null, authMode: "login",
  guest: false,   // visitor without an account
  pending: null,  // question typed by a visitor, sent after they sign up
};

// ---------- Markdown (small, safe subset: the model's answers are escaped first) ----------
function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function inline(s) {
  return s
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>");
}

function renderMarkdown(text) {
  const lines = escapeHtml(text).split("\n");
  let html = "", list = null, para = [];
  const flushPara = () => { if (para.length) { html += `<p>${inline(para.join("<br>"))}</p>`; para = []; } };
  const closeList = () => { if (list) { html += `</${list}>`; list = null; } };
  let table = null;
  const flushTable = () => {
    if (!table) return;
    const [head, ...body] = table.filter((r) => !/^\s*\|?\s*:?-{2,}/.test(r));
    table = null;
    if (!head) return; // only a "|---|---|" line (possible while the answer is still arriving)
    const cells = (r) => r.trim().replace(/^\||\|$/g, "").split("|").map((c) => inline(c.trim()));
    const heads = cells(head);
    // Every row gets exactly the header's columns: the model sometimes adds an empty one
    // ("| a | b | c | |"), which ended up as an invisible, 1,800 px tall column on phones
    const fit = (row) => heads.map((_, i) => row[i] ?? "");
    html += `<div class="table-wrap"><table><thead><tr>${heads.map((c) => `<th>${c}</th>`).join("")}</tr></thead>` +
      `<tbody>${body.map((r) => `<tr>${fit(cells(r)).map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
  };
  for (const raw of lines) {
    const line = raw.trimEnd();
    let m;
    if (/^\s*\|.*\|/.test(line)) { flushPara(); closeList(); (table ||= []).push(line); continue; }
    flushTable();
    if (!line.trim()) { flushPara(); closeList(); continue; }
    if ((m = line.match(/^#{1,4}\s+(.*)/))) { flushPara(); closeList(); html += `<h4>${inline(m[1])}</h4>`; continue; }
    if ((m = line.match(/^\s*[*\-•]\s+(.*)/))) {
      flushPara(); if (list !== "ul") { closeList(); html += "<ul>"; list = "ul"; }
      html += `<li>${inline(m[1])}</li>`; continue;
    }
    if ((m = line.match(/^\s*\d+[.)]\s+(.*)/))) {
      flushPara(); if (list !== "ol") { closeList(); html += "<ol>"; list = "ol"; }
      html += `<li>${inline(m[1])}</li>`; continue;
    }
    closeList();
    if (/^\*\*[^*]+:\*\*/.test(line.trim())) flushPara(); // "**Summary:** ..." starts a new paragraph
    para.push(line);
  }
  flushTable(); flushPara(); closeList();
  return html;
}

const PILL_ICON = `<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="2.5" y="8" width="19" height="8" rx="4" transform="rotate(-35 12 12)"/><path d="M9.2 7.9l5.6 8.1"/></svg>`;

// Safety rules applied in code, because the model does not follow them reliably
// (it suggested NSAIDs with warfarin and in pregnancy, and gave doses for a child).
const NSAID_RE = /ibuprofen|naproxen|aspirin|acetylsalicylic|acetilsalic|diclofenac|ketoprofen|dexketoprofen|celecoxib|nsaid|\baine|antiinflamatorio/i;
const ANTICOAG_RE = /warfarin|acenocoumarol|acenocumarol|sintrom|apixab|rivaroxab|dabigatr|edoxab|heparin|anticoag|blood thinner/i;
const DOSE_RE = /\b\d+(?:[.,]\d+)?\s?(?:-\s?\d+(?:[.,]\d+)?\s?)?(?:mg\/kg|mg|ml|mcg|µg|g)\b/i;
const ANTIBIOTIC_RE = /amoxicil|penicil|ampicil|cloxacil|azitromic|claritromic|cefurox|cefalex|ciprofloxac|levofloxac|antibi[oó]tic|antibiotic/i;

// How to take common over-the-counter medicines, written in code and never by the model
// (the model gave wrong doses in testing). Usual adult doses from Spanish leaflets for
// non-prescription presentations; they must be reviewed by a pharmacist before real use.
const DOSING = [
  [/paracetamol|acetaminof|acetaminophen/i, "1 comprimido de 500 mg a 1 g cada 6–8 horas. No superes 3 g al día."],
  [/dexketoprofen/i, "1 comprimido de 25 mg cada 8 horas, con comida. No superes 75 mg al día."],
  [/ibuprofen/i, "1 comprimido de 200 o 400 mg cada 6–8 horas, con comida. No superes 1.200 mg al día."],
  [/naproxen/i, "1 comprimido de 220 mg cada 8–12 horas, con comida. No superes 660 mg al día."],
  [/aspirin|acetilsalic|acetylsalicylic/i, "1 comprimido de 500 mg cada 4–6 horas, con comida. No superes la dosis máxima del prospecto."],
  [/omeprazol/i, "1 cápsula de 20 mg al día, antes del desayuno. Sin receta, máximo 14 días seguidos."],
  [/loratadin/i, "1 comprimido de 10 mg al día."],
  [/cetirizin/i, "1 comprimido de 10 mg al día, mejor por la noche (puede dar sueño)."],
  [/oximetazolin|oxilmetazolin|oxymetazolin/i, "1–2 pulverizaciones en cada fosa nasal cada 12 horas. Máximo 3 días seguidos."],
  [/loperamid/i, "2 cápsulas (4 mg) al principio y 1 (2 mg) tras cada deposición líquida. No superes 6 al día ni 2 días seguidos."],
  [/hidrocortisona|hydrocortison/i, "Una capa fina 1–2 veces al día en la zona. Máximo 7 días seguidos."],
  [/rehidrataci|suero oral/i, "Prepáralo como indica el sobre y bébelo a sorbos frecuentes."],
  [/salin|suero fisiol|agua de mar/i, "Lavados nasales varias veces al día, cuando lo necesites."],
];

function howToTake(name, ctx) {
  const child = ctx && (ctx.who === "child" || (ctx.age !== "" && Number(ctx.age) < 16));
  if (child) return "Según el peso: sigue el prospecto o pregunta al pediatra o farmacéutico.";
  if (ctx && (ctx.pregnant || ctx.breastfeeding)) return "Pregunta antes a tu médico o farmacéutico.";
  const hit = DOSING.find(([re]) => re.test(name));
  return hit ? hit[1] : "Sigue el prospecto o pregunta a tu farmacéutico.";
}

function medicationRisks(ctx) {
  const risks = [[ANTIBIOTIC_RE, "Los antibióticos necesitan receta médica: no te automediques."]];
  if (!ctx) return risks;
  const child = ctx.who === "child" || (ctx.age !== "" && Number(ctx.age) < 16);
  const conditions = `${ctx.meds} ${ctx.allergies}`;
  if (/penicil/i.test(ctx.allergies)) {
    risks.unshift([/amoxicil|penicil|ampicil|cloxacil/i, "Tienes alergia a la penicilina: puede causarte una reacción grave."]);
  }
  if (ANTICOAG_RE.test(conditions)) risks.push([NSAID_RE, "No tomar con anticoagulantes: riesgo de sangrado."]);
  if (ctx.pregnant) risks.push([NSAID_RE, "No recomendado en el embarazo, sobre todo desde la semana 20."]);
  if (/asthma|asma/i.test(conditions)) risks.push([NSAID_RE, "Puede provocar crisis de asma en algunas personas."]);
  if (child) risks.push([/aspirin|acetylsalicylic|acetilsalic/i, "Nunca dar aspirina a niños: riesgo de síndrome de Reye."]);
  return risks;
}

function applyMedicationSafety(card, ctx) {
  const risks = medicationRisks(ctx);
  card.querySelectorAll("th").forEach((th) => { if (DOSE_RE.test(th.textContent)) th.textContent = ""; });
  card.querySelectorAll("tbody tr").forEach((tr) => {
    const name = tr.cells[0]?.textContent || "";
    const hit = risks.find(([re]) => re.test(name));
    if (hit) {
      tr.classList.add("flagged");
      tr.cells[tr.cells.length - 1].textContent = `Evitar. ${hit[1]} Consulta a tu farmacéutico.`;
    }
    [...tr.cells].slice(1).forEach((td) => {
      if (DOSE_RE.test(td.textContent)) td.textContent = "Consulta el prospecto.";
    });
    // "How to take it" column, from the DOSING table above
    const how = tr.insertCell(1);
    how.className = "how-to-take";
    how.textContent = hit ? "—" : howToTake(name, ctx);
  });
  card.querySelectorAll("thead tr").forEach((tr) => {
    const th = document.createElement("th");
    th.textContent = "Cómo se toma";
    tr.insertBefore(th, tr.cells[1] || null);
  });
  // Column names on each cell, so on phones every medicine shows as a card with labels
  card.querySelectorAll("table").forEach((t) => {
    const labels = [...t.querySelectorAll("thead th")].map((th) => th.textContent);
    t.querySelectorAll("tbody tr").forEach((tr) =>
      [...tr.cells].forEach((td, i) => td.setAttribute("data-label", labels[i] || "")));
  });
  const table = card.querySelector(".table-wrap");
  if (table) {
    const note = document.createElement("p");
    note.className = "med-note";
    note.textContent = "Pautas orientativas para adultos con medicamentos sin receta. Comprueba siempre el prospecto de tu envase: la dosis cambia según la presentación.";
    table.after(note);
  }
}

// ---------- The same rules for the text outside the medication table ----------
// The model can recommend in a paragraph what the table crosses out ("toma paracetamol o
// ibuprofeno (hasta 400 mg)" to a pregnant woman). In the text: medicines this person must not
// take are marked with a warning below, and doses become "según el prospecto" (the only doses
// shown are the reviewed ones of the table). Answers written in code (opts.trusted) keep their doses.
const PROSE_DOSE_RE = new RegExp(
  String.raw`\s*\([^()]*?\b\d+(?:[.,]\d+)?\s?(?:-\s?\d+(?:[.,]\d+)?\s?)?(?:mg\/kg|mg|ml|mcg|µg|gramos?|g)(?![\/\w])[^()]*\)` +
  String.raw`|\b(?:hasta |un máximo de |máximo )?\d+(?:[.,]\d+)?\s?mg\s?\/\s?\d+(?:[.,]\d+)?\s?ml\b` + // syrups: 120 mg/5 ml
  String.raw`|\b(?:hasta |un máximo de |máximo )?\d+(?:[.,]\d+)?\s?(?:-\s?\d+(?:[.,]\d+)?\s?)?(?:mg\/kg|mg|ml|mcg|µg|gramos?|g)(?![\/\w])`,
  "gi");
// Words before a medicine that already make the sentence a warning ("no le des aspirina")
const NEGATION_RE = /\b(no|nunca|evita\w*|evites|desaconsej\w*|contraindic\w*|ni|tampoco|prohibid\w*|alergi\w*|al[ée]rgic\w*)\b/i;

function proseRisks(ctx) {
  // Only the rules that depend on the person: the general "antibiotics need a prescription" rule
  // would also mark "tu médico puede recetarte antibióticos"
  return medicationRisks(ctx).filter(([re]) => re !== ANTIBIOTIC_RE);
}

function applyProseSafety(el, ctx, opts = {}) {
  const risks = ctx ? proseRisks(ctx) : [];
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, {
    acceptNode: (n) => n.parentElement.closest(".med-card, .prose-warning, .emergency, code")
      ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT,
  });
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  const warnings = new Map(); // block element -> {medicine: reason}
  for (const node of nodes) {
    if (!opts.trusted) {
      node.nodeValue = node.nodeValue.replace(PROSE_DOSE_RE, (m) =>
        /^\s*\(/.test(m) ? " (según el prospecto)" : "la dosis del prospecto");
    }
    if (!risks.length) continue;
    const text = node.nodeValue;
    const found = [];
    for (const [re, reason] of risks) {
      const word = new RegExp(String.raw`[\p{L}]*(?:${re.source})[\p{L}]*`, "giu");
      for (const m of text.matchAll(word)) {
        // the sentence so far ("Evitar: ibuprofeno, naproxeno" counts as a warning for both)
        const before = text.slice(0, m.index).split(/[.!?;]/).pop().slice(-80);
        if (NEGATION_RE.test(before)) continue;
        if (!found.some((f) => f.index === m.index)) found.push({ index: m.index, word: m[0], reason });
      }
    }
    if (!found.length) continue;
    found.sort((a, b) => a.index - b.index);
    const frag = document.createDocumentFragment();
    let last = 0;
    for (const f of found) {
      frag.append(text.slice(last, f.index));
      const mark = document.createElement("span");
      mark.className = "prose-flag";
      mark.title = f.reason;
      mark.textContent = f.word;
      frag.append(mark);
      last = f.index + f.word.length;
      const block = node.parentElement.closest("li")?.closest("ul, ol") || node.parentElement.closest("p, li, ul, ol") || node.parentElement;
      if (!warnings.has(block)) warnings.set(block, new Map());
      warnings.get(block).set(f.word.toLowerCase(), [f.word, f.reason]);
    }
    frag.append(text.slice(last));
    node.replaceWith(frag);
  }
  for (const [block, items] of warnings) {
    const note = document.createElement("p");
    note.className = "prose-warning";
    note.setAttribute("role", "note");
    note.innerHTML = [...items.values()].map(([word, reason]) =>
      `⚠️ <strong>${escapeHtml(word[0].toUpperCase() + word.slice(1))}:</strong> ${escapeHtml(reason)} Consulta a tu médico o farmacéutico.`).join("<br>");
    block.after(note);
  }
}

// Renders an answer and groups the "Medication:" section (title, table, "Avoid:" line) into a card
function renderAnswer(el, text, ctx, opts = {}) {
  text = text
    // the model sometimes appends JSON code blocks (with its own doses); never show them
    .replace(/`{2,3}\s*\w*[\s\S]*?(`{2,3}|$)/g, "")
    .replace(/^\s*[{}\[\]].*$/gm, "")
    .replace(/^\s*"[\w ]+"\s*:.*$/gm, "")
    // the model sometimes echoes the context line, alone or as a "**Contexto:**" block
    .replace(/^.*\[Conte(?:xt|xto):.*$/gm, "")
    .replace(/^\W*Conte(?:xt|xto):.*$/gim, "")
    .replace(/^\W*If the context.*$/gim, "")
    .replace(/^.*\[Datos oficiales de CIMA.*$/gm, "") // the official facts line, if echoed
    .replace(/^\s*[ABC]\)\s*/, "") // the format letter from the system prompt ("A) **Resumen:**")
    .replace(/<(start|end)_of_(image|turn)>/g, "") // special model tokens written as text
    // English notes about the prompt, e.g. "(Note: The question ... does not fit the structure ...)"
    .replace(/^\W*\(?\s*Note\b[^\n]*$/gim, "")
    .replace(/^(\*\*[^*\n]+:\*\*|\p{Lu}\p{Ll}+:)[ \t]*(\|.*\|)[ \t]*$/gmu, "$1\n$2") // "**Medication:** | a | b |"
    .trim();
  el.innerHTML = renderMarkdown(text);
  const start = [...el.children].find((c) => /^(Medicación|Medicacion|Medication):?$/i.test(c.querySelector("strong:first-child")?.textContent.trim() || ""));
  if (start) buildMedCard(el, start, ctx);
  applyProseSafety(el, ctx, opts);
}

// Groups the "Medication:" section (title, table, "Avoid:" line) into a card with the safety rules
function buildMedCard(el, start, ctx) {
  const card = document.createElement("div");
  card.className = "med-card";
  card.innerHTML = `<div class="med-head">${PILL_ICON}<span>Medicación</span><small>Sin receta · pautas orientativas, revisa el prospecto</small></div>`;
  start.before(card);
  let node = start;
  while (node) {
    const next = node.nextElementSibling;
    card.appendChild(node);
    // the next section starts at the next bold heading; a bold "Evitar:" still belongs to the card
    const heading = next?.querySelector("strong:first-child")?.textContent.trim() || "";
    if (!next || (/^\p{Lu}[\p{L} ]+:$/u.test(heading) && !/^(Evitar|Avoid):$/i.test(heading))) break;
    node = next;
  }
  applyMedicationSafety(card, ctx);
  const title = start.querySelector("strong:first-child");
  if (title) title.remove();
  if (!start.textContent.trim()) start.remove();
  card.querySelectorAll("p").forEach((p) => {
    if (/^\s*(Evitar|Avoid):/i.test(p.textContent)) {
      p.classList.add("med-avoid");
      p.innerHTML = p.innerHTML.replace(/^\s*(<strong>)?(Evitar|Avoid):(<\/strong>)?/i, "<strong>Evitar:</strong>");
    }
  });
}

// Never lose an answer because of a formatting problem: if rendering fails, show it as plain
// text, without doses (the safety layer above could not run)
function showAnswer(el, text, ctx, opts = {}) {
  try {
    renderAnswer(el, text, ctx, opts);
  } catch (err) {
    console.error("renderAnswer failed", err);
    const plain = text
      .replace(/^\[Conte(?:xt|xto):.*$/gm, "")
      .replace(/\b\d+(?:[.,]\d+)?\s?(?:-\s?\d+(?:[.,]\d+)?\s?)?(?:mg\/kg|mg|ml|mcg|µg|g)\b/gi, "[dosis: ver prospecto]")
      .replace(/[*|]/g, " ");
    el.innerHTML = plain.split(/\n{2,}/).map((p) => `<p>${escapeHtml(p).replace(/\n/g, "<br>")}</p>`).join("");
  }
}

// ---------- Messages ----------
function hideWelcome() {
  els.welcome.hidden = true;
  $("demo-video")?.pause();  // hidden: no point playing it
}

function scrollToBottom() { els.chat.scrollTop = els.chat.scrollHeight; }

function addUserMessage(text, imageUrl, context) {
  hideWelcome();
  const row = document.createElement("div");
  row.className = "msg user";
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  if (context) {
    const tag = document.createElement("span");
    tag.className = "ctx-tag";
    tag.textContent = `Para: ${context}`;
    bubble.appendChild(tag);
  }
  if (imageUrl) {
    const img = document.createElement("img");
    img.src = imageUrl; img.alt = "Imagen adjunta";
    bubble.appendChild(img);
  }
  bubble.appendChild(document.createTextNode(text));
  row.appendChild(bubble);
  els.messages.appendChild(row);
  scrollToBottom();
}

function addBotMessage() {
  const row = document.createElement("div");
  row.className = "msg bot";
  row.innerHTML = `
    <img src="logo.svg" alt="" class="avatar">
    <div class="bubble">
      <div class="content"><span class="typing"><span class="dots"><i></i><i></i><i></i></span>Pensando…</span></div>
      <div class="meta" hidden></div>
    </div>`;
  els.messages.appendChild(row);
  scrollToBottom();
  return {
    bubble: row.querySelector(".bubble"),
    content: row.querySelector(".content"),
    meta: row.querySelector(".meta"),
  };
}

function showMeta(msg, text, seconds, note) {
  msg.meta.hidden = false;
  msg.meta.innerHTML = `<span>${seconds.toFixed(1)} s${note ? " · " + note : ""}</span>
    <button type="button" title="Copiar respuesta">
      <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 012-2h10"/></svg>
      <span>Copiar</span>
    </button>`;
  const btn = msg.meta.querySelector("button");
  btn.addEventListener("click", async () => {
    // The text as shown, after the safety rules (not the model's original)
    try { await navigator.clipboard.writeText(msg.content.innerText.trim() || text); btn.querySelector("span").textContent = "Copiado"; }
    catch { btn.querySelector("span").textContent = "No se pudo copiar"; }
    setTimeout(() => (btn.querySelector("span").textContent = "Copiar"), 1500);
  });
}

// ---------- Sending ----------
function setBusy(busy) {
  els.app.classList.toggle("busy", busy);
  labButtons().forEach((btn) => (btn.disabled = busy));
  els.send.title = busy ? "Parar" : "Enviar";
  els.send.setAttribute("aria-label", els.send.title);
  updateSendButton();
}

function updateSendButton() {
  const busy = els.app.classList.contains("busy");
  els.send.disabled = !busy && !els.input.value.trim() && !state.pdf;
}

async function send(text) {
  text = text.trim();
  if (state.pdf && !state.controller) return sendLab(text);
  if (!text || state.controller) return;
  if (state.guest && state.image) {
    showAuth("register", `Crea una cuenta gratis para analizar imágenes: ${FREE.image} imagen gratis.`);
    return;
  }

  const image = state.image;
  const context = contextText();
  const ctxSnapshot = readContextForm();
  const outgoing = context ? `[Contexto: ${context}]\n${text}` : text;
  addUserMessage(text, image ? state.imageUrl : null, context);
  clearAttachment(false); // keep the object URL alive: the message bubble shows it
  els.input.value = ""; autoResize();

  const msg = addBotMessage();
  state.controller = new AbortController();
  setBusy(true);
  const t0 = performance.now();
  let answer = "", note = "";

  try {
    let resp;
    if (image) {
      const fd = new FormData();
      fd.append("message", outgoing);
      fd.append("session_id", state.sessionId || "");
      const photo = await shrinkPhoto(image);
      if (photo.size > MAX_IMAGE_MB * 1024 * 1024) throw new Error(`La imagen ocupa más de ${MAX_IMAGE_MB} MB.`);
      fd.append("image", photo, photo.name || "image.jpg");
      resp = await fetch("chat/image", { method: "POST", body: fd, signal: state.controller.signal });
    } else {
      resp = await fetch("chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: outgoing, session_id: state.sessionId }),
        signal: state.controller.signal,
      });
    }
    if (!resp.ok) {
      let detail = resp.statusText;
      try { detail = (await resp.json()).detail || detail; } catch { /* not JSON */ }
      const err = new Error(detail);
      err.status = resp.status;
      err.signup = resp.headers.get("X-Signup-Required") === "1";
      throw err;
    }
    const guestAnswer = resp.headers.get("X-Guest") === "1";
    const trusted = resp.headers.get("X-Fixed-Answer") === "1"; // written in code, reviewed doses
    state.sessionId = resp.headers.get("X-Session-Id") || state.sessionId;
    updateUsageFromHeaders(resp.headers);
    if (resp.headers.get("X-Emergency")) showEmergencyBanner(msg);

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      answer += decoder.decode(value, { stream: true });
      showAnswer(msg.content, answer, ctxSnapshot, { trusted });
      scrollToBottom();
    }
    answer += decoder.decode();
    if (!answer.trim()) throw new Error("El modelo no ha devuelto ninguna respuesta.");
    showAnswer(msg.content, answer, ctxSnapshot, { trusted });
    if (image) addImageNotes(msg, answer);
    if (guestAnswer) addSignupCta(msg, `¿Te ha servido? Crea una cuenta gratis para seguir preguntando: ${FREE.text} preguntas, ${FREE.image} imagen y ${FREE.lab} analítica gratis.`);
  } catch (err) {
    if (err.name === "AbortError") {
      note = "detenida";
      if (!answer) msg.content.innerHTML = "<p><em>Respuesta detenida.</em></p>";
    } else if (err.status === 402) {
      msg.content.innerHTML = renderMarkdown(err.message);
      openPlans(err.message);
      refreshMe();
    } else if (err.status === 401 && err.signup) {
      // The visitor already used their free question: explain it in the chat, and send the
      // question again once they sign up (replacing this unanswered pair)
      const row = msg.bubble.closest(".msg");
      state.pending = { text, rows: [row.previousElementSibling, row] };
      msg.content.innerHTML = "<p>Ya has usado tu pregunta gratis. <strong>Crea una cuenta gratis</strong> y te respondo a esta pregunta al momento.</p>";
      addSignupCta(msg, `${FREE.text} preguntas, ${FREE.image} imagen y ${FREE.lab} analítica gratis, sin tarjeta.`);
      showAuth("register", err.message);
    } else if (err.status === 401) {
      msg.content.textContent = "Tu sesión ha caducado. Vuelve a entrar para continuar.";
      enterGuestMode();
      showAuth("login");
    } else {
      msg.bubble.classList.add("error");
      msg.content.textContent = isNetworkError(err) ? NETWORK_MESSAGE : `Algo ha fallado: ${err.message}. Inténtalo de nuevo en un momento.`;
      answer = "";
    }
  } finally {
    if (answer || note) showMeta(msg, answer, (performance.now() - t0) / 1000, note);
    state.controller = null;
    setBusy(false);
    scrollToBottom();
    els.input.focus();
  }
}

function showEmergencyBanner(msg, text = "Esto puede ser una urgencia. Llama al 112 o ve a urgencias ahora.") {
  const banner = document.createElement("div");
  banner.className = "emergency";
  banner.setAttribute("role", "alert");
  banner.textContent = text;
  msg.bubble.prepend(banner);
}

// Image answers: the model can see something serious without saying it is urgent (it
// described a brain bleed on a CT and only suggested "a neurological evaluation"), and it
// can miss findings altogether (a collapsed lung). So the warning comes from code.
const URGENT_IMAGE_RE = /neumot[oó]rax|hemorragia|hematoma|sangrado|fractura|ictus|infarto|derrame|\bmasa\b|tumor|neoplasi|disecci[oó]n|perforaci[oó]n|obstrucci[oó]n|trombo|embolia|aneurisma|edema pulmonar/i;

function addImageNotes(msg, answer) {
  if (URGENT_IMAGE_RE.test(answer)) {
    showEmergencyBanner(msg, "La imagen podría mostrar algo grave. Consúltalo hoy con un médico, o ve a urgencias o llama al 112 si te encuentras mal.");
  }
  const note = document.createElement("p");
  note.className = "image-note";
  note.textContent = "El análisis de imágenes es orientativo y puede no detectar problemas graves. Ante cualquier duda, consulta a un médico.";
  msg.content.after(note);
}

function addSignupCta(msg, text) {
  const cta = document.createElement("div");
  cta.className = "signup-cta";
  cta.innerHTML = `<p><strong>${escapeHtml(text)}</strong></p>
    <div><button type="button" class="btn primary">Crear cuenta gratis</button>
    <button type="button" class="btn">Entrar</button></div>`;
  const [signup, login] = cta.querySelectorAll("button");
  signup.addEventListener("click", () => showAuth("register"));
  login.addEventListener("click", () => showAuth("login"));
  msg.bubble.appendChild(cta);
}

// ---------- Lab reports (PDF) ----------
// The server reads the PDF and classifies every value in code (labs.py); here the report is
// drawn, and then the model's summary is streamed below it.
const fmtNum = (v) => (v == null ? "" : Number(v).toLocaleString("es-ES", { maximumFractionDigits: 3 }));

function labLabel(r) {
  if (r.status === "positive") return "Positivo";
  if (r.status === "normal") return "Normal";
  if (r.status === "unknown") return "Sin rango";
  const word = r.status === "high" ? "alto" : "bajo";
  if (r.critical) return `Muy ${word}`;
  return r.slight ? `Algo ${word}` : word[0].toUpperCase() + word.slice(1);
}

function labRef(r) {
  if (r.ref_source === "lab") return `Referencia del laboratorio: ${r.ref_text}`;
  if (r.ref_source === "default") {
    const range = r.low != null && r.high != null ? `${fmtNum(r.low)}–${fmtNum(r.high)}`
      : r.high != null ? `menos de ${fmtNum(r.high)}` : `más de ${fmtNum(r.low)}`;
    return `Referencia orientativa para adultos: ${range} (el informe no traía rango)`;
  }
  return "";
}

// Where the value falls: the green zone is the reference range
function labBar(r) {
  if (r.value == null || (r.low == null && r.high == null)) return "";
  const v = r.value;
  const lo = r.low ?? 0;
  const hi = r.high ?? Math.max(r.low * 1.6, v * 1.15);
  const span = hi - lo || 1;
  const dmin = Math.max(0, Math.min(lo - span * 0.4, v - span * 0.1));
  const dmax = Math.max(hi + span * 0.4, v + span * 0.1);
  const pos = (x) => Math.max(2, Math.min(98, ((x - dmin) / (dmax - dmin)) * 100));
  const left = r.low == null ? 0 : pos(lo), right = r.high == null ? 0 : 100 - pos(hi);
  return `<div class="lab-bar" aria-hidden="true"><span class="zone" style="left:${left}%;right:${right}%"></span>` +
    `<i style="left:${pos(v)}%"></i></div>`;
}

function labValue(r) {
  const value = r.value == null ? r.text_value : `${r.qualifier || ""}${r.text_value}`;
  return `${escapeHtml(value)}${r.unit ? ` <small>${escapeHtml(r.unit)}</small>` : ""}`;
}

function labItem(r) {
  const cls = r.critical ? "critical" : r.slight ? "slight" : "";
  const meaningLabel = r.status === "positive" ? "Qué puede significar" : `Si está ${r.status === "high" ? "alto" : "bajo"}`;
  return `<div class="lab-item ${cls}">
    <div class="lab-row"><span class="lab-name">${escapeHtml(r.name)}</span>
      <span class="lab-pill ${r.critical ? "critical" : r.status}">${labLabel(r)}</span></div>
    <div class="lab-value">${labValue(r)}</div>
    ${labBar(r)}
    ${labRef(r) ? `<div class="lab-ref">${escapeHtml(labRef(r))}</div>` : ""}
    ${r.what ? `<p><b>Qué mide:</b> ${escapeHtml(r.what)}</p>` : ""}
    ${r.meaning ? `<p><b>${meaningLabel}:</b> ${escapeHtml(r.meaning)}</p>` : ""}
    ${!r.analyte ? "<p>No tengo una explicación preparada para esta prueba: pregúntale a tu médico o a mí en el chat.</p>" : ""}
  </div>`;
}

function renderLabReport(el, data) {
  const abnormal = data.results.filter((r) => ["high", "low", "positive"].includes(r.status));
  abnormal.sort((a, b) => (b.critical - a.critical) || (a.slight - b.slight));
  const groups = [];
  for (const r of data.results) {
    let g = groups.find((x) => x.name === r.group);
    if (!g) groups.push((g = { name: r.group, rows: [] }));
    g.rows.push(r);
  }
  const c = data.counts;
  el.innerHTML = `<div class="lab">
    <div class="lab-head"><h3>Tu analítica</h3>
      <div class="lab-counts"><span class="lab-count">${c.total} valores leídos</span>
        ${c.abnormal ? `<span class="lab-count bad">${c.abnormal} fuera de rango</span>` : ""}
        <span class="lab-count good">${c.normal} ${c.normal === 1 ? "normal" : "normales"}</span></div></div>
    ${data.critical.length ? `<div class="lab-critical" role="alert"><strong>Importante: revisa esto hoy</strong>
      ${escapeHtml(data.critical_message)}<ul>${data.critical.map((t) => `<li>${escapeHtml(t)}</li>`).join("")}</ul></div>` : ""}
    ${abnormal.length ? `<h4>Fuera de rango</h4><div class="lab-items">${abnormal.map(labItem).join("")}</div>`
      : `<p>Todos los valores que he podido leer están dentro de su rango de referencia.</p>`}
    <details class="lab-all"><summary>Ver los ${c.total} resultados</summary>
      ${groups.map((g) => `<div class="lab-group"><h5>${escapeHtml(g.name)}</h5><table class="lab-table"><tbody>
        ${g.rows.map((r) => `<tr><td><span class="dot ${r.critical ? "critical" : r.status}"></span>${escapeHtml(r.name)}</td>
          <td>${labValue(r)}</td><td>${escapeHtml(r.ref_text || (r.ref_source === "default" ? "orientativa" : ""))}</td>
          <td>${labLabel(r)}</td></tr>`).join("")}
      </tbody></table></div>`).join("")}
    </details>
    <div class="lab-summary content"><span class="typing"><span class="dots"><i></i><i></i><i></i></span>Preparando el resumen…</span></div>
    <p class="lab-note">Explicación orientativa leída de tu PDF. Comprueba los valores con el informe original: tu médico la interpreta con tu historia clínica.${data.child ? " En niños los valores normales cambian con la edad: usa los rangos del laboratorio." : ""}</p>
  </div>`;
  return el.querySelector(".lab-summary");
}

async function sendLab(text) {
  if (state.guest) { showAuth("register", "Crea una cuenta gratis para que te explique tu analítica."); return; }
  const file = state.pdf;
  const ctx = readContextForm();
  const context = contextText();
  text = text || "Explícame mi analítica";
  hideWelcome();
  addUserMessage(text, null, context);
  const bubble = els.messages.lastElementChild.querySelector(".bubble");
  const tag = document.createElement("span");
  tag.className = "file-tag";
  tag.textContent = `📄 ${file.name}`;
  bubble.prepend(tag);
  clearAttachment(true);
  els.input.value = ""; autoResize();

  const msg = addBotMessage();
  msg.content.querySelector(".typing").lastChild.textContent = "Leyendo tu analítica…";
  state.controller = new AbortController();
  setBusy(true);
  const t0 = performance.now();
  let summary = "", note = "";
  try {
    const fd = new FormData();
    fd.append("file", file, file.name || "analitica.pdf");
    fd.append("context", context ? `${context}. ${text}` : text);
    fd.append("sex", ctx.sex || "");
    fd.append("child", ctx.who === "child" ? "true" : "false");
    fd.append("session_id", state.sessionId || "");
    const r = await fetch("labs/analyze", { method: "POST", body: fd, signal: state.controller.signal });
    if (!r.ok) {
      let detail = r.statusText;
      try { detail = (await r.json()).detail || detail; } catch { /* not JSON */ }
      const err = new Error(detail);
      err.status = r.status;
      throw err;
    }
    const data = await r.json();
    state.sessionId = data.session_id;
    refreshMe();
    const summaryEl = renderLabReport(msg.content, data);
    scrollToBottom();
    const resp = await fetch(`labs/${data.lab_id}/summary`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: data.session_id }),
      signal: state.controller.signal,
    });
    if (!resp.ok) throw new Error("No se pudo preparar el resumen");
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      summary += decoder.decode(value, { stream: true });
      showAnswer(summaryEl, summary, ctx);
    }
    summary += decoder.decode();
    showAnswer(summaryEl, summary, ctx);
  } catch (err) {
    const summaryEl = msg.content.querySelector(".lab-summary");
    if (err.name === "AbortError") {
      note = "detenida";
      if (summaryEl) summaryEl.innerHTML = "<p><em>Resumen detenido.</em></p>";
      else msg.content.innerHTML = "<p><em>Detenido.</em></p>";
    } else if (err.status === 402) {
      msg.content.innerHTML = renderMarkdown(err.message);
      openPlans(err.message);
    } else if (err.status === 401) {
      msg.content.textContent = "Tu sesión ha caducado. Vuelve a entrar para continuar.";
      enterGuestMode();
      showAuth("login");
    } else if (summaryEl) {
      summaryEl.innerHTML = `<p>${escapeHtml(err.message)}. Arriba tienes cada valor explicado; puedes preguntarme por cualquiera.</p>`;
    } else if ([400, 413, 415, 422, 429].includes(err.status)) {
      // File refused (not a PDF, not a lab report, scanned, unsafe...): the server says why
      msg.content.innerHTML = `<div class="lab-reject" role="alert"><strong>No puedo analizar este archivo</strong>
        <p>${escapeHtml(err.message)}</p></div>`;
    } else {
      msg.bubble.classList.add("error");
      msg.content.textContent = isNetworkError(err) ? NETWORK_MESSAGE : `Algo ha fallado: ${err.message}. Inténtalo de nuevo en un momento.`;
    }
  } finally {
    if (summary || note) showMeta(msg, summary, (performance.now() - t0) / 1000, note);
    state.controller = null;
    setBusy(false);
    els.input.focus();
  }
}

// ---------- Account, usage and plans ----------
function enterGuestMode() {
  state.me = null;
  state.guest = true;
  els.usage.hidden = true; els.upgrade.hidden = true; els.logout.hidden = true;
  els.loginBtn.hidden = false; els.signupBtn.hidden = false;
  renderLabButton();
  // A returning visitor who already used the free question shouldn't be promised another one
  fetch("guest", { cache: "no-store" }).then((r) => r.json()).then(({ remaining }) => {
    if (remaining === 0) {
      $("hero-sub").innerHTML = "Describe un síntoma, pregunta por un medicamento o comparte una imagen médica. " +
        `<strong>Ya has usado tu pregunta gratis:</strong> crea una cuenta gratis para seguir (${FREE.text} preguntas, ${FREE.image} imagen y ${FREE.lab} analítica).`;
    }
  }).catch(() => {});
}

// ---------- "Upload lab report" button ----------
// Green when this person can upload a report now (Premium, or the free one not used yet),
// red when not: a visitor (sign up) or a free account that used it (plans).
function labAccess() {
  if (state.guest || !state.me) return { ok: false, badge: "Premium", title: "Crea una cuenta para que te explique tu analítica" };
  if (state.me.plan === "premium") return { ok: true, badge: "Premium", title: "Sube tu analítica en PDF: analíticas ilimitadas con Premium" };
  const left = state.me.remaining?.lab ?? 0;
  if (left > 0) return { ok: true, badge: `${left} gratis`, free: true, title: "Sube tu analítica en PDF: tienes 1 gratis" };
  return { ok: false, badge: "Premium", title: "Ya has usado tu analítica gratis: hazte Premium para subir más" };
}

const labButtons = () => document.querySelectorAll(".lab-btn");  // header and chat

function renderLabButton() {
  const access = labAccess();
  labButtons().forEach((btn) => {
    btn.classList.toggle("on", access.ok);
    btn.classList.toggle("off", !access.ok);
    btn.title = access.title;
    btn.setAttribute("aria-label", `Subir analítica en PDF. ${access.ok ? "Disponible" : "No disponible"}: ${access.title}`);
    const badge = btn.querySelector(".lab-btn-badge");
    badge.textContent = access.badge;
    badge.classList.toggle("free", !!access.free);
  });
}

labButtons().forEach((btn) => btn.addEventListener("click", onLabButton));

function onLabButton() {
  if (state.controller) return;
  const access = labAccess();
  if (access.ok) return $("lab-file").click();
  // Not available: the button leads to Premium (a visitor creates the account when choosing a plan)
  openPlans(state.guest || !state.me
    ? "Hazte Premium y te explico todas tus analíticas, sin límite. Primero crearás tu cuenta."
    : "Ya has usado tu analítica gratis. Con Premium puedes subir todas las que quieras.");
}
$("lab-file").addEventListener("change", async () => {
  const file = $("lab-file").files[0];
  $("lab-file").value = "";
  if (!file) return;
  await setAttachment(file);
  if (state.pdf) sendLab(els.input.value.trim());  // one tap: pick the PDF and it is explained
});

function renderUsage() {
  const me = state.me;
  if (!me) return;
  state.guest = false;
  els.loginBtn.hidden = true; els.signupBtn.hidden = true; els.logout.hidden = false;
  document.querySelectorAll(".signup-cta").forEach((c) => c.remove());
  const premium = me.plan === "premium";
  els.usage.hidden = false;
  els.upgrade.hidden = premium;
  els.usage.className = "usage" + (premium ? " premium" : me.remaining.text === 0 ? " empty" : "");
  els.usage.innerHTML = premium
    ? "Premium"
    : `<span class="u-full">${me.remaining.text}/${me.limits.text} ${me.text_period === "day" ? "hoy" : "gratis"} · ${me.remaining.image}/${me.limits.image} imagen</span>` +
      `<span class="u-short">${me.remaining.text}/${me.limits.text}</span>`;
  const sub = me.subscription;
  renderLabButton();
  els.usage.title = premium
    ? (sub && !sub.cancel_at_period_end
        ? `Premium ${sub.plan_name.toLowerCase()} · próximo cobro el ${fmtDate(sub.current_period_end)} · ${me.email}`
        : `Premium hasta el ${fmtDate(me.premium_until)} · ${me.email}`)
    : me.text_period === "day"
      ? `Preguntas gratis de hoy (${FREE.daily} al día) · ${me.email}`
      : `Preguntas gratis de bienvenida · después, ${FREE.daily} al día · ${me.email}`;
}

function updateUsageFromHeaders(headers) {
  const text = headers.get("X-Usage-Text"), image = headers.get("X-Usage-Images");
  if (!state.me || text === null || text === "unlimited") return;
  state.me.remaining = { ...state.me.remaining, text: Number(text), image: Number(image) };  // keeps "lab"
  const period = headers.get("X-Usage-Period"), limit = headers.get("X-Usage-Text-Limit");
  if (period) state.me.text_period = period;
  if (limit && state.me.limits) state.me.limits = { ...state.me.limits, text: Number(limit) };
  renderUsage();
}

async function refreshMe() {
  const r = await fetch("me", { cache: "no-store" });
  if (r.status === 401) { enterGuestMode(); return false; }
  state.me = await r.json();
  renderUsage();
  return true;
}

function showAuth(mode = "login", reason = "") {
  setAuthMode(mode);
  if (reason) els.authSub.textContent = reason;
  els.auth.hidden = false;
  els.authEmail.focus();
}

function setAuthMode(mode) {
  state.authMode = mode;
  const login = mode === "login";
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.mode === mode));
  els.authTitle.textContent = login ? "Hola de nuevo" : "Crea tu cuenta";
  els.authSub.textContent = login
    ? "Entra para seguir preguntando tus dudas de salud."
    : `${FREE.text} preguntas, ${FREE.image} imagen y ${FREE.lab} analítica gratis al registrarte, y después ${FREE.daily} pregunta gratis al día. Sin tarjeta.`;
  els.authSubmit.textContent = login ? "Entrar" : "Crear cuenta";
  els.authPassword.autocomplete = login ? "current-password" : "new-password";
  els.authError.hidden = true;
}

async function submitAuth(e) {
  e.preventDefault();
  const email = els.authEmail.value.trim(), password = els.authPassword.value;
  const fail = (text) => { els.authError.textContent = text; els.authError.hidden = false; };
  if (!email) return fail("Escribe tu email.");
  if (password.length < 8) return fail("La contraseña necesita al menos 8 caracteres.");
  els.authSubmit.disabled = true;
  try {
    const r = await fetch(`auth/${state.authMode}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    if (!r.ok) {
      let detail = "Algo ha fallado. Inténtalo de nuevo.";
      try { detail = (await r.json()).detail || detail; } catch { /* not JSON */ }
      return fail(typeof detail === "string" ? detail : "Revisa tu email y tu contraseña.");
    }
    els.authPassword.value = "";
    els.auth.hidden = true;
    await refreshMe();
    if (state.pendingPlan) {
      const plan = state.pendingPlan;
      state.pendingPlan = null;
      await checkout(plan, document.createElement("button"));
      return;
    }
    if (state.pending) {
      const { text, rows } = state.pending;
      state.pending = null;
      rows.forEach((r) => r?.remove());
      send(text);
    }
    els.input.focus();
  } catch {
    fail("No se puede conectar con el servidor. ¿Está en marcha?");
  } finally {
    els.authSubmit.disabled = false;
  }
}

async function logout() {
  await fetch("auth/logout", { method: "POST" }).catch(() => {});
  await newChat();
  enterGuestMode();
}

const PER = { week: "a la semana", month: "al mes", year: "al año" };
const fmtDate = (iso) => new Date(iso).toLocaleDateString("es-ES", { day: "numeric", month: "long", year: "numeric" });
const fmtPrice = (eur) => `${eur.toFixed(2).replace(".", ",")} €`;

async function openPlans(reason) {
  els.plansReason.textContent = reason || "Preguntas e imágenes sin límite.";
  const r = await fetch("plans");
  const data = await r.json();
  const test = data.billing_mode === "test";
  const sub = state.me?.subscription;
  els.plansTest.hidden = !test;
  $("plans-title").textContent = sub ? "Tu suscripción" : "Hazte Premium";
  renderSubscription(sub);
  els.planGrid.innerHTML = "";
  for (const [id, plan] of Object.entries(data.plans)) {
    const current = sub && sub.plan_id === id;
    const next = sub && sub.next_plan_id === id;
    const card = document.createElement("div");
    card.className = "plan" + (current ? " current" : plan.best_value ? " best" : "");
    const tag = current ? "Tu plan" : next ? "Desde la renovación" : plan.best_value ? "Mejor precio" : "";
    // With Stripe the plan is changed by cancelling first; in test mode at the next renewal
    const label = current ? (sub.cancel_at_period_end ? "Reanudar" : "Plan actual")
      : sub ? (test ? "Cambiar en la renovación" : "Disponible al terminar")
      : test ? "Suscribirme (prueba)" : "Suscribirme";
    card.innerHTML = `${tag ? `<span class="tag">${tag}</span>` : ""}
      <strong>${escapeHtml(plan.name)}</strong>
      <span class="price">${fmtPrice(plan.price_eur)}</span>
      <span class="per">${PER[plan.interval] || ""} · se renueva sola</span>
      <button type="button" class="btn ${current || plan.best_value ? "primary" : ""}">${label}</button>`;
    const btn = card.querySelector("button");
    btn.disabled = (current && !sub.cancel_at_period_end) || next || (!!sub && !current && !test);
    btn.addEventListener("click", () =>
      current ? changeSubscription("resume", btn) : checkout(id, btn));
    els.planGrid.appendChild(card);
  }
  els.plans.hidden = false;
}

function renderSubscription(sub) {
  els.plansSub.hidden = !sub;
  if (!sub) return;
  const price = sub.price_eur != null ? ` · ${fmtPrice(sub.price_eur)}` : "";
  let text;
  if (sub.cancel_at_period_end) {
    text = `Tu plan ${escapeHtml(sub.plan_name)} está cancelado. Sigues siendo Premium hasta el ` +
      `<strong>${fmtDate(sub.current_period_end)}</strong> y no se te cobrará más.`;
  } else if (sub.status === "past_due") {
    text = `<span class="warn">No se pudo cobrar tu plan ${escapeHtml(sub.plan_name)}.</span> Revisa tu forma de pago.`;
  } else {
    text = `Plan ${escapeHtml(sub.plan_name)}${price}. Próximo cobro: <strong>${fmtDate(sub.current_period_end)}</strong>.`;
    if (sub.next_plan_name) text += ` Desde entonces pasarás al plan ${escapeHtml(sub.next_plan_name)}.`;
  }
  els.plansSub.innerHTML = `<p>${text}</p>`;
  if (!sub.cancel_at_period_end) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn ghost";
    btn.textContent = "Cancelar suscripción";
    btn.addEventListener("click", () => {
      if (confirm(`¿Cancelar la suscripción? Seguirás siendo Premium hasta el ${fmtDate(sub.current_period_end)} y no se te cobrará más.`)) {
        changeSubscription("cancel", btn);
      }
    });
    els.plansSub.appendChild(btn);
  }
}

async function billingRequest(path, body, btn) {
  btn.disabled = true;
  try {
    const r = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || r.statusText);
    return data;
  } catch (err) {
    els.plansReason.textContent = `No se pudo completar: ${err.message}`;
    return null;
  } finally {
    btn.disabled = false;
  }
}

async function checkout(planId, btn) {
  if (state.guest || !state.me) {
    state.pendingPlan = planId;  // activated right after signing up (submitAuth)
    els.plans.hidden = true;
    showAuth("register", "Crea tu cuenta para activar Premium. Después se activa el plan que has elegido.");
    return;
  }
  const data = await billingRequest("billing/checkout", { plan_id: planId }, btn);
  if (!data) return;
  if (data.checkout_url) { location.href = data.checkout_url; return; }  // Stripe payment page
  state.me = data;
  renderUsage();
  await openPlans("Listo: tu suscripción está activa.");
}

async function changeSubscription(action, btn) {
  const data = await billingRequest(`billing/${action}`, null, btn);
  if (!data) return;
  state.me = data;
  renderUsage();
  await openPlans(action === "cancel" ? "Suscripción cancelada." : "Suscripción reanudada.");
}

// Back from the Stripe payment page: Stripe confirms the payment to the server a few
// seconds later, so the account is checked again until Premium shows up
async function afterStripeCheckout() {
  const result = new URLSearchParams(location.search).get("checkout");
  if (!result) return;
  history.replaceState(null, "", location.pathname);
  if (result !== "ok") return;
  for (let i = 0; i < 10; i++) {
    if (await refreshMe() && state.me.plan === "premium") return openPlans("¡Gracias! Tu suscripción está activa.");
    await new Promise((r) => setTimeout(r, 2000));
  }
}

// ---------- "Who is it for?" context (kept only in this browser) ----------
const CTX_KEY = "medapp_context";
const CTX_FIELDS = ["who", "sex", "age", "weight", "pregnant", "breastfeeding", "allergies", "meds"];
const ctxEl = (f) => $(`ctx-${f}`);

function readContextForm() {
  return Object.fromEntries(CTX_FIELDS.map((f) => {
    const el = ctxEl(f);
    return [f, el.type === "checkbox" ? el.checked : el.value.trim()];
  }));
}

function contextText() {
  const c = readContextForm();
  const parts = [];
  if (c.who === "child") parts.push("mi hijo o hija");
  else if (c.who === "other") parts.push("otra persona");
  if (c.sex) parts.push(c.sex === "female" ? "mujer" : "hombre");
  if (c.age) parts.push(`${c.age} años`);
  if (c.weight) parts.push(`${c.weight} kg`);
  if (c.pregnant) parts.push("embarazada");
  if (c.breastfeeding) parts.push("en lactancia");
  if (c.allergies) parts.push(`alergia a ${c.allergies}`);
  if (c.meds) parts.push(`toma o tiene: ${c.meds}`);
  if (!parts.length) return "";
  if (c.who === "me") parts.unshift("para mí");
  return parts.join(" · ");
}

function renderContextChip() {
  const text = contextText();
  const chip = $("ctx-chip");
  chip.classList.toggle("set", !!text);
  chip.textContent = text ? `Para: ${text}` : "+ ¿Para quién es? Edad, embarazo, alergias…";
}

function saveContext() {
  try { localStorage.setItem(CTX_KEY, JSON.stringify(readContextForm())); } catch { /* storage unavailable */ }
  renderContextChip();
}

function loadContext() {
  try {
    const saved = JSON.parse(localStorage.getItem(CTX_KEY) || "{}");
    for (const f of CTX_FIELDS) {
      if (!(f in saved)) continue;
      const el = ctxEl(f);
      if (el.type === "checkbox") el.checked = !!saved[f]; else el.value = saved[f];
    }
  } catch { /* storage unavailable or corrupt */ }
  renderContextChip();
}

$("ctx-chip").addEventListener("click", () => {
  const panel = $("ctx-panel");
  panel.hidden = !panel.hidden;
  $("ctx-chip").setAttribute("aria-expanded", String(!panel.hidden));
});
CTX_FIELDS.forEach((f) => ctxEl(f).addEventListener(ctxEl(f).type === "checkbox" ? "change" : "input", saveContext));
loadContext();

// ---------- Attachment: an image, or a lab report in PDF ----------
const isPdf = (file) => file.type === "application/pdf" || /\.pdf$/i.test(file.name || "");

// Quick check in the browser (the server checks everything again): a PDF starts with "%PDF-"
async function looksLikePdf(file) {
  try {
    const head = new Uint8Array(await file.slice(0, 5).arrayBuffer());
    return String.fromCharCode(...head) === "%PDF-";
  } catch { return false; }
}

async function setAttachment(file) {
  if (!file) return;
  if (!(file.type.startsWith("image/") || isPdf(file))) {
    alert("Solo puedes adjuntar imágenes (PNG, JPG) o analíticas en PDF.");
    return;
  }
  const pdf = isPdf(file);
  if (pdf && !(await looksLikePdf(file))) {
    alert("Este archivo no es un PDF válido. Descarga la analítica de nuevo en PDF.");
    return;
  }
  const limit = pdf ? MAX_IMAGE_MB : MAX_PHOTO_MB;  // photos are shrunk before sending
  if (file.size > limit * 1024 * 1024) {
    alert(`${pdf ? "El PDF" : "La imagen"} ocupa más de ${limit} MB.`);
    return;
  }
  clearAttachment(true);
  if (pdf) {
    state.pdf = file;
  } else {
    state.image = file;
    state.imageUrl = URL.createObjectURL(file);
    els.attachmentImg.src = state.imageUrl;
  }
  els.attachmentImg.hidden = pdf;
  els.attachmentPdf.hidden = !pdf;
  els.attachmentName.textContent = file.name || "Imagen pegada";
  els.attachmentHint.textContent = pdf ? "Analítica: pulsa enviar y te la explico" : "Se envía con tu próxima pregunta";
  els.attachment.hidden = false;
  updateSendButton();
  els.input.focus();
}

function clearAttachment(revoke) {
  if (revoke && state.imageUrl) URL.revokeObjectURL(state.imageUrl);
  state.image = null; state.imageUrl = null; state.pdf = null;
  els.attachment.hidden = true; els.file.value = "";
  updateSendButton();
}

// ---------- Status ----------
async function checkHealth() {
  try {
    const r = await fetch("health", { cache: "no-store" });
    if (!r.ok) throw new Error(r.statusText);
    els.status.className = "status ok";
    els.statusText.textContent = "En línea";
  } catch {
    els.status.className = "status err";
    els.statusText.textContent = "Sin conexión";
  }
}

// ---------- New chat ----------
async function newChat() {
  if (state.controller) state.controller.abort();
  if (state.sessionId) {
    fetch(`sessions/${encodeURIComponent(state.sessionId)}`, { method: "DELETE" }).catch(() => {});
  }
  state.sessionId = null;
  clearAttachment(true);
  els.messages.innerHTML = "";
  els.welcome.hidden = false;
  const video = $("demo-video");
  if (video && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) video.play().catch(() => {});
  els.input.value = ""; autoResize();
  els.input.focus();
}

// ---------- Wiring ----------
function autoResize() {
  els.input.style.height = "auto";
  els.input.style.height = Math.min(els.input.scrollHeight, 200) + "px";
  updateSendButton();
}

els.form.addEventListener("submit", (e) => {
  e.preventDefault();
  if (state.controller) state.controller.abort();
  else send(els.input.value);
});
els.input.addEventListener("input", autoResize);
els.input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); els.form.requestSubmit(); }
});
els.input.addEventListener("paste", (e) => {
  const item = [...(e.clipboardData?.items || [])].find((i) => i.type.startsWith("image/"));
  if (item) { e.preventDefault(); setAttachment(item.getAsFile()); }
});
els.attach.addEventListener("click", () => {
  // Photos only: with PDFs in the same picker, Android opens the file manager instead of the
  // gallery or the camera. PDFs go through the "Analítica" buttons.
  if (state.guest) showAuth("register", `Crea una cuenta gratis y analizo tu imagen: ${FREE.image} imagen gratis al registrarte.`);
  else els.file.click();
});
els.file.addEventListener("change", () => setAttachment(els.file.files[0]));
els.attachmentRemove.addEventListener("click", () => clearAttachment(true));
els.newChat.addEventListener("click", newChat);
document.querySelectorAll(".chip").forEach((chip) =>
  chip.addEventListener("click", () => send(chip.dataset.prompt)));

let dragDepth = 0;
window.addEventListener("dragenter", (e) => {
  if ([...(e.dataTransfer?.types || [])].includes("Files")) { dragDepth++; els.drop.hidden = false; }
});
window.addEventListener("dragleave", () => { if (--dragDepth <= 0) { dragDepth = 0; els.drop.hidden = true; } });
window.addEventListener("dragover", (e) => e.preventDefault());
window.addEventListener("drop", (e) => {
  e.preventDefault(); dragDepth = 0; els.drop.hidden = true;
  setAttachment(e.dataTransfer?.files?.[0]);
});

document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => setAuthMode(t.dataset.mode)));
els.authForm.addEventListener("submit", submitAuth);
els.logout.addEventListener("click", logout);
els.loginBtn.addEventListener("click", () => showAuth("login"));
els.signupBtn.addEventListener("click", () => showAuth("register"));
els.authClose.addEventListener("click", () => { els.auth.hidden = true; });
els.upgrade.addEventListener("click", () => openPlans());
els.usage.addEventListener("click", () => openPlans());
els.plansClose.addEventListener("click", () => (els.plans.hidden = true));
els.plans.addEventListener("click", (e) => { if (e.target === els.plans) els.plans.hidden = true; });
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") { els.plans.hidden = true; els.auth.hidden = true; }
});

// Home video: muted autoplay; paused for people who prefer less motion; pause button
(() => {
  const video = $("demo-video"), toggle = $("video-toggle");
  if (!video) return;
  const sync = () => {
    video.parentElement.classList.toggle("paused", video.paused);
    toggle.textContent = video.paused ? "Reproducir" : "Pausar";
    toggle.setAttribute("aria-label", video.paused ? "Reproducir el vídeo" : "Pausar el vídeo");
  };
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) video.removeAttribute("autoplay"), video.pause();
  toggle.addEventListener("click", () => (video.paused ? video.play() : video.pause()));
  video.addEventListener("play", sync);
  video.addEventListener("pause", sync);
  sync();
})();

checkHealth();
setInterval(checkHealth, 15000);
updateSendButton();
refreshMe().then((ok) => ok && els.input.focus());
afterStripeCheckout();
