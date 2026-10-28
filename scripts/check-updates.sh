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

# mcp-grafana (GitHub-Repo: grafana/mcp-grafana)
GRAFANA_LATEST=$(curl -s --max-time 15 https://api.github.com/repos/grafana/mcp-grafana/releases/latest \
  | grep -o '"tag_name": *"[^"]*"' | head -1 | sed 's/.*"v//;s/"//')
if [ -n "${GRAFANA_MCP_VERSION:-}" ] && [ -n "$GRAFANA_LATEST" ] && [ "$GRAFANA_LATEST" != "${GRAFANA_MCP_VERSION}" ]; then
  row "mcp-grafana" "${GRAFANA_MCP_VERSION}" "$GRAFANA_LATEST" "versions.env: GRAFANA_MCP_VERSION=$GRAFANA_LATEST"
else
  row "mcp-grafana" "${GRAFANA_MCP_VERSION:-?}" "${GRAFANA_LATEST:-?}" "aktuell"
fi

# herdr (GitHub-Repo: herdrdev/herdr; Binary via herdr.dev-API)
HERDR_LATEST=$(curl -s --max-time 15 https://api.github.com/repos/herdrdev/herdr/releases/latest \
  | grep -o '"tag_name": *"[^"]*"' | head -1 | sed 's/.*"v//;s/"//')
if [ -n "${HERDR_VERSION:-}" ] && [ -n "$HERDR_LATEST" ] && [ "$HERDR_LATEST" != "${HERDR_VERSION}" ]; then
  row "herdr" "${HERDR_VERSION}" "$HERDR_LATEST" "versions.env: HERDR_VERSION=$HERDR_LATEST"
else
  row "herdr" "${HERDR_VERSION:-?}" "${HERDR_LATEST:-?}" "aktuell"
fi

# DCP (npm: @tarquinen/opencode-dcp)
DCP_LATEST=$(npm view @tarquinen/opencode-dcp version 2>/dev/null | tail -1)
if [ -n "${DCP_VERSION:-}" ] && [ -n "$DCP_LATEST" ] && [ "$DCP_LATEST" != "${DCP_VERSION}" ]; then
  row "opencode-dcp" "${DCP_VERSION}" "$DCP_LATEST" "versions.env: DCP_VERSION=$DCP_LATEST"
else
  row "opencode-dcp" "${DCP_VERSION:-?}" "${DCP_LATEST:-?}" "aktuell"
fi

# ── Bake-Abgleich (Dockerfile-ARGs vs. versions.env) ───────────────────────
# Die Dockerfiles backen die Toolchain über ARG-Defaults ins Image. Driftet so
# ein Default gegen versions.env, läuft der Container zwar weiter (startup.sh
# korrigiert zur Laufzeit nach), aber der Kaltstart verliert seinen Vorteil.
BAKE_OK=1
for df in "$REPO_DIR"/coder-templates/*/Dockerfile; do
  [ -f "$df" ] || continue
  tmpl="$(basename "$(dirname "$df")")"
  for key in NODE_MAJOR OPENCODE_VERSION DCP_VERSION SSH_MCP_VERSION OCIS_MCP_VERSION GRAFANA_MCP_VERSION; do
    arg=$(grep -E "^ARG ${key}=" "$df" | head -1 | cut -d= -f2-)
    [ -z "$arg" ] && continue     # z. B. SSH_MCP_VERSION in muellerconnect/datascience
    env_val="${!key:-}"
    [ -z "$env_val" ] && continue # kein Pin in versions.env (z. B. NODE_MAJOR)
    if [ "$arg" != "$env_val" ]; then
      echo "  ⚠ Bake-Drift [$tmpl]: ARG ${key}=${arg} ≠ versions.env ${key}=${env_val}"
      BAKE_OK=0
    fi
  done
done
if [ "$BAKE_OK" = "1" ]; then
  echo "Bake-Abgleich: ok – Bake-ARGs und Versions-Pins stimmen überein."
else
  echo "Bake-Abgleich: Drift gefunden – Dockerfile-ARGs an versions.env angleichen (nur Anzeige)."
fi

echo "--------------------------------------------------------------------"
echo "Aenderung dann: 'versions.env' bearbeiten, commit + push, Workspace neu starten."
