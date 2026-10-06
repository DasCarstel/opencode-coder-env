# Server Management – OpenCode-Projekt

## Kontext
Verwaltung der Infrastruktur-Hosts über den Community-SSH-MCP (`ssh-mcp`) und
Authentik über den Community-MCP (`authentik-mcp`).

## MCP-Server
- **ssh-mcp** (lokal, stdio) – im Workspace-Container via `npm install -g ssh-mcp`
  - Konfiguration: `~/.config/ssh-mcp/config.toml` (von `startup.sh` erzeugt, nicht im Repo)
  - Auth: statischer ed25519-Key aus OpenBao (`secret/data/mcp/ssh-mcp`) → `/root/.ssh/id_ed25519_mcp`
  - Policy: `role=admin`, `group=prod`, `approvalPolicy=auto` (alle Befehlsklassen inkl. sudo)
  - SFTP: `transferRoot=/root/.ssh-mcp-transfers`
- **authentik** (lokal, `uvx`) – `nikitatsym/authentik-mcp`
  - Token: OpenBao `secret/data/mcp/authentik` → `AUTHENTIK_API_KEY`

## Host-Profile (`~/.config/ssh-mcp/config.toml`)

| Profile | Host | Zweck |
|---------|------|-------|
| `docker-host` | 10.0.10.10 | Docker Host (CT 100) |
| `proxmox` | 10.0.10.20 | Proxmox VE |
| `truenas` | 10.0.10.30 | TrueNAS (VM 200) |
| `pbs` | 10.0.10.21 | Proxmox Backup Server (CT 101) |
| `coder` | 10.0.10.17 | Coder-Host (CT 120) |
| `unifi` | 10.0.0.1 | UniFi Gateway (UCG-Ultra) |
| `media` | 10.0.10.25 | Media (CT 115) |
| `test` | 10.0.10.40 | Test (CT 105) |

## Richtlinien
- **NIEMALS raw SSH via bash** (`ssh Docker '...'`, `bash("ssh ...")`) – ausschließlich `ssh-mcp`-Tools
- Befehle: `run-command({profile:"...", command:"..."})`; lesend: `read-command(...)`; sudo: `privileged-command(...)`
- Dateien: `sftp-list`, `sftp-download`, `sftp-upload` (große Dateien: `sftp-*-file`)
- Keine Secrets, Tokens oder private Keys im Repo

## Skills
`authentik`, `authentik-integration`, `authentik-troubleshooting`,
`docker-host`, `docker-host-compose`, `docker-host-filesystem`, `docker-host-git`,
`create-skill`

## Referenzen
- ssh-mcp: https://github.com/tufantunc/ssh-mcp
- authentik-mcp: https://github.com/nikitatsym/authentik-mcp
