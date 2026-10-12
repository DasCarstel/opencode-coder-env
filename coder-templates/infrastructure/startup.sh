#!/bin/bash
# startup.sh – Infrastructure-Profil (Thin-Wrapper)
#
# Das komplette Setup (OpenBao, Secrets, MCP, OpenCode, Herdr-Workspaces
# inkl. Svelte/Cleve) liegt im gemeinsamen Skript:
#     coder-templates/shared/startup.sh
# Aufruf: startup.sh infrastructure   (siehe entrypoint.sh)
#
# MCP-Konfigurationen pro Space liegen NICHT hier, sondern in den
# Workspace-Verzeichnissen des Repos, z. B.:
#     workspaces/infrastructure/svelte/opencode.json   (ssh-mcp, authentik, ocis, svelte-remote)
#     workspaces/infrastructure/server-management/opencode.json

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$SCRIPT_DIR/../shared/startup.sh" "${1:-infrastructure}"
