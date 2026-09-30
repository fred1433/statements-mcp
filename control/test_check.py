# /// script
# requires-python = ">=3.11"
# dependencies = ["openpyxl==3.1.5"]
# ///
"""The answer checker must catch what it claims to catch. Forged transcripts, one fault each.

    uv run control/test_check.py
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check  # noqa: E402

TOOL = {
    "values": [
        {"row": "Net sales", "column": "Quarter to Date Actual", "unit": "Alder & Finch Supply Co.", "value": 6748503, "display": "$6,748,503", "ref": "[IS_2026-06.xlsx]Total!C13"},
        {"row": "Net sales", "column": "Year to Date Actual", "unit": "Alder & Finch Supply Co.", "value": 12769721, "display": "$12,769,721", "ref": "[IS_2026-06.xlsx]Total!D13"},
    ]
}


def run(answer, tool=TOOL, error=False):
    lines = [
        {"type": "system", "model": "test"},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "mcp__fs__get_values", "input": {}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "is_error": error, "content": [{"type": "text", "text": json.dumps(tool)}]}]}},
        {"type": "result", "result": answer},
    ]
    path = Path(tempfile.mkdtemp()) / "x.jsonl"
    path.write_text("\n".join(json.dumps(x) for x in lines))
    return check.check({"id": "x", "question": "", "expect": "answer"}, path)


def test_faithful():
    r = run("Q2 net sales were $6,748,503 ([IS_2026-06.xlsx]Total!C13) and first-half sales $12,769,721 (Total!D13), about $12.8 million.")
    assert r["untraced_figures"] == [] and r["figures_different_from_cited_cell"] == [] and r["citations_to_cells_no_tool_returned"] == [], r


def test_invented_figure():
    r = run("Q2 net sales were $6,748,503 (Total!C13), up about $700,000 on Q1.")
    assert r["untraced_figures"] == ["$700,000"], r


def test_figure_attached_to_the_wrong_cell():
    r = run("Q2 net sales were $12,769,721 (Total!C13).")
    assert r["untraced_figures"] == [] and len(r["figures_different_from_cited_cell"]) == 1, r


def test_citation_to_a_cell_never_returned():
    r = run("Q2 net sales were $6,748,503 (Total!C14).")
    assert r["citations_to_cells_no_tool_returned"] == ["Total!C14"], r


def test_tool_value_that_differs_from_its_cell():
    bad = {"values": [dict(TOOL["values"][0], value=6748000, display="$6,748,000")]}
    r = run("Q2 net sales were $6,748,000 (Total!C13).", tool=bad)
    assert r["tool_results_checked"]["problems"], r


def test_percent_is_not_points():
    tool = {"change": {"display": "-0.46 pts", "value": -0.0046, "formula": "to - from, in percentage points", "inputs": ["[IS_2026-03.xlsx]Total!C21", "[IS_2026-06.xlsx]Total!C21"]}}
    ok = run("The margin fell 0.46 pts.", tool=tool)
    bad = run("The margin fell 0.46%.", tool=tool)
    assert ok["untraced_figures"] == [] and bad["untraced_figures"] == ["0.46%"], (ok, bad)


if __name__ == "__main__":
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn(); print("ok  ", name)
            except AssertionError as e:
                fails += 1; print("FAIL", name, str(e)[:400])
    sys.exit(1 if fails else 0)
