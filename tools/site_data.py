# /// script
# requires-python = ">=3.11"
# dependencies = ["openpyxl==3.1.5"]
# ///
"""Builds site/src/data.json for the demonstration page from the committed workbooks, the recorded
transcripts and control/results.json. Nothing on the page is typed by hand: the answers are the
recorded ones, the statements are the sample exports cell for cell.

    uv run tools/site_data.py
"""

import html
import json
import re
from pathlib import Path

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "samples" / "approved-exports"
OUT = ROOT / "site" / "src" / "data.json"
REPO = "https://github.com/fred1433/statements-mcp/blob/main/"
SCENES = [("q2-margin", "A useful answer"), ("quarter-so-far", "A stop"), ("supplier-prices", "A limit")]


def sheets(file):
    wb = load_workbook(DATA / file)
    out = []
    for ws in wb.worksheets:
        rows, comments, header_row, columns = [], [], None, []
        in_comments = False
        for r in range(1, ws.max_row + 1):
            label = ws.cell(row=r, column=1).value
            if header_row is None and r > 4 and ws.cell(row=r, column=2).value and isinstance(ws.cell(row=r, column=2).value, str):
                header_row = r
                columns = [ws.cell(row=r, column=c).value for c in range(2, ws.max_column + 1)]
                continue
            if header_row is None or label is None:
                continue
            if label == "Report comments":
                in_comments = True
                continue
            if in_comments:
                comments.append({"text": label, "addr": f"A{r}"})
                continue
            cells = []
            kind = "section"
            for c in range(2, 2 + len(columns)):
                cell = ws.cell(row=r, column=c)
                v = cell.value
                pct = "%" in (cell.number_format or "")
                if v is None:
                    cells.append({"addr": cell.coordinate, "display": ""})
                    continue
                kind = "percent" if pct else "amount"
                disp = f"{v * 100:.1f}%" if pct else (f"({abs(v):,})" if v < 0 else f"{v:,}")
                cells.append({"addr": cell.coordinate, "display": disp})
            bold = bool(ws.cell(row=r, column=1).font and ws.cell(row=r, column=1).font.b)
            rows.append({"label": label, "kind": kind, "bold": bold, "cells": cells, "row": r})
        out.append({
            "name": ws.title,
            "header": [ws.cell(row=i, column=1).value for i in range(1, 6)],
            "columns": columns, "rows": rows, "comments": comments,
        })
    return out


def transcript(qid):
    calls, answer = {}, ""
    for line in (ROOT / "control/transcripts" / f"{qid}.jsonl").read_text().splitlines():
        e = json.loads(line)
        if e["type"] == "assistant":
            for c in e["message"]["content"]:
                if c["type"] == "tool_use":
                    calls[c["id"]] = {"tool": c["name"].split("__")[-1], "input": c["input"], "result": None, "error": False}
        if e["type"] == "user":
            for c in e["message"]["content"]:
                if c["type"] == "tool_result" and c["tool_use_id"] in calls:
                    content = c["content"]
                    text = content if isinstance(content, str) else "".join(x.get("text", "") for x in content or [])
                    calls[c["tool_use_id"]].update(result=text, error=c.get("is_error", False))
        if e["type"] == "result":
            answer = e["result"]
    return list(calls.values()), answer


def returned_refs(calls):
    refs = {}
    def walk(o):
        if isinstance(o, dict):
            if isinstance(o.get("ref"), str):
                refs[o["ref"]] = o.get("display")
            for r in o.get("inputs", []) if isinstance(o.get("inputs"), list) else []:
                refs.setdefault(r, None)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    for c in calls:
        try:
            walk(json.loads(c["result"]))
        except (TypeError, json.JSONDecodeError):
            pass
    return refs


SHEET_CELL = re.compile(r"(?:\[?(IS_\d{4}-\d{2})(?:\.xlsx)?\]?[,\s]*)?(?:(Total|Wholesale|Online|Service)!)?([A-Z]{1,2}\d{1,3})\b")


