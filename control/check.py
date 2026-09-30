# /// script
# requires-python = ">=3.11"
# dependencies = ["openpyxl==3.1.5"]
# ///
"""Checks the recorded conversations in control/transcripts/ without trusting Claude or the server.

Tool results
  Every printed value is re-read from the cell its ref names (openpyxl, a different reader from the
  server's) and its display re-formatted here. Differences, ratios, sums and relative changes are
  recomputed from their input cells; margin-bridge values are compared with tools/expected.py.

Figures in Claude's answer (dollars, with or without "$", percents, points)
  A figure is supported when a tool returned the same number in that conversation, and
  - its sign agrees when the answer writes one (+/-);
  - the period and reporting unit named in the same sentence, if any, are those of the supporting value;
  - a figure found only inside a report comment counts only when the answer quotes it (inside quotation
    marks); it is then counted as a quotation, never as a supported financial figure.
Cell citations
  Any "Sheet!B8" in the answer is a citation. A sheet or cell no tool returned is rejected, and the figure
  written just before a citation must equal that cell or a value computed from it.
Completeness
  Every question must have a transcript and a person's grade bound to that transcript's SHA-256; a
  re-recorded answer with an old grade fails. Whether a refusal or a wording is right stays with the person:
  a pattern matcher cannot judge meaning, and these checks do not claim to.

    uv run control/check.py        -> control/results.json and control/RESULTS.md; exit 1 on any failure
"""

import hashlib
import json
import re
import sys
from datetime import date
from pathlib import Path

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parent.parent
QUESTIONS = json.loads((ROOT / "control/questions.json").read_text())
GRADES_PATH = ROOT / "control/grades.json"
EXPECTED = json.loads((ROOT / "tests/FinancialStatements.Tests/expected.json").read_text())
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]
UNITS = ["Wholesale", "Online", "Service"]
_books = {}


def grades():
    return json.loads(GRADES_PATH.read_text()) if GRADES_PATH.exists() else {}


def fiscal_start(folder):
    m = json.loads((ROOT / folder / "approved.json").read_text())
    return m.get("fiscal_year_start_month")


def cell_at(folder, ref):
    m = re.match(r"^\[(.+?\.xlsx)\](.+)!([A-Z]+\d+)$", ref)
    if not m:
        raise ValueError(ref)
    key = (folder, m[1])
    if key not in _books:
        _books[key] = load_workbook(ROOT / folder / m[1])
    ws = _books[key][m[2]]
    c = ws[m[3]]
    header = [ws.cell(row=7, column=k).value for k in range(1, ws.max_column + 1)]
    column = ws.cell(row=7, column=c.column).value if c.column <= len(header) else None
    return c.value, "%" in (c.number_format or ""), column


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def fmt_amount(v):
    r = round(v)
    return ("-$" if r < 0 else "$") + f"{abs(r):,}"


def fmt_change(v):
    r = round(v)
    return ("+$" if r > 0 else "-$" if r < 0 else "$") + f"{abs(r):,}"


def fmt_points(d, decimals=2):
    p = round(d * 100, decimals)
    return ("+" if p > 0 else "-" if p < 0 else "") + f"{abs(p):.{decimals}f} pts"


# ---------------------------------------------------------------- periods


def period_words(ref, column, fy_start):
    """Words a sentence may use for the period of a cell: month names, quarter, first half, year to date."""
    m = re.match(r"^\[.*?(\d{4})-(\d{2}).*?\.xlsx\]", ref)
    if not m:
        return set()
    month = int(m[2])
    words = {f"{m[1]}-{m[2]}"}
    col = (column or "").lower()
    if "quarter" in col and fy_start:
        into = (month - fy_start) % 12
        q = into // 3 + 1
        first = month - into % 3
        words |= {f"q{q}", ["first", "second", "third", "fourth"][q - 1] + " quarter", "quarter"}
        for k in range(first, month + 1):
            words |= {MONTHS[(k - 1) % 12], MONTHS[(k - 1) % 12][:3]}
    elif "year" in col and fy_start:
        into = (month - fy_start) % 12
        words |= {"year to date", "ytd", "so far this year"}
        if into == 5:
            words |= {"first half", "h1"}
        for k in range(month - into, month + 1):
            words |= {MONTHS[(k - 1) % 12], MONTHS[(k - 1) % 12][:3]}
    else:
        words |= {MONTHS[month - 1], MONTHS[month - 1][:3]}
    return words


