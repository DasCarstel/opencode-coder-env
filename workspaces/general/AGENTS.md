# General – OpenCode-Projekt

## Kontext
Allgemeiner Workspace ohne Site-Bezug (Netz `coder-gen`). Zugriff auf **genau
einen** SSH-Host: den **Carsten-PC** (Windows, `10.0.0.20`) über `ssh-mcp` mit der
Config `config-general.toml` (nur Profil `carsten-pc`). Kein Zugriff auf das
Krefeld- oder Kleve-VLAN — deren Profile existieren hier nicht. Dazu der private
Obsidian-Vault (oCIS) und lokales PDF-Lesen.

## MCP-Server
- **ssh-mcp** (lokal, stdio) – ausschließlich der Carsten-PC
  - Config: `/root/.config/ssh-mcp/config-general.toml` (von `startup.sh` erzeugt)
  - Auth: exklusiver ed25519-Key aus OpenBao (`secret/data/mcp/ssh-mcp-pc`) → `/root/.ssh/id_ed25519_mcp_pc`
  - Profil: `carsten-pc` (`10.0.0.20`, User `carst`, `role=admin`, `approvalPolicy=auto`)
  - Windows-Hinweise: kein `sudo` (`privileged-command` unbrauchbar), Shell ist `cmd.exe`
    bzw. PowerShell — POSIX-Helfer (`ls`/`cat`/`grep`) sind unzuverlässig.
- **ocis** (lokal) – privater Obsidian-Vault
  - Env: `OCIS_PRIVATE_USER` / `OCIS_PRIVATE_TOKEN` (aus OpenBao)
- **pdf** (lokal) – `npx @sylphx/anymd@8.5.1`
  - lokales Lesen von PDFs und URLs, kein API-Key

## OpenBao
Eigene, minimal berechtigte AppRole `mcp-general` (Policy `mcp-server-general`) —
liest nur `secret/data/mcp/{github,opencloud-ocis,opencode-go,hermes-opencode-service,ssh-mcp-pc}`.
Nicht die gemeinsame `mcp-server`.

## Richtlinien
- ssh-mcp nur mit Profil `carsten-pc`; andere Hosts gibt es hier nicht
- Kein internes VLAN (Krefeld/Kleve) — kein Zugriff
- Keine Secrets, Tokens oder private Keys im Repo

## Skills
Aus `../../skills/shared` (`create-skill`, `docker-host*`), `../../skills/general`
(`carsten-pc`) und `../../skills/obsidian` (`obsidian`). Die `docker-host*`-Skills
bleiben hier ohne Ziel (der Carsten-PC ist kein Docker-Host).
