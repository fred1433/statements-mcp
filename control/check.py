# /// script
# requires-python = ">=3.11"
# dependencies = ["openpyxl==3.1.5"]
# ///
"""Checks the recorded conversations in control/transcripts/ without trusting Claude or the server.

1. Every printed value a tool returned is re-read from the cell its ref names (openpyxl, a different
   reader from the server's) and its display string is re-formatted here. Differences and ratios are
   recomputed from their input cells; margin-bridge effects are compared with tools/expected.py.
2. Every figure written in Claude's final answer (dollars, percents, points) must match a figure some
   tool returned in that conversation, to the precision the answer shows. Otherwise it is "untraced".
3. Every cell Claude cites must be a cell some tool returned, and the figure written just before a
   citation must equal that cell, or a value computed from it.
Whether a refusal, a stop or an answer was the right behavior is graded by a person in
control/grades.json: a pattern matcher cannot judge that, so this script does not pretend to.

    uv run control/check.py        -> control/results.json and control/RESULTS.md
"""

import hashlib
import json
import re
import sys
from pathlib import Path

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parent.parent
QUESTIONS = json.loads((ROOT / "control/questions.json").read_text())
GRADES_PATH = ROOT / "control/grades.json"
GRADES = json.loads(GRADES_PATH.read_text()) if GRADES_PATH.exists() else {}
EXPECTED = json.loads((ROOT / "tests/FinancialStatements.Tests/expected.json").read_text())

_books = {}


def cell_at(folder, ref):
    m = re.match(r"^\[(.+?\.xlsx)\](.+)!([A-Z]+\d+)$", ref)
    if not m:
        raise ValueError(ref)
    key = (folder, m[1])
    if key not in _books:
        _books[key] = load_workbook(ROOT / folder / m[1])
    ws = _books[key][m[2]]
    c = ws[m[3]]
    return c.value, "%" in (c.number_format or "")


def fmt_amount(v):
    r = round(v)
    return ("-$" if r < 0 else "$") + f"{abs(r):,}"


def fmt_change(v):
    r = round(v)
    return ("+$" if r > 0 else "-$" if r < 0 else "$") + f"{abs(r):,}"


def fmt_points(d, decimals=2):
    p = round(d * 100, decimals)
    return ("+" if p > 0 else "-" if p < 0 else "") + f"{abs(p):.{decimals}f} pts"


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


# ---------------------------------------------------------------- traced figures


class Traced:
    def __init__(self):
        self.dollars, self.percents, self.points = [], [], []
        self.refs = set()
        self.derived = {}  # ref -> derived magnitudes computed from it
        self.problems = []
        self.recomputed = 0
        self.bridge_checked = 0
        self.quoted = []  # figures that appear only inside report comments (not verified figures)

    def derive(self, refs, v):
        for r in refs:
            self.derived.setdefault(r, []).append(v)