PERIOD_VOCAB = re.compile(r"\b(q[1-4]|(?:first|second|third|fourth) quarter|first half|h1|year to date|ytd|" + "|".join(MONTHS) + r"|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)\b", re.I)


# ---------------------------------------------------------------- transcript


def parse(path):
    calls, answer, model = {}, "", ""
    for line in path.read_text().splitlines():
        e = json.loads(line)
        if e["type"] == "system" and e.get("model"):
            model = e["model"]
        if e["type"] == "assistant":
            for c in e["message"].get("content", []):
                if c["type"] == "tool_use":
                    calls[c["id"]] = {"name": c["name"].split("__")[-1], "input": c["input"], "result": "", "error": False}
        if e["type"] == "user":
            for c in e["message"].get("content", []) if isinstance(e["message"].get("content"), list) else []:
                if c.get("type") == "tool_result" and c["tool_use_id"] in calls:
                    content = c.get("content")
                    text = content if isinstance(content, str) else "".join(x.get("text", "") for x in content or [])
                    calls[c["tool_use_id"]].update(result=text, error=bool(c.get("is_error")))
        if e["type"] == "result":
            answer = e.get("result", "")
    return list(calls.values()), answer, model


# ---------------------------------------------------------------- facts from tool results


class Facts:
    def __init__(self, folder):
        self.folder = folder
        self.fy = fiscal_start(folder)
        self.items = []  # {kind, value (signed), periods, units, refs, source}
        self.refs = set()
        self.problems = []
        self.recomputed = 0
        self.bridge_checked = 0
        self.rows = set()

    def add(self, kind, value, refs, source="tool"):
        periods, units = set(), set()
        for r in refs:
            try:
                _, _, column = cell_at(self.folder, r)
            except Exception:
                column = None
            periods |= period_words(r, column, self.fy)
            sheet = re.match(r"^\[.+?\](.+)!", r)
            if sheet:
                units.add(sheet[1])
        self.items.append({"kind": kind, "value": value, "periods": periods, "units": units, "refs": list(refs), "source": source})


