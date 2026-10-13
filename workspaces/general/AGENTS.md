# General – OpenCode-Projekt

## Kontext
Allgemeiner Workspace ohne Site-Bezug (Netz `coder-gen`). Bewusst isoliert:
kein `ssh-mcp`, kein SSH-Key, kein Zugriff auf das interne VLAN. Nur der private
Obsidian-Vault (oCIS) und lokales PDF-Lesen.

## MCP-Server
- **ocis** (lokal) – privater Obsidian-Vault
  - Env: `OCIS_PRIVATE_USER` / `OCIS_PRIVATE_TOKEN` (aus OpenBao)
- **pdf** (lokal) – `npx @sylphx/anymd@8.5.1`
  - lokales Lesen von PDFs und URLs, kein API-Key

## Richtlinien
- Kein `ssh-mcp`, keine SSH-Keys, kein internes VLAN
- Keine Secrets, Tokens oder private Keys im Repo

## Skills
Aus `../../skills/shared` (`create-skill`, `docker-host*`) und
`../../skills/obsidian` (`obsidian`). Die `docker-host*`-Skills sind hier mangels
`ssh-mcp` nicht nutzbar.
