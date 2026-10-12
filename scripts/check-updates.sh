#!/bin/bash
# check-updates.sh – zeigt an, welche Versions-Pins in versions.env veraltet sind.
#
# Aufruf:  bash scripts/check-updates.sh        (auf der Coder-LXC oder im Workspace-Container)
# Benötigt: npm (für OpenCode/ssh-mcp), curl (für GitHub/herdr.dev), Netz.
#
# WICHTIG: nur Anzeige. Versionen werden von Hand in coder-templates/versions.env
# angepasst; beim nächsten Workspace-Start ziehen alle Container nach.
# Strategie: OpenCode = immer latest (kein Pin) – Umschalten des Modells wäre versions.env.

set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$REPO_DIR/coder-templates/versions.env" 2>/dev/null

row() { printf "%-16s %-10s %-10s %s\n" "$1" "$2" "$3" "$4"; }

echo "Update-Check (Pin vs Latest – nur Anzeige), $(date '+%Y-%m-%d %H:%M')"
row "Tool" "Pin" "Latest" "Aktion"
echo "--------------------------------------------------------------------"

# OpenCode – Hybrid: kein Pin, startup.sh gleicht bei jedem Start ab
OC_LATEST=$(npm view @opencode/cli version 2>/dev/null | tail -1)
row "OpenCode" "(immer latest)" "${OC_LATEST:-?}" "startup.sh zieht automatisch nach"

# ssh-mcp
S_LATEST=$(npm view ssh-mcp version 2>/dev/null | tail -1)
if [ -n "${SSH_MCP_VERSION:-}" ] && [ -n "$S_LATEST" ] && [ "$S_LATEST" != "${SSH_MCP_VERSION}" ]; then
  row "ssh-mcp" "${SSH_MCP_VERSION}" "$S_LATEST" "versions.env: SSH_MCP_VERSION=$S_LATEST"
else
  row "ssh-mcp" "${SSH_MCP_VERSION:-?}" "${S_LATEST:-?}" "aktuell"
fi

# ocis-mcp-server (GitHub-Release)
OCIS_LATEST=$(curl -s --max-time 15 https://api.github.com/repos/owncloud/ocis-mcp-server/releases/latest \
  | grep -o '"tag_name": *"[^"]*"' | head -1 | sed 's/.*"v//;s/"//')
if [ -n "${OCIS_MCP_VERSION:-}" ] && [ -n "$OCIS_LATEST" ] && [ "$OCIS_LATEST" != "${OCIS_MCP_VERSION}" ]; then
  row "ocis-mcp" "${OCIS_MCP_VERSION}" "$OCIS_LATEST" "versions.env: OCIS_MCP_VERSION=$OCIS_LATEST"
else
  row "ocis-mcp" "${OCIS_MCP_VERSION:-?}" "${OCIS_LATEST:-?}" "aktuell"
fi

# herdr (GitHub-Repo: herdrdev/herdr; Binary via herdr.dev-API)
HERDR_LATEST=$(curl -s --max-time 15 https://api.github.com/repos/herdrdev/herdr/releases/latest \
  | grep -o '"tag_name": *"[^"]*"' | head -1 | sed 's/.*"v//;s/"//')
if [ -n "${HERDR_VERSION:-}" ] && [ -n "$HERDR_LATEST" ] && [ "$HERDR_LATEST" != "${HERDR_VERSION}" ]; then
  row "herdr" "${HERDR_VERSION}" "$HERDR_LATEST" "versions.env: HERDR_VERSION=$HERDR_LATEST"
else
  row "herdr" "${HERDR_VERSION:-?}" "${HERDR_LATEST:-?}" "aktuell"
fi

echo "--------------------------------------------------------------------"
echo "Aenderung dann: 'versions.env' bearbeiten, commit + push, Workspace neu starten."
