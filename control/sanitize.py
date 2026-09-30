"""Keeps what a reader needs from a raw Claude Code stream-json transcript (model, tool calls, tool
results, the final answer) and drops local details (paths, session ids, installed skills, thinking signatures).

    python3 control/sanitize.py < raw.jsonl > clean.jsonl
"""
import json
import sys

for line in sys.stdin:
    e = json.loads(line)
    t = e.get("type")
    if t == "system" and e.get("subtype") == "init":
        out = {k: e.get(k) for k in ("type", "subtype", "model", "claude_code_version", "tools")}
        out["mcp_servers"] = [{"name": s.get("name"), "status": s.get("status")} for s in e.get("mcp_servers", [])]
    elif t in ("assistant", "user"):
        content = e.get("message", {}).get("content")
        if not isinstance(content, list):
            continue
        keep = []
        for c in content:
            if c.get("type") == "text":
                keep.append({"type": "text", "text": c["text"]})
            elif c.get("type") == "tool_use":
                keep.append({"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["input"]})
            elif c.get("type") == "tool_result":
                keep.append({"type": "tool_result", "tool_use_id": c["tool_use_id"], "is_error": c.get("is_error", False), "content": c.get("content")})
        if not keep:
            continue
        out = {"type": t, "message": {"role": e["message"].get("role"), "content": keep}}
    elif t == "result":
        out = {k: e.get(k) for k in ("type", "subtype", "is_error", "num_turns", "duration_ms", "result")}
    else:
        continue
    print(json.dumps(out, ensure_ascii=False))
