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
  const render = (text, ctx, opts) => {
    const el = document.createElement("div");
    renderAnswer(el, text, ctx, opts);
    return el;
  };
  const flags = (el) => [...el.querySelectorAll(".prose-flag")].map((f) => f.textContent.toLowerCase());
  const prose = "**Resumen:** Dolor de cabeza.\n\n**Qué puedes hacer:** Descansa. Si persiste, toma paracetamol (hasta 1 gramo) o ibuprofeno (hasta 400 mg).";
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
    // The same rules in the free text, outside the medication table
    ["texto, embarazo: ibuprofeno marcado con aviso", () => {
      const el = render(prose, { ...base, pregnant: true });
      return flags(el).join() === "ibuprofeno" && el.querySelector(".prose-warning").textContent.includes("embarazo");
    }],
    ["texto: dosis del modelo cambiadas por el prospecto", () => {
      const t = render(prose, base).innerText;
      return !/\d\s?(mg|gramo)/i.test(t) && t.includes("(según el prospecto)");
    }],
    ["texto, sin contexto: ibuprofeno sin marcar", () => flags(render(prose, base)).length === 0],
    ["texto, embarazo: 'no tomes ibuprofeno' no se marca", () =>
      flags(render("**Qué puedes hacer:** No tomes ibuprofeno en el embarazo; mejor paracetamol.", { ...base, pregnant: true })).length === 0],
    ["texto, niño: aspirina marcada", () =>
      flags(render("Puedes darle aspirina para la fiebre.", { ...base, who: "child", age: "4" })).join() === "aspirina"],
    ["texto, niño: 'nunca le des aspirina' no se marca", () =>
      flags(render("Nunca le des aspirina a un niño.", { ...base, who: "child", age: "4" })).length === 0],
    ["texto, Sintrom: naproxeno marcado", () =>
      flags(render("Para el dolor puedes tomar naproxeno.", { ...base, meds: "Sintrom" })).join() === "naproxeno"],
    ["texto, asma: ibuprofeno marcado", () =>
      flags(render("Toma ibuprofeno con comida.", { ...base, meds: "asma" })).join() === "ibuprofeno"],
    ["texto: antibiótico que receta el médico no se marca", () =>
      flags(render("Si es bacteriana, tu médico puede recetarte amoxicilina.", base)).length === 0],
    ["texto, alergia a penicilina: amoxicilina marcada, la alergia no", () =>
      flags(render("Como tienes alergia a la penicilina, tu médico valorará. Puede recetarte amoxicilina.",
                   { ...base, allergies: "penicilina" })).join() === "amoxicilina"],
    ["texto: valores de analítica intactos", () => {
      const t = render("Tu hemoglobina es 11,2 g/dL y la glucosa 104 mg/dL.", base).innerText;
      return t.includes("11,2 g/dL") && t.includes("104 mg/dL");
    }],
    ["texto: respuesta fija (escrita en código) conserva sus dosis", () =>
      render("**Adultos:** 1 comprimido de 500 mg a 1 g cada 6–8 horas.", base, { trusted: true }).innerText.includes("500 mg")],
    ["texto: dosis de jarabe quitada", () => !/mg|ml/i.test(render("Dale 120 mg/5 ml cada 8 horas.", base).innerText)],
    ["texto: un aviso por medicamento aunque se repita", () => {
      const el = render("Toma ibuprofeno. Si no mejora, otra vez ibuprofeno.", { ...base, pregnant: true });
      return el.querySelectorAll(".prose-warning").length === 1 && (el.querySelector(".prose-warning").textContent.match(/Ibuprofeno/g) || []).length === 1;
    }],
    ["texto, embarazo: 'Evitar: ibuprofeno, naproxeno' no se marca", () =>
      flags(render("Descansa.\n\nEvitar: Ibuprofeno, naproxeno y aspirina.", { ...base, pregnant: true })).length === 0],
    ["'**Evitar:**' en negrita se queda dentro de la tarjeta", () => {
      const el = render("**Medicación:**\n| Medicamento | Para qué sirve | Precaución |\n|---|---|---|\n| Paracetamol | Dolor | Ok |\n\n" +
        "**Evitar:** Ibuprofeno.\n\n**Señales de alarma:** Fiebre alta.", { ...base, pregnant: true });
      return !!el.querySelector(".med-card .med-avoid") && flags(el).length === 0 && /Señales de alarma/.test(el.innerText);
    }],
    ["texto: la tabla sigue igual (ibuprofeno tachado en embarazo)", () =>
      flagged(render(table(meds) + "\n" + prose, { ...base, pregnant: true }), "Ibuprofeno")],
    // Medication tables written badly by the model are rebuilt (normalizeMedTable)
    ["tabla en una línea sin cabecera (caso real): se reconstruye", () => {
      const el = render("**Resumen:** Dolor de cabeza.\n\n**Medicación:**\nParacetamol | Para aliviar el dolor | No tomar más de 4 gramos al día. | Evitar: Ibuprofeno.\n\n**Señales de alarma:** Si empeora.", base);
      const rows = el.querySelectorAll(".med-card tbody tr");
      return rows.length === 1 && rows[0].cells[0].textContent === "Paracetamol" && how(el, "Paracetamol").includes("No superes 3 g")
        && !/4 gramos/.test(el.innerText) && el.querySelector(".med-card .med-avoid")?.textContent.includes("Ibuprofeno")
        && /Señales de alarma/.test(el.innerText);
    }],
    ["título y fila sin barra inicial en la misma línea", () => {
      const el = render("**Medicación:** Paracetamol | Dolor | Ok\n**Señales de alarma:** Fiebre.", base);
      return el.querySelectorAll(".med-card tbody tr").length === 1;
    }],
    ["varios medicamentos en una sola línea: una fila cada uno", () => {
      const el = render("**Medicación:**\nParacetamol | Dolor | Ok | Loratadina | Alergia | Puede dar sueño\n**Señales de alarma:** x", base);
      const names = [...el.querySelectorAll(".med-card tbody tr")].map((tr) => tr.cells[0].textContent);
      return names.join() === "Paracetamol,Loratadina";
    }],
    ["filas con barras pero sin cabecera ni separador", () => {
      const el = render("**Medicación:**\n| Paracetamol | Dolor | Ok |\n| Ibuprofeno | Dolor | Con comida |\n**Señales de alarma:** x", { ...base, pregnant: true });
      return el.querySelectorAll(".med-card tbody tr").length === 2 && flagged(el, "Ibuprofeno");
    }],
    ["tabla bien escrita: no se toca", () => {
      const el = render(table(meds), base);
      return el.querySelectorAll(".med-card tbody tr").length === meds.length && normalizeMedTable(table(meds)) === table(meds);
    }],
    ["dosis en 'gramos' dentro de la tabla: quitada", () =>
      !/gramos/.test(render(table([["Paracetamol", "Dolor", "No más de 4 gramos al día"]]), base).innerText)],
    ["dosis en un párrafo de la tarjeta de medicación: quitada", () =>
      !/400 mg/.test(render("**Medicación:**\nPuedes tomar ibuprofeno 400 mg cada 8 horas.\n**Señales de alarma:** x", base).innerText)],
    // Medicines for the symptom sent by the server (X-Suggested-Meds)
    ["'No hace falta medicación' con lista del servidor: tabla con suero oral (niño)", () => {
      const el = render("**Resumen:** Diarrea.\n\n**Medicación:** No hace falta medicación.\n\n**Señales de alarma:** Si no orina.",
        { ...base, who: "child", age: "4" }, { suggested: [["Suero oral de rehidratación", "Repone el agua y las sales"]] });
      const rows = el.querySelectorAll(".med-card tbody tr");
      return rows.length === 1 && rows[0].cells[0].textContent.startsWith("Suero oral") && !/No hace falta/.test(el.innerText)
        && how(el, "Suero oral").startsWith("Según el peso");
    }],
    ["lista suelta con lista del servidor: tabla", () => {
      const el = render("**Medicación:** loratadina, cetirizina, suero fisiológico.\n\n**Señales de alarma:** x", base,
        { suggested: [["Loratadina", "Antihistamínico"], ["Cetirizina", "Antihistamínico"], ["Suero fisiológico", "Lavados"]] });
      return el.querySelectorAll(".med-card tbody tr").length === 3 && how(el, "Loratadina") === "1 comprimido de 10 mg al día.";
    }],
    ["con tabla del modelo, la lista del servidor no se añade", () => {
      const el = render(table(meds), base, { suggested: [["Loratadina", "x"]] });
      return el.querySelectorAll(".med-card tbody tr").length === meds.length;
    }],
    ["la línea 'Evitar:' se conserva al montar la tabla", () => {
      const el = render("**Medicación:** No hace falta medicación.\nEvitar: aspirina.\n\n**Señales de alarma:** x", base,
        { suggested: [["Paracetamol", "Dolor"]] });
      return el.querySelectorAll(".med-card tbody tr").length === 1 && /Evitar/.test(el.innerText);
    }],
    // "Evitar: No hace falta medicación." next to a table (screenshot)
    ["'Evitar: No hace falta medicación' con tabla montada: sin contradicción", () => {
      const el = render("**Qué puedes hacer:** Estira el cuello.\n\n**Medicación:**\nEvitar: No hace falta medicación.\n\n**Señales de alarma:** Fiebre.", base,
        { suggested: [["Paracetamol", "Alivia el dolor"], ["Ibuprofeno", "Alivia el dolor y la inflamación"]] });
      return el.querySelectorAll(".med-card tbody tr").length === 2 && !/No hace falta/i.test(el.innerText)
        && el.querySelector(".med-avoid")?.textContent === "Evitar: nada en particular.";
    }],
    ["tabla del modelo y 'No hace falta medicación' suelto: se quita", () => {
      const el = render(table(meds).replace("Evitar: nada en particular.", "No hace falta medicación.\nEvitar: aspirina."), base);
      return !/No hace falta/i.test(el.innerText) && /Evitar: aspirina/.test(el.innerText);
    }],
    ["'Evitar: ninguno' pasa a 'nada en particular'", () =>
      render(table(meds).replace("Evitar: nada en particular.", "Evitar: ninguno."), base).querySelector(".med-avoid").textContent === "Evitar: nada en particular."],
    ["sin tabla, 'No hace falta medicación' se respeta", () =>
      /No hace falta medicación/.test(render("**Medicación:** No hace falta medicación.\n\n**Señales de alarma:** x", base).innerText)],
    ["tabla con solo la cabecera y medicamentos en texto: se monta la tabla", () => {
      const el = render("**Medicación:**\n| Medicamento | Para qué sirve | Precaución |\n|---|---|---|\nSuero oral: repone sales.\n\n**Señales de alarma:** x",
        base, { suggested: [["Suero oral de rehidratación", "Repone sales"], ["Loperamida", "Reduce deposiciones"]] });
      return el.querySelectorAll(".med-card tbody tr").length === 2;
    }],
    ["tabla con solo la cabecera y sin lista del servidor: sin tabla vacía", () => {
      const el = render("**Medicación:**\n| Medicamento | Para qué sirve | Precaución |\n|---|---|---|\n\n**Señales de alarma:** x", base);
      return !el.querySelector(".med-card table") || el.querySelectorAll(".med-card tbody tr").length > 0;
    }],
    // Battery of 25/10: answers of the real model that showed no table or a contradiction
    ["'**Medicamento:**' en prosa con lista del servidor: tabla", () => {
      const el = render("**Resumen:** x\n**Medicamento:** Paracetamol (Alivia el dolor).\n**Evitar:** Ibuprofeno y aspirina.\n**Señales de alarma:** x",
        base, { suggested: [["Paracetamol", "Alivia el dolor"]] });
      return el.querySelectorAll(".med-card tbody tr").length === 1 && /Evitar: Ibuprofeno/.test(el.innerText) && /Señales de alarma/.test(el.innerText);
    }],
    ["separador de 4 columnas en una tabla de 3: tabla", () => {
      const el = render("**Medicamentos:** | Medicamento | Para qué sirve | Precaución |\n|---|---|---|---|\n| Paracetamol | Alivia el dolor | Lee el prospecto |\n**Señales de alarma:** x", base);
      return el.querySelectorAll(".med-card tbody tr").length === 1;
    }],
    ["fila '| **Evitar:** | Ibuprofeno… |' va a Evitar, no es un medicamento", () => {
      const el = render("**Medicamento:** | Medicamento | Para qué sirve | Precaución |\n|---|---|---|---|\n| Suero oral | Repone sales | Bebe despacio |\n" +
        "| **Evitar:** | Ibuprofeno, Naproxeno | No los tomes porque estás embarazada. |\n**Señales de alarma:** x", { ...base, pregnant: true });
      const rows = [...el.querySelectorAll(".med-card tbody tr")];
      return rows.length === 1 && rows[0].cells[0].textContent.startsWith("Suero") && /Evitar: Ibuprofeno/.test(el.querySelector(".med-avoid")?.textContent || "");
    }],
    ["'No hace falta medicación' en una celda: se quita", () => {
      const el = render("**Medicación:**\n| Medicamento | Para qué sirve | Precaución |\n|---|---|---|\n| Paracetamol | Alivia el dolor | No hace falta medicación. |\n**Señales de alarma:** x", base);
      return el.querySelectorAll(".med-card tbody tr").length === 1 && !/No hace falta/i.test(el.innerText);
    }],
    ["fila en línea acabada en 'No hace falta medicación': se quita", () => {
      const el = render("**Medicación:** Paracetamol | Para el dolor | Ninguna | Ibuprofeno | Para la inflamación | Ninguna | No hace falta medicación.\n**Señales de alarma:** x", base);
      return el.querySelectorAll(".med-card tbody tr").length === 2 && !/No hace falta/i.test(el.innerText);
    }],
    ["notas internas repetidas por el modelo: se quitan", () => {
      const t = render(table(meds) + "\nEn la tabla de Medicación pon estos medicamentos (los que encajen con la persona), no otros.\n" +
        "Datos oficiales de CIMA (AEMPS), úsalos en lugar de tu memoria:\n- sobre «nolotil»: principio activo: metamizol.", base).innerText;
      return !/CIMA|pon estos medicamentos|principio activo/.test(t);
    }],
    ["solo la tabla, sin 'Medicación:': tarjeta y reglas de seguridad", () => {
      const el = render("| Medicamento | Para qué sirve | Precaución |\n| :--- | :--- | :--- |\n| Paracetamol | Baja la fiebre | Lee el prospecto |\n" +
        "| Ibuprofeno | Baja la fiebre | Con comida |\n| **Evitar:** | nada en particular. | |", { ...base, pregnant: true });
      return !!el.querySelector(".med-card table") && flagged(el, "Ibuprofeno");
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
