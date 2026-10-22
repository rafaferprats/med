// Tests for the safety layer of the web interface (renderAnswer in static/app.js):
// flagged medicines, removed doses, "Cómo se toma" column and cleaning of stray text.
// Runs in the browser, without the model: open the app, paste this file in the browser
// console (or run it with a browser automation tool) and read the returned summary.
(() => {
  const BT = "`";
  const base = { who: "me", sex: "", age: "", weight: "", pregnant: false, breastfeeding: false, allergies: "", meds: "" };
  const table = (rows) => [
    "**Resumen:** Prueba.",
    "**Medicación:**",
    "| Medicamento | Para qué sirve | Precaución |",
    "|---|---|---|",
    ...rows.map((r) => `| ${r[0]} | ${r[1]} | ${r[2]} |`),
    "Evitar: nada en particular.",
    "**Señales de alarma:** Fiebre muy alta.",
  ].join("\n");
  const render = (text, ctx) => {
    const el = document.createElement("div");
    renderAnswer(el, text, ctx);
    return el;
  };
  const row = (el, name) => [...el.querySelectorAll("tbody tr")].find((tr) => tr.cells[0].textContent.includes(name));
  const how = (el, name) => row(el, name)?.cells[1].textContent || "";
  const flagged = (el, name) => !!row(el, name)?.classList.contains("flagged");
  const imageNotes = (answer) => {
    const msg = addBotMessage();
    renderAnswer(msg.content, answer, base);
    addImageNotes(msg, answer);
    const out = { banner: !!msg.bubble.querySelector(".emergency"), note: !!msg.bubble.querySelector(".image-note") };
    msg.bubble.closest(".msg").remove();
    return out;
  };

  const meds = [["Paracetamol", "Dolor", "Ok."], ["Ibuprofeno", "Dolor", "Con comida."], ["Naproxeno", "Dolor", "Ok."],
    ["Aspirina", "Dolor", "Ok."], ["Amoxicilina", "Infección", "Ok."], ["Loratadina", "Alergia", "Ok."],
    ["Fluzolanex", "Algo", "Ok."]];
  const cases = [
    ["embarazo: ibuprofeno tachado", () => flagged(render(table(meds), { ...base, pregnant: true }), "Ibuprofeno")],
    ["embarazo: naproxeno tachado", () => flagged(render(table(meds), { ...base, pregnant: true }), "Naproxeno")],
    ["embarazo: sin pauta, preguntar antes", () => how(render(table(meds), { ...base, pregnant: true }), "Paracetamol").startsWith("Pregunta antes")],
    ["Sintrom: ibuprofeno tachado", () => flagged(render(table(meds), { ...base, meds: "Sintrom" }), "Ibuprofeno")],
    ["Sintrom: aspirina tachada", () => flagged(render(table(meds), { ...base, meds: "sintrom" }), "Aspirina")],
    ["asma: ibuprofeno tachado", () => flagged(render(table(meds), { ...base, meds: "asma" }), "Ibuprofeno")],
    ["niño: aspirina tachada", () => flagged(render(table(meds), { ...base, who: "child", age: "2" }), "Aspirina")],
    ["niño: pauta según el peso", () => how(render(table(meds), { ...base, who: "child", age: "2" }), "Paracetamol").startsWith("Según el peso")],
    ["alergia penicilina: amoxicilina tachada con aviso", () => {
      const el = render(table(meds), { ...base, allergies: "penicilina" });
      return flagged(el, "Amoxicilina") && row(el, "Amoxicilina").textContent.includes("alergia a la penicilina");
    }],
    ["sin contexto: antibiótico tachado", () => flagged(render(table(meds), base), "Amoxicilina")],
    ["sin contexto: paracetamol no tachado", () => !flagged(render(table(meds), base), "Paracetamol")],
    ["pauta adulto paracetamol", () => how(render(table(meds), base), "Paracetamol").includes("No superes 3 g al día")],
    ["pauta adulto loratadina", () => how(render(table(meds), base), "Loratadina") === "1 comprimido de 10 mg al día."],
    ["medicamento sin pauta: prospecto", () => how(render(table(meds), base), "Fluzolanex").startsWith("Sigue el prospecto")],
    ["columna Cómo se toma", () => [...render(table(meds), base).querySelectorAll("th")].some((th) => th.textContent === "Cómo se toma")],
    ["dosis del modelo borradas", () => !/\d\s?(mg|ml)/i.test([...render(table([["Ibuprofeno", "Fiebre", "Dar 5-10ml cada 6 horas"]]), base)
      .querySelectorAll("tbody td")].slice(2).map((td) => td.textContent).join(" "))],
    ["título y tabla en la misma línea", () => render("**Medicación:** | Medicamento | Para qué sirve | Precaución |\n|---|---|---|\n| Paracetamol | Dolor | Ok. |", base)
      .querySelectorAll("thead th").length === 4],
    ["bloques JSON eliminados", () => {
      const t = table(meds) + "\n" + BT + BT + " json\n{\n\"medicamentos\": [\n{\"nombre\": \"X\", \"dosis\": 1000}\n]\n}\n" + BT;
      return !/json|medicamentos"|\{|\}/.test(render(t, base).innerText);
    }],
    ["líneas de contexto eliminadas", () => !/Contexto/.test(render(table(meds) + "\n**Contexto:** [Contexto: adulto]\n(Contexto: [x])", base).innerText)],
    ["datos de CIMA eliminados", () => !/CIMA/.test(render(table(meds) + "\n[Datos oficiales de CIMA (AEMPS) sobre «x»: y]", base).innerText)],
    ["notas en inglés eliminadas", () => !/Note/.test(render(table(meds) + "\n(Note: The question does not fit the structure.)", base).innerText)],
    ["letra de formato eliminada", () => render("A) " + table(meds), base).innerText.startsWith("Resumen")],
    ["tarjeta de medicación creada", () => !!render(table(meds), base).querySelector(".med-card")],
    ["columna vacía extra del modelo: filas con las columnas de la cabecera", () => {
      const el = render("**Medicación:** | Medicamento | Para qué sirve | Precaución |\n|---|---|---|---|\n| Amoxicilina | Infección | Receta | |", base);
      const tr = el.querySelector("tbody tr");
      return tr.cells.length === 4 && tr.cells[3].textContent.startsWith("Evitar.") && tr.cells[3].getAttribute("data-label") === "Precaución";
    }],
    ["cabecera de tabla sin «|» final", () => {
      const el = render("**Medicación:**\n| Medicamento | Para qué sirve | Precaución\n|---|---|---|\n| Paracetamol | Dolor | Ok\n**Señales de alarma:** Fiebre.", base);
      return el.querySelectorAll("tbody tr").length === 1 && /Señales de alarma/.test(el.innerText);
    }],
    ["solo la línea separadora (respuesta a medias)", () => !!render("**Medicación:**\n|---|---|---|", base)],
    ["si el formato falla, la respuesta se muestra igual y sin dosis", () => {
      const el = document.createElement("div");
      const original = window.renderAnswer;
      window.renderAnswer = () => { throw new Error("prueba"); };
      try { showAnswer(el, "**Resumen:** Toma 400 mg de ibuprofeno.", base); } finally { window.renderAnswer = original; }
      return el.innerText.includes("Resumen") && !/400 mg/.test(el.innerText);
    }],
    ["imagen con hallazgo grave: banner", () => imageNotes("Se observa una hemorragia en el hemisferio izquierdo.").banner],
    ["imagen sin hallazgo grave: sin banner", () => !imageNotes("Radiografía de tórax sin alteraciones.").banner],
    ["imagen: aviso fijo de análisis orientativo", () => imageNotes("Radiografía normal.").note],
  ];
  const failures = [];
  for (const [name, check] of cases) {
    try { if (!check()) failures.push(name); } catch (e) { failures.push(`${name} (error: ${e.message})`); }
  }
  return { passed: cases.length - failures.length, total: cases.length, failures };
})();
