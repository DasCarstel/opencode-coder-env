# Cleve – OpenCode-Projekt

## Kontext
Verwaltung der Cleve-Infrastruktur (Proxmox, TrueNAS, Docker) über den
Community-SSH-MCP (`ssh-mcp`) durch einen WireGuard-VPN-Tunnel.

## Netzwerk-Verbindung
- **WireGuard VPN**: Der Workspace verbindet sich über einen WireGuard-Tunnel
  mit dem Cleve-Netzwerk (192.168.178.0/24)
- **WireGuard Server**: Docker-Container auf Docker-Kleve (192.168.178.52)
- **Subnetz**: 10.20.0.0/24 (WireGuard), 192.168.178.0/24 (Cleve LAN)
- **Client-IP**: 10.20.0.2, **Server-IP**: 10.20.0.1

## MCP-Server
- **ssh-mcp** (lokal, stdio) – im Workspace-Container via `npm install -g ssh-mcp`
  - Konfiguration: `~/.config/ssh-mcp/config.toml` (von `startup.sh` erzeugt)
  - Auth: statischer ed25519-Key aus OpenBao (`secret/data/mcp/ssh-mcp`) → `/root/.ssh/id_ed25519_mcp`
  - Policy: `role=admin`, `group=prod`, `approvalPolicy=auto`
  - SFTP: `transferRoot=/root/.ssh-mcp-transfers`
- **ocis** (lokal) – privater Obsidian-Vault
  - Env: `OCIS_PRIVATE_USER` / `OCIS_PRIVATE_TOKEN` (aus OpenBao)

## Cleve Host-Profile (`~/.config/ssh-mcp/config.toml`)

| Profile | Host | Zweck |
|---------|------|-------|
| `cleve-proxmox` | 192.168.178.50 | Proxmox VE (Cleve) |
| `cleve-truenas` | 192.168.178.51 | TrueNAS (Cleve) |
| `cleve-docker` | 192.168.178.52 | Docker Host (Cleve) |

## Richtlinien
- **NIEMALS raw SSH via bash** – ausschließlich `ssh-mcp`-Tools
- Befehle: `run-command({profile:"cleve-...", command:"..."})`
- Dateien: `sftp-list`, `sftp-download`, `sftp-upload`
- Keine Secrets, Tokens oder private Keys im Repo

## Skills
`authentik`, `authentik-integration`, `authentik-troubleshooting`,
`docker-host`, `docker-host-compose`, `docker-host-filesystem`, `docker-host-git`,
`create-skill`

## Referenzen
- ssh-mcp: https://github.com/tufantunc/ssh-mcp
- WireGuard: https://www.wireguard.com/
