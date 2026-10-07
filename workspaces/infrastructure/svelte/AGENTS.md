# Svelte – OpenCode-Projekt

## Kontext
Svelte-Entwicklung mit dem offiziellen Svelte-MCP (Remote-Endpunkt). Der MCP
validiert Svelte-Code direkt im Workspace — es wird nichts lokal installiert.

## MCP-Server
- **svelte** (remote) – offizieller Svelte-MCP
  - URL: `https://mcp.svelte.dev/mcp`
  - Kein API-Key, keine lokale Installation
  - Tools: `list-sections`, `get-documentation`, `svelte-autofixer`, `playground-link`
- **ssh-mcp** (lokal, stdio) – aus der globalen Config (`~/.config/opencode/opencode.json`)
  - Host-Zugriff auf die Infrastruktur (Docker, Proxmox, TrueNAS, …)
- **ocis** (lokal) – privater Obsidian-Vault
  - Env: `OCIS_PRIVATE_USER` / `OCIS_PRIVATE_TOKEN` (aus OpenBao)

## Richtlinien
- Svelte-Projekte liegen auf dem Docker Host (`/opt/docker-compose/<name>/`)
- Source-Dateien (`.svelte`, `.js`, `.css`) via `ssh-mcp` bearbeiten
- Nach `.svelte`-Edits immer den Svelte-MCP für Validierung nutzen
- Keine Secrets, Tokens oder private Keys im Repo

## Skills
`create-skill`, `obsidian`
