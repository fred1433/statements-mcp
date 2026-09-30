#!/bin/sh
# Replays control/questions.json through Claude Code as the MCP client (the machine's Claude Code login,
# no API key), one fresh conversation per question, and keeps the raw transcripts, every tool call and
# tool result included, in control/transcripts/. User settings and memory files are not loaded.
#   sh control/run.sh            # all questions
#   sh control/run.sh <id> ...   # some questions
set -eu
cd "$(dirname "$0")/.."
DOTNET="${DOTNET:-dotnet}"
"$DOTNET" build src/FinancialStatements.Mcp -c Release -v q -nologo >/dev/null
DLL="$(pwd)/src/FinancialStatements.Mcp/bin/Release/net10.0/FinancialStatements.Mcp.dll"
OUT=control/transcripts
WORK=$(mktemp -d)
mkdir -p "$OUT"
MODEL="${CONTROL_MODEL:-sonnet}"
SYSTEM="You are Claude, an AI assistant. The current date is Wednesday, September 30, 2026."
TOOLS="mcp__financial-statements__list_snapshots mcp__financial-statements__get_values mcp__financial-statements__compare_periods mcp__financial-statements__margin_bridge mcp__financial-statements__get_report_comments"
IDS="${*:-$(python3 -c 'import json; [print(q["id"]) for q in json.load(open("control/questions.json"))]')}"
for id in $IDS; do
  q=$(python3 -c 'import json,sys; print(next(x["question"] for x in json.load(open("control/questions.json")) if x["id"]==sys.argv[1]))' "$id")
  folder=$(python3 -c 'import json,sys; print(next(x.get("folder","samples/approved-exports") for x in json.load(open("control/questions.json")) if x["id"]==sys.argv[1]))' "$id")
  python3 -c 'import json,sys; print(json.dumps({"mcpServers":{"financial-statements":{"command":sys.argv[1],"args":[sys.argv[2]],"env":{"STATEMENTS_DIR":sys.argv[3]}}}}))' \
    "$DOTNET" "$DLL" "$(pwd)/$folder" > "$WORK/mcp.json"
  echo "== $id: $q"
  (cd "$WORK" && claude -p "$q" --setting-sources "" --strict-mcp-config --mcp-config mcp.json --tools "" \
      --allowedTools $TOOLS --system-prompt "$SYSTEM" --model "$MODEL" \
      --output-format stream-json --verbose --no-session-persistence < /dev/null) > "$WORK/raw.jsonl"
  python3 control/sanitize.py < "$WORK/raw.jsonl" > "$OUT/$id.jsonl"
done