def mark_citations(answer, refs):
    """Replaces each citation (a parenthetical of cell references, or a bare reference) with a token
    ⟦ref1|ref2⟧ naming full refs, resolved against the refs the tools returned in that conversation."""

    def resolve(file_stem, sheet, cell, before):
        cands = [r for r in refs if r.endswith(f"]{sheet}!{cell}") and (not file_stem or r.startswith(f"[{file_stem}.xlsx]"))]
        if len(cands) > 1:
            m = re.findall(r"(\d{1,2}\.\d%|\$[\d,]+)", before[-80:])
            narrowed = [r for r in cands if m and refs[r] == m[-1]]
            if narrowed:
                cands = narrowed
        return cands

    def refs_in(text, before):
        found, sheet, stem = [], None, None
        for m in SHEET_CELL.finditer(text):
            stem = m[1] or stem
            sheet = m[2] or sheet
            if not sheet or not re.search(r"!", text):
                continue
            found += resolve(stem, sheet, m[3], before)
        return list(dict.fromkeys(found))

    def paren(m):
        inner = m[1]
        if "!" not in inner:
            return m[0]
        found = refs_in(inner, answer[:m.start()])
        return (" ⟦" + "|".join(found) + "⟧") if found else m[0]

    text = re.sub(r"\s*\(([^()]*![A-Z]{1,2}\d[^()]*)\)", paren, answer)

    def bare(m):
        found = refs_in(m[0], text[:m.start()])
        return (m[0] + " ⟦" + "|".join(found) + "⟧") if found else m[0]

    text = re.sub(r"`?\[?(?:IS_\d{4}-\d{2}(?:\.xlsx)?\]?,?\s*)?(?:Total|Wholesale|Online|Service)![A-Z]{1,2}\d{1,3}`?(?![^⟦]*⟧)", bare, text)
    return text


def figure_index(calls):
    """display string -> list of (formula, refs) from every tool result of the conversation."""
    idx = {}
    def add(disp, formula, refs):
        if isinstance(disp, str) and refs:
            idx.setdefault(disp, []).append((formula, list(refs)))
    def all_refs(o):
        found = []
        def rec(x):
            if isinstance(x, dict):
                for r in x.get("inputs", []) if isinstance(x.get("inputs"), list) else []:
                    found.append(r)
                for v in x.values():
                    rec(v)
            elif isinstance(x, list):
                for v in x:
                    rec(v)
        rec(o)
        return list(dict.fromkeys(found))

    def walk(o, scope=None):
        if isinstance(o, dict):
            if "units" in o and "mix_effect_total" in o:
                scope = all_refs(o)  # bridge totals: their inputs are every unit's inputs
            if "formula" in o and isinstance(o.get("inputs"), list):
                add(o.get("display"), o["formula"].split(";")[0], o["inputs"] or scope or [])
                if isinstance(o.get("relative"), str):
                    add(o["relative"], "(to - from) / |from|", o["inputs"])
                m = re.search(r"([-+]?\d+\.\d+ pts)", o.get("note") or "")
                if m:
                    add(m[1], "to - from, in percentage points", o["inputs"])
            elif isinstance(o.get("ref"), str) and "display" in o:
                add(o["display"], "printed value", [o["ref"]])
            for v in o.values():
                walk(v, scope)
        elif isinstance(o, list):
            for v in o:
                walk(v, scope)
    for c in calls:
        try:
            walk(json.loads(c["result"]))
        except (TypeError, json.JSONDecodeError):
            pass
    return idx


FIG = re.compile(r"(?<![\w.$,])([-+]?\$\d{1,3}(?:,\d{3})*(?:\.\d+)?|[-+]?\d+(?:\.\d+)?(?: pts|%))(?![\w])")


def link_figures(text, idx):
    """Wraps every figure not already followed by a cell citation in ⟪figure¦formula¦refs⟫, found by its exact
    display string in the tool results. Returns the text and the figures that could not be linked."""
    out, unlinked, pos = [], [], 0
    for m in FIG.finditer(text):
        if "⟦" in text[pos:m.start()] and "⟧" not in text[text.rfind("⟦", 0, m.start()):m.start()]:
            continue  # inside a token
        after = text[m.end():m.end() + 3]
        if after.startswith(" ⟦"):
            continue  # already cited to its cell
        fig = m[1]
        cands = idx.get(fig) or idx.get("+" + fig) or idx.get("-" + fig) or (idx.get(fig[1:]) if fig[0] in "+-" else None)
        if not cands:
            unlinked.append(fig)
            continue
        refs = list(dict.fromkeys(r for _, rs in cands for r in rs))
        out.append(text[pos:m.start()])
        out.append(f"⟪{fig}¦{cands[0][0]}¦{'|'.join(refs)}⟫")
        pos = m.end()
    out.append(text[pos:])
    return "".join(out), unlinked


