// Renders the recorded conversations and the exports they cite. Everything comes from data.json,
// which tools/site_data.py builds from the committed workbooks and transcripts.
(async function () {
  const data = await (await fetch("data.json")).json();
  const $ = (s, el = document) => el.querySelector(s);
  const esc = (s) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const BOOKS = Object.keys(data.workbooks);
  const bookLabel = (f) => ({ "IS_2026-03.xlsx": "March 2026", "IS_2026-06.xlsx": "June 2026", "IS_2026-08.xlsx": "August 2026" }[f] || f);
  const blockedFiles = new Set(data.scenes.flatMap((s) => s.blocked.map((b) => b.file)));
  const blockedChecks = Object.fromEntries(data.scenes.flatMap((s) => s.blocked.map((b) => [b.file, b.failed_checks])));
  const UNITS = ["Total", "Wholesale", "Online", "Service"];

  const state = { scene: 0, book: "IS_2026-06.xlsx", sheet: "Total", hits: new Set(), swipe: false };

  const TICK = '<svg viewBox="0 0 17 15" aria-hidden="true"><path d="M2.5 8.2 L6.4 12 L14.6 2.6"/></svg>';

  // ---------- markdown (the subset Claude used) ----------
  function inline(s) {
    return esc(s)
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/ ?⟦([^⟧]+)⟧/g, (_, refs) => {
        const label = refs.split("|").map((r) => r.replace(/^\[IS_(\d{4})-(\d{2})\.xlsx\]/, (m, y, mo) => bookLabel(`IS_${y}-${mo}.xlsx`) + ", ")).join("; ");
        return `<button class="tick" type="button" data-refs="${refs}" aria-pressed="false" aria-label="Show cell: ${esc(label)}" title="${esc(label)}">${TICK}</button>`;
      });
  }
  function markdown(md) {
    const lines = md.split("\n");
    let html = "", i = 0;
    while (i < lines.length) {
      const l = lines[i];
      if (/^\s*\|/.test(l)) {
        const rows = [];
        while (i < lines.length && /^\s*\|/.test(lines[i])) rows.push(lines[i++]);
        const cells = (r) => r.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
        const head = cells(rows[0]);
        const body = rows.slice(2).map(cells);
        html += "<table><thead><tr>" + head.map((h) => `<th>${inline(h)}</th>`).join("") + "</tr></thead><tbody>" +
          body.map((r) => "<tr>" + r.map((c) => `<td>${inline(c)}</td>`).join("") + "</tr>").join("") + "</tbody></table>";
        continue;
      }
      if (/^\s*[-*] /.test(l)) {
        const render = (indent) => {
          let out = "<ul>";
          while (i < lines.length && /^\s*[-*] /.test(lines[i])) {
            const ind = lines[i].match(/^\s*/)[0].length;
            if (ind < indent) break;
            if (ind > indent) { out = out.replace(/<\/li>$/, "") + render(ind) + "</li>"; continue; }
            out += `<li>${inline(lines[i].replace(/^\s*[-*] /, ""))}</li>`;
            i++;
          }
          return out + "</ul>";
        };
        html += render(l.match(/^\s*/)[0].length);
        continue;
      }
      if (/^#{1,4} /.test(l)) { html += `<p><strong>${inline(l.replace(/^#+ /, ""))}</strong></p>`; i++; continue; }
      if (l.trim() === "") { i++; continue; }
      const para = [];
      while (i < lines.length && lines[i].trim() !== "" && !/^\s*(\||[-*] |#)/.test(lines[i])) para.push(lines[i++]);
      html += `<p>${inline(para.join(" "))}</p>`;
    }
    return html;
  }

  // ---------- questions and answer ----------
  const qBox = $(".questions");
  data.scenes.forEach((s, n) => {
    const b = document.createElement("button");
    b.className = "q"; b.type = "button"; b.setAttribute("role", "tab");
    b.innerHTML = `“${esc(s.question)}”<small>${esc(s.label)}</small>`;
    b.addEventListener("click", () => showScene(n));
    qBox.appendChild(b);
  });

  function showScene(n) {
    state.scene = n;
    const s = data.scenes[n];
    qBox.querySelectorAll(".q").forEach((b, k) => b.setAttribute("aria-selected", String(k === n)));
    $(".answer-meta").textContent = `Claude's recorded answer, after ${s.tools.length} tool calls: ${[...new Set(s.tools)].join(", ")}.`;
    $(".answer-body").innerHTML = markdown(s.answer);
    $(".answer-foot").innerHTML = `Unedited, except that cell references are shown as tick marks. <a href="${s.transcript}">Full transcript with every tool result</a>.`;
    $(".answer-body").querySelectorAll(".tick").forEach((t) => t.addEventListener("click", () => pick(t)));
    const ticks = [...$(".answer-body").querySelectorAll(".tick")];
    // Open on the rate the question is about when the answer cites it, else on the first citation.
    const first = ticks.filter((t) => /IS_2026-06\.xlsx\]Total!C21$/.test(t.dataset.refs)).pop() || ticks[0];
    if (!first && s.blocked.length) { state.hits = new Set(); state.book = s.blocked[0].file; state.sheet = "Total"; state.swipe = false; render(); }
    else if (first) pick(first, true);
    else render();
  }

  function pick(tick, quiet) {
    document.querySelectorAll(".tick").forEach((t) => t.setAttribute("aria-pressed", "false"));
    tick.setAttribute("aria-pressed", "true");
    const refs = tick.dataset.refs.split("|");
    state.hits = new Set(refs);
    const last = refs[refs.length - 1].match(/^\[(.+?)\](.+)!([A-Z]+\d+)$/);
    state.book = last[1]; state.sheet = last[2]; state.swipe = !quiet;
    render();
    if (!quiet && window.matchMedia("(max-width: 1080px)").matches) $(".desk").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // ---------- the packet ----------
  function render() {
    const books = $(".books");
    books.innerHTML = "";
    for (const f of BOOKS) {
      const b = document.createElement("button");
      b.type = "button"; b.className = "book" + (blockedFiles.has(f) ? " blocked" : ""); b.setAttribute("role", "tab");
      b.setAttribute("aria-selected", String(f === state.book));
      b.textContent = bookLabel(f) + (blockedFiles.has(f) ? ", blocked" : "");
      b.addEventListener("click", () => { state.book = f; state.sheet = "Total"; state.swipe = false; render(); });
      books.appendChild(b);
    }
    const wb = data.workbooks[state.book];
    const sheet = wb.find((s) => s.name === state.sheet) || wb[0];
    const h = sheet.header;
    $(".sheet-head").innerHTML = `<b>${esc(h[0])}</b>${esc(h[1])}<br>${esc(h[2])}<br>${esc(h[3])}<br><span style="color:var(--ink-soft)">${esc(h[4])}</span>`;
    const refOf = (addr) => `[${state.book}]${sheet.name}!${addr}`;
    let html = "<thead><tr><th scope=\"col\" style=\"text-align:left\"></th>" + sheet.columns.map((c) => `<th scope="col">${esc(c)}</th>`).join("") + "</tr></thead><tbody>";
    for (const r of sheet.rows) {
      const cls = r.kind === "section" ? "section" : r.kind === "percent" ? "percent" : r.bold ? "total" : "line";
      html += `<tr class="${cls}"><td class="label"><span class="rowno">${r.row}</span>${esc(r.label)}</td>` +
        r.cells.map((c) => state.hits.has(refOf(c.addr))
          ? `<td class="hit${state.swipe ? " swipe" : ""}" data-addr="${c.addr}">${esc(c.display)}<span class="tie" title="Cited in the answer">${TICK}</span></td>`
          : `<td data-addr="${c.addr}">${esc(c.display)}</td>`).join("") + "</tr>";
    }
    html += "</tbody>";
    $(".statement").innerHTML = html;
    const wrap = $(".table-wrap");
    wrap.querySelectorAll(".comment").forEach((e) => e.remove());
    for (const c of sheet.comments) {
      const d = document.createElement("div");
      d.className = "comment" + (state.hits.has(refOf(c.addr)) ? " hit" : "");
      d.textContent = `Report comment (${c.addr}): ${c.text}`;
      wrap.appendChild(d);
    }
    const note = $(".margin-note");
    $(".paper").classList.toggle("blocked", blockedFiles.has(state.book));
    if (blockedFiles.has(state.book)) {
      note.hidden = false;
      note.innerHTML = "<b>Blocked. No figure is read from this export.</b><ul>" + blockedChecks[state.book].map((f) => `<li>${esc(f.replace(/^IS_\d{4}-\d{2}\.xlsx:\s*/, ""))}</li>`).join("") + "</ul>";
    } else note.hidden = true;
    const tabs = $(".sheet-tabs");
    tabs.innerHTML = "";
    for (const u of UNITS) {
      const present = wb.some((s) => s.name === u);
      const t = document.createElement("button");
      t.type = "button"; t.className = "sheet-tab" + (present ? "" : " missing"); t.setAttribute("role", "tab");
      t.setAttribute("aria-selected", String(u === sheet.name));
      t.textContent = u;
      if (present) t.addEventListener("click", () => { state.sheet = u; state.swipe = false; render(); });
      else { t.disabled = true; t.title = "Not in this export"; }
      tabs.appendChild(t);
    }
    const hit = $(".statement td.hit");
    if (hit && state.swipe) hit.scrollIntoView({ block: "nearest", inline: "nearest" });
  }

  // ---------- checked ----------
  const s = data.summary;
  $(".summary").innerHTML = `${s.conversations} recorded conversations. <span class="n">${s.figures}</span> figures written by Claude, <span class="n">${s.untraced}</span> that no tool returned. <span class="n">${s.citations}</span> cell citations, <span class="n">${s.bad_citations}</span> wrong. <span class="n">${s.as_expected}</span> of ${s.conversations} behaved as expected, read by a person.`;
  const tb = $(".ledger tbody");
  const expectWord = { answer: "Answer", refuse: "Refuse", stop: "Stop", boundary: "Say the limit" };
  for (const r of data.ledger) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td class="lq"><a href="${r.transcript}">“${esc(r.question)}”</a></td><td class="exp">${expectWord[r.expect] || r.expect}</td>` +
      `<td class="why">${r.behavior === "as expected" ? '<span class="ok" aria-label="as expected">✓</span>' : ""}${esc(r.why || "")}</td>` +
      `<td class="num">${r.figures}</td><td class="num">${r.untraced}</td>`;
    tb.appendChild(tr);
  }

  const fromHash = { "#stop": 1, "#limit": 2 }[location.hash];
  showScene(fromHash ?? 0);
})();
