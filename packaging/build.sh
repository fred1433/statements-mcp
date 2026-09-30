#!/bin/sh
# Builds one .mcpb per platform: a self-contained server binary (no .NET install needed) plus the
# synthetic demonstration folder. Install by opening the file with Claude Desktop.
#   sh packaging/build.sh           -> dist/financial-statements-macos-arm64.mcpb, dist/financial-statements-windows-x64.mcpb
set -eu
cd "$(dirname "$0")/.."
DOTNET="${DOTNET:-dotnet}"
mkdir -p dist
for pair in osx-arm64:darwin:macos-arm64:FinancialStatements.Mcp win-x64:win32:windows-x64:FinancialStatements.Mcp.exe; do
  rid=${pair%%:*}; rest=${pair#*:}; platform=${rest%%:*}; rest=${rest#*:}; label=${rest%%:*}; binary=${rest#*:}
  stage=$(mktemp -d)
  mkdir -p "$stage/server/approved-exports"
  "$DOTNET" publish src/FinancialStatements.Mcp -c Release -r "$rid" --self-contained true -p:PublishSingleFile=true \
    -p:IncludeNativeLibrariesForSelfExtract=true -p:EnableCompressionInSingleFile=true -p:DebugType=none -o "$stage/server" -v q -nologo
  cp samples/approved-exports/* "$stage/server/approved-exports/"
  sed -e "s/__BINARY__/$binary/g" -e "s/__PLATFORM__/$platform/g" packaging/manifest.template.json > "$stage/manifest.json"
  npx -y @anthropic-ai/mcpb@2.1.2 validate "$stage/manifest.json"
  npx -y @anthropic-ai/mcpb@2.1.2 pack "$stage" "dist/financial-statements-$label.mcpb"
done
ls -la dist