def walk_tool_result(obj, folder, t, is_error):
    if isinstance(obj, list):
        for x in obj:
            walk_tool_result(x, folder, t, is_error)
        return
    if not isinstance(obj, dict):
        if is_error and isinstance(obj, str):
            for n in numbers(obj):  # figures quoted inside a refusal message (control totals, for example)
                getattr(t, {"dollars": "dollars", "percent": "percents", "points": "points"}[n["kind"]]).append(n["value"])
        return
    if isinstance(obj.get("ref"), str):
        t.refs.add(obj["ref"])  # comments carry a ref too
    if isinstance(obj.get("text"), str) and isinstance(obj.get("ref"), str):
        # A report comment: figures quoted from it are traced to the comment, and counted apart.
        for n in numbers(obj["text"]):
            getattr(t, {"dollars": "dollars", "percent": "percents", "points": "points"}[n["kind"]]).append(n["value"])
            t.quoted.append(n["value"])
    if isinstance(obj.get("ref"), str) and "display" in obj and "value" in obj and "formula" not in obj:
        ref, v, disp = obj["ref"], obj["value"], obj["display"]
        actual, pct = cell_at(folder, ref)
        t.refs.add(ref)
        if v is None:
            if actual is not None:
                t.problems.append(f"{ref}: tool said blank, cell holds {actual}")
        else:
            if actual is None or abs(actual - v) > 1e-9:
                t.problems.append(f"{ref}: tool value {v}, cell {actual}")
            expected_disp = f"{actual * 100:.1f}%" if pct else fmt_amount(actual)
            if disp != expected_disp:
                t.problems.append(f"{ref}: tool display {disp!r}, expected {expected_disp!r}")
            (t.percents if pct else t.dollars).append(abs(v * 100) if pct else abs(v))
    if "formula" in obj and "inputs" in obj and "value" in obj:
        inputs, v, formula = obj["inputs"], obj["value"], obj["formula"]
        for r in inputs:
            t.refs.add(r)
        disp = obj.get("display", "")
        if formula.startswith("to - from") and len(inputs) == 2:
            a, pa = cell_at(folder, inputs[0])
            b, _ = cell_at(folder, inputs[1])
            d = b - a
            want = fmt_points(d) if pa else fmt_change(d)
            if want != disp:
                t.problems.append(f"change {disp!r} from {inputs}, recomputed {want!r}")
            t.recomputed += 1
            if pa:
                t.points.append(abs(d * 100)); t.derive(inputs, abs(d * 100))
            else:
                t.dollars.append(abs(d)); t.derive(inputs, abs(d))
                if obj.get("relative") and a:
                    rel = abs(d / abs(a) * 100)
                    t.percents.append(rel); t.derive(inputs, rel)
        elif re.fullmatch(r"[A-Za-z ]+ / [A-Za-z ]+", formula) and len(inputs) == 2:
            n, _ = cell_at(folder, inputs[0])
            d, _ = cell_at(folder, inputs[1])
            if abs(n / d - v) > 1e-9:
                t.problems.append(f"ratio {disp!r} from {inputs}, recomputed {n / d}")
            t.recomputed += 1
            t.percents.append(abs(v * 100)); t.derive(inputs, abs(v * 100))
        else:
            # Bridge terms and shares: magnitudes in points or percent, checked against expected.py below.
            (t.points if disp.endswith("pts") else t.percents).append(abs(v * 100))
            t.derive(inputs, abs(v * 100))
    for k, x in obj.items():
        if k not in ("ref",):
            walk_tool_result(x, folder, t, is_error)


def check_bridge(result, t):
    """Compare a margin_bridge result for 2026-03 -> 2026-06 QTD with the independent expected.json."""
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
            t.problems.append(f"margin_bridge value {got} differs from expected.py {want}")
    t.bridge_checked += len(pairs)


# ---------------------------------------------------------------- figures in the answer

MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec"
NUM_RE = re.compile(r"([-+−]?\(?\$?\s?\d[\d,]*(?:\.\d+)?)\)?\s*(million|thousand|[MK]\b|k\b)?\s*(%|percentage points?|pts?\b|points?\b)?", re.I)


def numbers(text):
    s = text
    s = re.sub(rf"\b(?:{MONTHS})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}\b", " ", s)
    s = re.sub(rf"\b(?:{MONTHS})\.?\s+\d{{1,2}}\b", " ", s)
    s = re.sub(rf"\b(?:{MONTHS})\.?\s+\d{{4}}\b", " ", s)
    s = re.sub(r"\b\d{4}-\d{2}(?:-\d{2})?\b", " ", s)
    s = re.sub(r"\b(?:Q[1-4]|H[12])\b", " ", s)
    s = re.sub(r"(?<![$\d,.])\b(?:19|20)\d{2}\b(?!\d|%|,\d|\.\d)", " ", s)
    s = re.sub(r"\b[A-Z]{1,2}\d{1,4}\b", " ", s)  # stray cell addresses
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
        if kind == "bare" and ("," in digits or mult > 1):
            kind = "dollars"
        if kind == "bare":
            continue
        out.append({"token": m[0].strip(), "kind": kind, "value": float(digits) * mult, "decimals": dec, "mult": mult, "start": m.start()})
    return out


