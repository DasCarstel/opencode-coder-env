# Svelte – OpenCode-Projekt

## Kontext
Svelte-Entwicklung mit dem offiziellen Svelte-MCP (Remote-Endpunkt). Der MCP
validiert Svelte-Code direkt im Workspace — es wird nichts lokal installiert.

## MCP-Server
- **svelte** (remote) – offizieller Svelte-MCP
  - URL: `https://mcp.svelte.dev/mcp`
  - Kein API-Key, keine lokale Installation
  - Tools: `list-sections`, `get-documentation`, `svelte-autofixer`, `playground-link`
- **ssh-mcp** (lokal, stdio) – aus der Projekt-Config dieses Workspaces (`opencode.json`)
  - Host-Zugriff auf die Infrastruktur (Docker, Proxmox, TrueNAS, …)
  - Config: `/root/.config/ssh-mcp/config-full.toml` (alle Krefeld-Hosts, Gruppe prod) —
    identisch zum Server-Management-Space
- **authentik** (lokal) – Authentik-Verwaltung
  - Env: `AUTHENTIK_API_KEY` (aus OpenBao, service-weit injiziert)
  - URL: `https://auth.mueller-nas.de`
- **ocis** (lokal) – privater Obsidian-Vault
  - Env: `OCIS_PRIVATE_USER` / `OCIS_PRIVATE_TOKEN` (aus OpenBao)

## Richtlinien
- Svelte-Projekte liegen auf dem Docker Host (`/opt/docker-compose/<name>/`)
- Source-Dateien (`.svelte`, `.js`, `.css`) via `ssh-mcp` bearbeiten
- Nach `.svelte`-Edits immer den Svelte-MCP für Validierung nutzen
- Keine Secrets, Tokens oder private Keys im Repo

## Rückfragen an den Nutzer

- **Fragen an den Nutzer werden immer über das native OpenCode-Tool `question`
  gestellt** — mit klickbaren Auswahlmöglichkeiten, **nie** als Freitext im Chat.
- Gilt für jede Rückfrage (auch Ja/Nein- und Zwischenfragen), nicht erst am Ende
  einer Antwort.
- `question` ist ein **OpenCode-Built-in-Tool, kein MCP-Server** (der Name
  „Fragen-MCP" ist irreführend).
- Jede Option mit kurzer Beschreibung angeben; OpenCode ergänzt automatisch die
  Möglichkeit einer eigenen Antwort.

## Skills
`create-skill`, `obsidian`,
`docker-host-location` (Krefeld → `docker-host`),
`docker-host`, `docker-host-compose`, `docker-host-filesystem`, `docker-host-git`
