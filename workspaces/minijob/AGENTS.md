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
Aus `../../skills/shared` (`create-skill`, `docker-host*`) und
`../../skills/minijob` (`obsidian`, Arbeits-Vault).