def tolerance(n):
    return max(0.5 * 10 ** -n["decimals"] * n["mult"], 1.0 if n["kind"] == "dollars" and n["mult"] == 1 else 1e-9)


def is_traced(n, t):
    pool = {"dollars": t.dollars, "percent": t.percents, "points": t.points}[n["kind"]]
    return any(abs(v - n["value"]) <= tolerance(n) + 1e-9 for v in pool)


def citations(answer, t):
    sheets = sorted({re.match(r"^\[.+?\](.+)!", r)[1] for r in t.refs}, key=len, reverse=True)
    if not sheets:
        return []
    alt = "|".join(re.escape(s) for s in sheets)
    rx = re.compile(rf"(?:\[?([^\[\]\s(),`]+\.xlsx)\]?[,\s]*)?({alt})!([A-Z]{{1,3}}\d{{1,5}})((?:`?,\s*`?[A-Z]{{1,3}}\d{{1,5}}\b(?!!))*)")
    out = []
    for g, m in enumerate(rx.finditer(answer)):
        out.append({"file": m[1], "sheet": m[2], "cell": m[3], "start": m.start(), "end": m.end(), "group": g})
        for extra in re.findall(r"[A-Z]{1,3}\d{1,5}", m[4] or ""):
            out.append({"file": m[1], "sheet": m[2], "cell": extra, "start": m.start(), "end": m.end(), "group": g})
    return out


def refs_for(c, t):
    return [r for r in t.refs if r.endswith(f"]{c['sheet']}!{c['cell']}") and (not c["file"] or r.startswith(f"[{c['file']}]"))]


def check(q, path=None):
    path = path or ROOT / "control/transcripts" / f"{q['id']}.jsonl"
    folder = q.get("folder", "samples/approved-exports")
    calls, answer, model = parse(path)
    t = Traced()
    for c in calls:
        if not c["result"]:
            continue
        try:
            body = json.loads(c["result"])
        except json.JSONDecodeError:
            walk_tool_result(c["result"], folder, t, True)
            continue
        walk_tool_result(body, folder, t, c["error"])
        if c["name"] == "margin_bridge" and not c["error"]:
            check_bridge(body, t)
    cites = citations(answer, t)
    blanked = list(answer)
    for c in cites:
        blanked[c["start"]:c["end"]] = " " * (c["end"] - c["start"])
    blanked = "".join(blanked)
    figures = numbers(blanked)
    untraced = [n["token"] for n in figures if not is_traced(n, t)]
    quoted = [n["token"] for n in figures if any(abs(v - n["value"]) <= tolerance(n) for v in t.quoted)]
    invented = sorted({f"{c['sheet']}!{c['cell']}" for c in cites if not refs_for(c, t)})
    mismatched, prev_end = [], 0
    for g in sorted({c["group"] for c in cites}):
        members = [c for c in cites if c["group"] == g]
        before = [n for n in numbers(blanked[prev_end:members[0]["start"]])]
        prev_end = members[0]["end"]
        if not before:
            continue
        last = before[-1]
        vals = []
        for c in members:
            for r in refs_for(c, t):
                v, pct = cell_at(folder, r)
                if isinstance(v, (int, float)):
                    vals.append(abs(v * 100) if pct else abs(v))
                vals += t.derived.get(r, [])
        if vals and not any(abs(v - last["value"]) <= tolerance(last) + 1e-9 for v in vals):
            mismatched.append(f"{last['token']} cited to {', '.join(c['sheet'] + '!' + c['cell'] for c in members)}")
    return {
        "id": q["id"], "question": q["question"], "class": q.get("class"), "scene": q.get("scene"), "expect": q["expect"],
        "folder": folder, "model": model,
        "tool_calls": [{"tool": c["name"], "input": c["input"], "error": c["error"]} for c in calls],
        "answer": answer,
        "figures_in_answer": len(figures), "untraced_figures": untraced, "figures_quoted_from_report_comments": quoted,
        "citations": len(cites), "citations_to_cells_no_tool_returned": invented,
        "figures_different_from_cited_cell": mismatched,
        "tool_results_checked": {"cells_reread": len(t.refs), "changes_and_ratios_recomputed": t.recomputed, "bridge_values_compared": t.bridge_checked, "problems": t.problems},
        "behavior": GRADES.get(q["id"]),
    }


