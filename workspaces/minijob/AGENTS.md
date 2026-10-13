# MuellerConnect (minijob) – OpenCode-Projekt

## Kontext
Mini-Job-Workspace (Netz `coder-mc`). Bewusst isoliert: kein `ssh-mcp`, kein
SSH-Key, kein internes VLAN. Zugriff ausschließlich auf den **Arbeits-Vault
`MuellerConnect`** (oCIS) und lokales PDF-Lesen.

## MCP-Server
- **ocis** (lokal) – Arbeits-Vault `MuellerConnect`
  - Env: `OCIS_MUELLERCONNECT_USER` / `OCIS_MUELLERCONNECT_TOKEN` (aus OpenBao)
- **pdf** (lokal) – `npx @sylphx/anymd@8.5.1`

## Richtlinien
- Nur der Arbeits-Vault `MuellerConnect` — der private Vault ist hier physisch
  nicht erreichbar (getrenntes Token, Hard-Guardrail)
- Kein `ssh-mcp`, keine SSH-Keys, kein internes VLAN
- Keine Secrets, Tokens oder private Keys im Repo

## Skills
Aus `../../skills/shared` (`create-skill`, `docker-host*`) und
`../../skills/minijob` (`obsidian`, Arbeits-Vault).