def walk(obj, f, is_error):
    if isinstance(obj, list):
        for x in obj:
            walk(x, f, is_error)
        return
    if not isinstance(obj, dict):
        if is_error and isinstance(obj, str):
            for n in numbers(obj):
                f.add(n["kind"], n["signed"], [], source="refusal message")
        return
    if isinstance(obj.get("ref"), str):
        f.refs.add(obj["ref"])
    if isinstance(obj.get("row"), str):
        f.rows.add(obj["row"])
    if isinstance(obj.get("text"), str) and isinstance(obj.get("ref"), str):
        for n in numbers(obj["text"]):
            f.add(n["kind"], n["signed"], [obj["ref"]], source="report comment")
    if isinstance(obj.get("ref"), str) and "display" in obj and "value" in obj and "formula" not in obj:
        ref, v, disp = obj["ref"], obj["value"], obj["display"]
        actual, pct, _ = cell_at(f.folder, ref)
        if v is None:
            if actual is not None:
                f.problems.append(f"{ref}: tool said blank, cell holds {actual}")
        else:
            if actual is None or abs(actual - v) > 1e-9:
                f.problems.append(f"{ref}: tool value {v}, cell {actual}")
            want = f"{actual * 100:.1f}%" if pct else fmt_amount(actual)
            if disp != want:
                f.problems.append(f"{ref}: tool display {disp!r}, expected {want!r}")
            f.add("percent" if pct else "dollars", v * 100 if pct else v, [ref])
    if "formula" in obj and isinstance(obj.get("inputs"), list) and "value" in obj:
        inputs, v, formula, disp = obj["inputs"], obj["value"], obj["formula"], obj.get("display", "")
        f.refs.update(inputs)
        cells = [cell_at(f.folder, r) for r in inputs]
        if formula.startswith("to - from") and len(inputs) == 2:
            (a, pa, _), (b, _, _) = cells
            d = b - a
            want = fmt_points(d) if pa else fmt_change(d)
            if want != disp:
                f.problems.append(f"change {disp!r} from {inputs}, recomputed {want!r}")
            f.recomputed += 1
            f.add("points" if pa else "dollars", d * 100 if pa else d, inputs)
            if obj.get("relative") and a:
                f.add("percent", (b - a) / abs(a) * 100, inputs)
        elif formula == "sum of inputs":
            total = sum(c[0] for c in cells)
            if abs(total - v) > 1e-6 or fmt_amount(total) != disp:
                f.problems.append(f"sum {disp!r} from {inputs}, recomputed {total}")
            f.recomputed += 1
            f.add("dollars", total, inputs)
        elif formula == "(to - from) / |from|" and len(inputs) == 2:
            a, b = cells[0][0], cells[1][0]
            rel = (b - a) / abs(a)
            if abs(rel - v) > 1e-9:
                f.problems.append(f"relative change {disp!r} from {inputs}, recomputed {rel}")
            f.recomputed += 1
            f.add("percent", rel * 100, inputs)
            m = re.search(r"([-+]?\d+\.\d+) pts", obj.get("note") or "")
            if m:
                f.add("points", float(m[1]), inputs)
        elif isinstance(obj.get("numerator"), str) and isinstance(obj.get("denominator"), list):
            n = cell_at(f.folder, obj["numerator"])[0]
            d = sum(cell_at(f.folder, r)[0] for r in obj["denominator"])
            if abs(n / d - v) > 1e-9:
                f.problems.append(f"share {disp!r}, recomputed {n / d}")
            f.recomputed += 1
            f.add("percent", v * 100, inputs)
        elif re.fullmatch(r"[A-Za-z ]+ / [A-Za-z ]+", formula) and len(inputs) == 2:
            n, d = cells[0][0], cells[1][0]
            if abs(n / d - v) > 1e-9:
                f.problems.append(f"ratio {disp!r} from {inputs}, recomputed {n / d}")
            f.recomputed += 1
            f.add("percent", v * 100, inputs)
        else:  # bridge terms: compared with tools/expected.py in check_bridge
            f.add("points" if disp.endswith("pts") else "percent", v * 100, inputs)
    for k, x in obj.items():
        if k != "ref":
            walk(x, f, is_error)


def check_bridge(result, f):
    if result.get("column") != "Quarter to Date Actual" or result.get("from", {}).get("period_end") != "2026-03-31" or result.get("to", {}).get("period_end") != "2026-06-30":
        return
    x = EXPECTED["bridge"]
    pairs = [(result["rate_effect_total"]["value"], x["rate_effect_total"]), (result["mix_effect_total"]["value"], x["mix_effect_total"]),
             (result["company_rate_from"]["value"], x["company_rate_from"]), (result["company_rate_to"]["value"], x["company_rate_to"])]
    for u in result["units"]:
        e = x["units"][u["unit"]]
        pairs += [(u["rate_effect"]["value"], e["rate_effect"]), (u["mix_effect"]["value"], e["mix_effect"]),
                  (u["rate_from"]["value"], e["rate_from"]), (u["rate_to"]["value"], e["rate_to"])]
    for got, want in pairs:
        if abs(got - want) > 1e-9:
            f.problems.append(f"margin_bridge value {got} differs from expected.py {want}")
    f.bridge_checked += len(pairs)


# ---------------------------------------------------------------- figures in the answer

MONTH_RE = "|".join(m.capitalize() for m in MONTHS) + "|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec"
NUM_RE = re.compile(r"([-+−]?\(?\$?\s?\d[\d,]*(?:\.\d+)?)\)?\s*(million|thousand|[MK]\b|k\b)?\s*(%|percentage points?|pts?\b|points?\b)?", re.I)