def main():
    results = [check(q) for q in QUESTIONS if (ROOT / "control/transcripts" / f"{q['id']}.jsonl").exists()]
    data_hash = hashlib.sha256(b"".join((ROOT / "samples/approved-exports" / f).read_bytes() for f in sorted(p.name for p in (ROOT / "samples/approved-exports").iterdir()))).hexdigest()[:12]
    (ROOT / "control/results.json").write_text(json.dumps({"dataset_sha256": data_hash, "results": results}, indent=2) + "\n")
    figs = sum(r["figures_in_answer"] for r in results)
    untr = sum(len(r["untraced_figures"]) for r in results)
    inv = sum(len(r["citations_to_cells_no_tool_returned"]) for r in results)
    mis = sum(len(r["figures_different_from_cited_cell"]) for r in results)
    prob = sum(len(r["tool_results_checked"]["problems"]) for r in results)
    graded = [r for r in results if r["behavior"]]
    good = sum(1 for r in graded if r["behavior"]["behavior"] == "as expected")
    lines = [
        "# Control results", "",
        f"Recorded conversations: {len(results)} of {len(QUESTIONS)}. Client: Claude Code in print mode, model {', '.join(sorted({r['model'] for r in results}))}. Sample data SHA-256 (first 12): {data_hash}.",
        f"Tool results: {sum(r['tool_results_checked']['cells_reread'] for r in results)} cell references re-read with openpyxl, {sum(r['tool_results_checked']['changes_and_ratios_recomputed'] for r in results)} changes and ratios recomputed, {sum(r['tool_results_checked']['bridge_values_compared'] for r in results)} bridge values compared with tools/expected.py; problems: {prob}.",
        f"Figures written in the answers: {figs}, of which {sum(len(r['figures_quoted_from_report_comments']) for r in results)} quoted from a report comment. Untraced (matching no figure a tool returned in that conversation): {untr}.",
        f"Cell citations: {sum(r['citations'] for r in results)}. Citing a cell no tool returned: {inv}. Figure different from the cell it cites: {mis}.",
        f"Behavior, graded by a person: {good} of {len(graded)} as expected.", "",
        "| Question | Class | Expected | Behavior | Figures | Untraced | Grader's note |", "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        b = r["behavior"] or {}
        lines.append(f"| {r['id']} | {r['class']} | {r['expect']} | {b.get('behavior', 'not graded')} | {r['figures_in_answer']} | {', '.join(r['untraced_figures']) or '0'} | {b.get('why', '')} |")
    (ROOT / "control/RESULTS.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    for r in results:
        if r["untraced_figures"] or r["citations_to_cells_no_tool_returned"] or r["figures_different_from_cited_cell"] or r["tool_results_checked"]["problems"]:
            print("\n!!", r["id"], json.dumps({k: r[k] for k in ("untraced_figures", "citations_to_cells_no_tool_returned", "figures_different_from_cited_cell")}), r["tool_results_checked"]["problems"][:5])
    return 1 if (untr or inv or mis or prob) else 0


if __name__ == "__main__":
    sys.exit(main())
