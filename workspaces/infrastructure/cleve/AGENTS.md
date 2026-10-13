# Cleve – OpenCode-Projekt

## Kontext
Verwaltung der Cleve-Infrastruktur (Proxmox, TrueNAS, Docker) über den
Community-SSH-MCP (`ssh-mcp`) durch einen Tailscale-Tunnel.

## Netzwerk-Verbindung
- **Tailscale**: Der Workspace ist ein Tailscale-Node mit Tag `tag:coder` und
  akzeptiert die Subnetz-Route nach Cleve. ACLs isolieren ihn von den privaten
  Geräten (nur `tag:coder` ↔ `tag:kleve`).
- **Subnet-Router**: Tailscale-Container auf Docker-Kleve (192.168.178.52)
  annonciert `192.168.178.0/24`.
- **Erreichbar**: Proxmox-Kleve (.50), TrueNAS-Kleve (.51), Docker-Kleve (.52)
  über die Subnetz-Route — kein Port-Forwarding, kein NAT.
- **Auth**: Auth-Key aus OpenBao (`secret/data/mcp/tailscale`, Feld `auth_key`),
  von `startup.sh` als `TS_AUTHKEY` gesetzt.

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
`create-skill`, `obsidian`,
`docker-host-location` (Kleve → `cleve-docker`),
`docker-host`, `docker-host-compose`, `docker-host-filesystem`, `docker-host-git`

> Die docker-host*-Skills sind generisch (geteilt mit Krefeld). Das konkrete
> Profil steht im `docker-host-location`-Skill — hier: `cleve-docker`.

## Referenzen
- ssh-mcp: https://github.com/tufantunc/ssh-mcp
- Tailscale: https://tailscale.com/