def numbers(text):
    s = text
    s = re.sub(rf"\b(?:{MONTH_RE})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}\b", lambda m: " " * len(m[0]), s)
    s = re.sub(rf"\b(?:{MONTH_RE})\.?\s+\d{{1,2}}\b", lambda m: " " * len(m[0]), s)
    s = re.sub(rf"\b(?:{MONTH_RE})\.?\s+\d{{4}}\b", lambda m: " " * len(m[0]), s)
    s = re.sub(r"\b\d{4}-\d{2}(?:-\d{2})?\b", lambda m: " " * len(m[0]), s)
    s = re.sub(r"\b(?:Q[1-4]|H[12])\b", lambda m: " " * len(m[0]), s)
    s = re.sub(r"(?<![$\d,.])\b(?:19|20)\d{2}\b(?!\d|%|,\d|\.\d)", lambda m: " " * len(m[0]), s)
    s = re.sub(r"\b[A-Z]{1,2}\d{1,4}\b", lambda m: " " * len(m[0]), s)
    s = re.sub(r"\b\d{1,2}-min\b", lambda m: " " * len(m[0]), s)
    out = []
    for m in NUM_RE.finditer(s):
        raw = m[1]
        digits = re.sub(r"[^\d.]", "", raw)
        if not digits or digits == ".":
            continue
        dec = len(digits.split(".")[1]) if "." in digits else 0
        word = (m[2] or "").lower()
        mult = 1e6 if word in ("million", "m") else 1e3 if word in ("thousand", "k") else 1
        suf = (m[3] or "").lower()
        kind = "percent" if suf.startswith("%") else "points" if suf else "dollars" if "$" in raw else "bare"
        if kind == "bare" and ("," in raw or mult > 1):
            kind = "dollars"  # an amount written without "$"
        if kind == "bare":
            continue
        value = float(digits) * mult
        negative = raw.lstrip().startswith(("-", "−", "(")) or raw.lstrip().startswith("(")
        explicit = raw.lstrip()[:1] in "+-−"
        out.append({"token": m[0].strip(), "kind": kind, "value": value, "signed": -value if negative else value,
                    "explicit_sign": explicit, "decimals": dec, "mult": mult, "start": m.start(), "end": m.end()})
    return out


def tolerance(n):
    return max(0.5 * 10 ** -n["decimals"] * n["mult"], 1.0 if n["kind"] == "dollars" and n["mult"] == 1 else 1e-9)


def sentence_at(text, pos):
    starts = [m.end() for m in re.finditer(r"(?<=[.!?])\s+(?=[A-Z*“\"(])|\n", text[:pos])]
    start = starts[-1] if starts else 0
    m = re.search(r"(?<=[.!?])\s+(?=[A-Z*“\"(])|\n", text[pos:])
    return text[start: pos + (m.start() if m else len(text) - pos)]


def inside_quotes(text, pos):
    before = text[:pos]
    return before.count("“") > before.count("”") or before.count('"') % 2 == 1


def judge(n, answer, f):
    """Returns (status, reason). status: supported, quoted, unsupported."""
    cands = [x for x in f.items if x["kind"] == n["kind"] and abs(abs(x["value"]) - n["value"]) <= tolerance(n) + 1e-9]
    if not cands:
        return "unsupported", "no tool returned this figure"
    if n["explicit_sign"]:
        signed = [x for x in cands if (x["value"] >= 0) == (n["signed"] >= 0) or abs(x["value"]) < 1e-12]
        if not signed:
            return "unsupported", "sign differs from every supporting value"
        cands = signed
    if all(x["source"] == "report comment" for x in cands):
        quoted_somewhere = any(inside_quotes(answer, m["start"]) for m in numbers(answer) if m["kind"] == n["kind"] and abs(m["value"] - n["value"]) < 1e-9)
        return ("quoted", "quoted from a report comment") if quoted_somewhere else ("unsupported", "found only in a report comment, stated as a fact")
    cands = [x for x in cands if x["source"] != "report comment"]
    sent = sentence_at(answer, n["start"]).lower().replace("-", " ").replace("–", " ")
    mentioned = {m.lower() for m in PERIOD_VOCAB.findall(sent)}
    unit_text = sent
    for row in f.rows:  # "Service labor" names a row, not the Service unit
        unit_text = unit_text.replace(row.lower(), " ")
    mentioned_units = {u for u in UNITS if re.search(rf"\b{u.lower()}\b", unit_text)}
    def fits(x):
        if mentioned and x["periods"] and not (mentioned & {w.lower() for w in x["periods"]}):
            return False
        if mentioned_units and x["units"] and not (mentioned_units & x["units"]) and not ("company" in sent or "total" in sent):
            return False
        return True
    fitting = [x for x in cands if fits(x)]
    if not fitting:
        return "unsupported", "period or unit named in the sentence differs from the supporting value's"
    return "supported", ""