def transcript_html(q, calls, answer):
    e = lambda x: html.escape(str(x))
    parts = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>"
             f"<meta name='robots' content='noindex, nofollow'><title>Transcript</title><link rel='icon' href='../favicon.svg' type='image/svg+xml'>"
             f"<link rel='stylesheet' href='../transcript.css'></head><body><main>"
             f"<p class='back'><a href='../'>Back to the demonstration</a></p>"
             f"<h1>“{e(q['question'])}”</h1>"
             f"<p class='meta'>Recorded September 30, 2026 with Claude Code as the MCP client, model {e(q.get('model',''))}. Every tool call and tool result, then Claude's answer, unedited. <a href='https://github.com/fred1433/statements-mcp/blob/main/control/transcripts/{q['id']}.jsonl'>Raw file</a>.</p>"]
    for i, c in enumerate(calls, 1):
        try:
            body = json.dumps(json.loads(c["result"]), indent=2, ensure_ascii=False)
        except (TypeError, json.JSONDecodeError):
            body = c["result"] or ""
        parts.append(f"<section class='call{' error' if c['error'] else ''}'><h2>{i}. {e(c['tool'])}{' (refused)' if c['error'] else ''}</h2>"
                     f"<p class='args'>{e(json.dumps(c['input'], ensure_ascii=False))}</p>"
                     f"<details><summary>Tool result</summary><pre>{e(body)}</pre></details></section>")
    parts.append(f"<section class='final'><h2>Claude's answer</h2><pre class='answer'>{e(answer)}</pre></section></main></body></html>")
    return "".join(parts)


def main():
    results = json.loads((ROOT / "control/results.json").read_text())
    scenes = []
    for qid, label in SCENES:
        calls, answer = transcript(qid)
        refs = returned_refs(calls)
        q = next(r for r in results["results"] if r["id"] == qid)
        blocked = []
        for c in calls:
            if c["tool"] == "list_snapshots" and c["result"]:
                for s in json.loads(c["result"])["snapshots"]:
                    if s["status"] == "blocked":
                        blocked.append({"file": s["file"], "failed_checks": s["failed_checks"]})
        marked = mark_citations(answer, refs)
        linked, unlinked = link_figures(marked, figure_index(calls))
        print(f"{qid}: figures without a cell or formula link: {unlinked}")
        scenes.append({
            "id": qid, "label": label, "question": q["question"], "answer": linked, "unlinked": unlinked,
            "tools": [c["tool"] for c in calls], "blocked": blocked,
            "transcript": f"transcripts/{qid}.html",
        })
    rs = results["results"]
    summary = {
        "conversations": len(rs),
        "figures": sum(r["figures_in_answer"] for r in rs),
        "quoted": sum(len(r["figures_quoted_from_report_comments"]) for r in rs),
        "untraced": sum(len(r["untraced_figures"]) for r in rs),
        "citations": sum(r["citations"] for r in rs),
        "bad_citations": sum(len(r["citations_to_cells_no_tool_returned"]) + len(r["figures_different_from_cited_cell"]) for r in rs),
        "cells_reread": sum(r["tool_results_checked"]["cells_reread"] for r in rs),
        "as_expected": sum(1 for r in rs if (r["behavior"] or {}).get("behavior") == "as expected"),
        "model": sorted({r["model"] for r in rs}),
        "dataset": results["dataset_sha256"],
    }
    ledger = [{"id": r["id"], "question": r["question"], "expect": r["expect"], "class": r["class"],
               "behavior": (r["behavior"] or {}).get("behavior"), "why": (r["behavior"] or {}).get("why"),
               "figures": r["figures_in_answer"], "untraced": len(r["untraced_figures"]),
               "transcript": f"transcripts/{r['id']}.html"} for r in rs]
    tdir = OUT.parent / "transcripts"
    tdir.mkdir(exist_ok=True)
    for r in rs:
        calls, answer = transcript(r["id"])
        (tdir / f"{r['id']}.html").write_text(transcript_html(r, calls, answer))
    data = {
        "scenes": scenes,
        "workbooks": {f: sheets(f) for f in ["IS_2026-03.xlsx", "IS_2026-06.xlsx", "IS_2026-08.xlsx"]},
        "summary": summary, "ledger": ledger,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n")



if __name__ == "__main__":
    main()