CITE_RE = re.compile(r"(?:\[?([\w.-]+\.xlsx)\]?[,\s]*)?((?:[A-Z][A-Za-z]*\s)?[A-Z][A-Za-z]*)!([A-Z]{1,3}\d{1,5})((?:`?,\s*`?[A-Z]{1,3}\d{1,5}\b(?!!))*)")


def citations(answer, f):
    known = {re.match(r"^\[.+?\](.+)!", r)[1] for r in f.refs if re.match(r"^\[.+?\](.+)!", r)}
    out = []
    for g, m in enumerate(CITE_RE.finditer(answer)):
        sheet = m[2]
        if sheet not in known and " " in sheet and sheet.split(" ", 1)[1] in known:
            sheet = sheet.split(" ", 1)[1]  # "June Total!C21": the sheet is "Total"
        out.append({"file": m[1], "sheet": sheet, "cell": m[3], "start": m.start(), "end": m.end(), "group": g})
        for extra in re.findall(r"[A-Z]{1,3}\d{1,5}", m[4] or ""):
            out.append({"file": m[1], "sheet": sheet, "cell": extra, "start": m.start(), "end": m.end(), "group": g})
    for prev, cur in zip(out, out[1:]):
        if cur["group"] != prev["group"] and re.fullmatch(r"[\s,;`\[\]]*(?:and)?[\s`\[]*", answer[prev["end"]:cur["start"]]):
            old = cur["group"]
            for c in out:
                if c["group"] == old:
                    c["group"] = prev["group"]
    return out


def refs_for(c, f):
    return [r for r in f.refs if r.endswith(f"]{c['sheet']}!{c['cell']}") and (not c["file"] or r.startswith(f"[{c['file']}]"))]


def check(q, path=None, grade=None):
    path = path or ROOT / "control/transcripts" / f"{q['id']}.jsonl"
    folder = q.get("folder", "samples/approved-exports")
    calls, answer, model = parse(path)
    f = Facts(folder)
    for c in calls:
        if not c["result"]:
            continue
        try:
            body = json.loads(c["result"])
        except json.JSONDecodeError:
            walk(c["result"], f, True)
            continue
        walk(body, f, c["error"])
        if c["name"] == "margin_bridge" and not c["error"]:
            check_bridge(body, f)
    cites = citations(answer, f)
    blanked = list(answer)
    for c in cites:
        blanked[c["start"]:c["end"]] = " " * (c["end"] - c["start"])
    blanked = "".join(blanked)
    figures = numbers(blanked)
    verdicts = [(n, *judge(n, blanked, f)) for n in figures]
    unsupported = [f"{n['token']} ({why})" for n, st, why in verdicts if st == "unsupported"]
    quoted = [n["token"] for n, st, _ in verdicts if st == "quoted"]
    invented = sorted({f"{c['sheet']}!{c['cell']}" for c in cites if not refs_for(c, f)})
    mismatched, prev_end = [], 0
    for g in sorted({c["group"] for c in cites}):
        members = [c for c in cites if c["group"] == g]
        before = numbers(blanked[prev_end:members[0]["start"]])
        prev_end = max(c["end"] for c in members)
        if not before:
            continue
        last = before[-1]
        vals = []
        for c in members:
            for r in refs_for(c, f):
                v, pct, _ = cell_at(folder, r)
                if isinstance(v, (int, float)):
                    vals.append(abs(v * 100) if pct else abs(v))
                vals += [abs(x["value"]) for x in f.items if r in x["refs"]]
        if vals and not any(abs(v - last["value"]) <= tolerance(last) + 1e-9 for v in vals):
            mismatched.append(f"{last['token']} cited to {', '.join(c['sheet'] + '!' + c['cell'] for c in members)}")
    digest = sha(path)
    grade = grade if grade is not None else grades().get(q["id"])
    grade_problem = None
    if not grade:
        grade_problem = "no grade"
    elif grade.get("transcript_sha256") != digest:
        grade_problem = f"grade is for transcript {grade.get('transcript_sha256')}, file is {digest}"
    return {
        "id": q["id"], "question": q["question"], "class": q.get("class"), "scene": q.get("scene"), "expect": q["expect"],
        "folder": folder, "model": model, "transcript_sha256": digest,
        "tool_calls": [{"tool": c["name"], "input": c["input"], "error": c["error"]} for c in calls],
        "answer": answer,
        "figures_in_answer": len(figures), "unsupported_figures": unsupported, "figures_quoted_from_report_comments": quoted,
        "citations": len(cites), "citations_to_cells_no_tool_returned": invented,
        "figures_different_from_cited_cell": mismatched,
        "tool_results_checked": {"cells_reread": len(f.refs), "calculations_recomputed": f.recomputed, "bridge_values_compared": f.bridge_checked, "problems": f.problems},
        "behavior": grade, "grade_problem": grade_problem,
    }


def main():
    missing = [q["id"] for q in QUESTIONS if not (ROOT / "control/transcripts" / f"{q['id']}.jsonl").exists()]
    results = [check(q) for q in QUESTIONS if q["id"] not in missing]
    data_hash = hashlib.sha256(b"".join((ROOT / "samples/approved-exports" / n).read_bytes() for n in sorted(p.name for p in (ROOT / "samples/approved-exports").iterdir()))).hexdigest()[:12]
    (ROOT / "control/results.json").write_text(json.dumps({"dataset_sha256": data_hash, "missing_transcripts": missing, "results": results}, indent=2) + "\n")
    figs = sum(r["figures_in_answer"] for r in results)
    uns = sum(len(r["unsupported_figures"]) for r in results)
    inv = sum(len(r["citations_to_cells_no_tool_returned"]) for r in results)
    mis = sum(len(r["figures_different_from_cited_cell"]) for r in results)
    prob = sum(len(r["tool_results_checked"]["problems"]) for r in results)
    stale = [f"{r['id']}: {r['grade_problem']}" for r in results if r["grade_problem"]]
    good = sum(1 for r in results if not r["grade_problem"] and r["behavior"]["behavior"] == "as expected")
    lines = [
        "# Control results", "",
        "Reviewed examples with selected takes, not a first-pass success rate: some questions were recorded several times and one take was published (see each grade).", "",
        f"Recorded conversations: {len(results)} of {len(QUESTIONS)}{'; missing: ' + ', '.join(missing) if missing else ''}. Client: Claude Code in print mode, model {', '.join(sorted({r['model'] for r in results}))}. Sample data SHA-256 (first 12): {data_hash}.",
        f"Tool results: {sum(r['tool_results_checked']['cells_reread'] for r in results)} cell references re-read with openpyxl, {sum(r['tool_results_checked']['calculations_recomputed'] for r in results)} calculations recomputed, {sum(r['tool_results_checked']['bridge_values_compared'] for r in results)} bridge values compared with tools/expected.py; problems: {prob}.",
        f"Figures written in the answers: {figs}, of which {sum(len(r['figures_quoted_from_report_comments']) for r in results)} quoted from a report comment. Unsupported (no tool value with that number, sign, period and unit): {uns}.",
        f"Cell citations: {sum(r['citations'] for r in results)}. Citing a sheet or cell no tool returned: {inv}. Figure different from the cell it cites: {mis}.",
        f"Behavior, graded by a person against the transcript's SHA-256: {good} of {len(results)} as expected{'; grade problems: ' + '; '.join(stale) if stale else ''}.", "",
        "| Question | Class | Expected | Behavior | Figures | Unsupported | Grader's note |", "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        b = r["behavior"] or {}
        lines.append(f"| {r['id']} | {r['class']} | {r['expect']} | {b.get('behavior', 'not graded')} | {r['figures_in_answer']} | {len(r['unsupported_figures'])} | {b.get('why', '')} |")
    (ROOT / "control/RESULTS.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    for r in results:
        if r["unsupported_figures"] or r["citations_to_cells_no_tool_returned"] or r["figures_different_from_cited_cell"] or r["tool_results_checked"]["problems"]:
            print("\n!!", r["id"], json.dumps({k: r[k] for k in ("unsupported_figures", "citations_to_cells_no_tool_returned", "figures_different_from_cited_cell")}), r["tool_results_checked"]["problems"][:5])
    return 1 if (missing or uns or inv or mis or prob or stale or good != len(results)) else 0


if __name__ == "__main__":
    sys.exit(main())
