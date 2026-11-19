# Server Management – OpenCode-Projekt

## Kontext
Verwaltung der Infrastruktur-Hosts über den Community-SSH-MCP (`ssh-mcp`) und
Authentik über den Community-MCP (`authentik-mcp`).

## MCP-Server
- **ssh-mcp** (lokal, stdio) – im Workspace-Container via `npm install -g ssh-mcp`
  - Konfiguration: `~/.config/ssh-mcp/config.toml` (von `startup.sh` erzeugt, nicht im Repo)
  - Auth: statischer ed25519-Key aus OpenBao (`secret/data/mcp/ssh-mcp`) → `/root/.ssh/id_ed25519_mcp`
  - Zusätzlich: exklusiver PC-Key `secret/data/mcp/ssh-mcp-pc` → `/root/.ssh/id_ed25519_mcp_pc` (nur für Profil `carsten-pc`)
  - `config-full.toml` enthält zusätzlich das Profil `carsten-pc`
  - Policy: `role=admin`, `group=prod`, `approvalPolicy=auto` (alle Befehlsklassen inkl. sudo)
  - SFTP: `transferRoot=/root/.ssh-mcp-transfers`
- **authentik** (lokal, `uvx`) – `nikitatsym/authentik-mcp`
  - Token: OpenBao `secret/data/mcp/authentik` → `AUTHENTIK_API_KEY`
- **homarr** (remote) – `https://homarr.mueller-nas.de/api/mcp`, Auth per Header `ApiKey` aus `{env:HOMARR_API_KEY}` (OpenBao `secret/data/mcp/homarr`)
- **unifi** (lokal, `unifi-network-mcp`) – Netzwerk/Firewall-MCP für das UniFi-Gateway
  - Version gepinnt über `UNIFI_MCP_VERSION` in `coder-templates/versions.env`
  - Auth: lokaler Admin `mcp-svc` (Local Access Only, **kein** Ubiquiti-SSO, kein MFA);
    Passwort aus OpenBao `secret/data/mcp/unifi` → Datei `/root/.config/unifi-mcp/password`
    (0600), gelesen per `UNIFI_PASSWORD_FILE` — bewusst **nicht** im OpenCode-Service-Env
  - `UNIFI_HOST=10.0.0.1`, `UNIFI_VERIFY_SSL=false` (selbstsigniertes Gateway-Zertifikat)
  - Lazy Mode: zuerst die Meta-Tools (`*_tool_index`, `*_execute`, `*_batch`), Domänen-Tools
    werden bei Bedarf nachgeladen — das hält den Kontext klein

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
| `carsten-pc` | 10.0.0.20 | Carsten-PC (Windows, User carst, VLAN 10.0.0.0/24) |

> **Carsten-PC** ist Windows: kein `sudo` (`privileged-command` nicht verwenden) und die
> Shell ist `cmd.exe`/PowerShell — POSIX-Helfer wie `ls`/`cat`/`grep` sind unzuverlässig.

> **Kleve-Hosts** (`cleve-proxmox` .50, `cleve-truenas` .51, `cleve-docker` .52)
> sind angeschlossen, gehören aber zum **cleve**-Workspace mit eigenen Skills.

## UniFi (UCG-Ultra)

Netzwerk, Firewall/Zonen, Port-Forwardings, VLANs/WLANs, Clients und Traffic-Flows
laufen über den MCP-Server `unifi`. Der SSH-Zugang (`profile: unifi`) bleibt für Fälle,
die die API nicht abdeckt (Konsole, Restore, Custom-Configs).

- **Änderungen nur nach Rücksprache:** Firewall-Policies, Port-Forwardings, WLAN-, VLAN-
  und DHCP-Änderungen erst als Preview/Dry-run zeigen und auf Freigabe warten.
- Vor Firewall-/Zonen-Änderungen den Ist-Stand sichern (Policy-Liste exportieren).
- Adressen und Namen aus dem MCP lesen, nicht raten.
- Änderungen immer über die API — nicht per SSH die Konfiguration von Hand patchen.

## Richtlinien
- **NIEMALS raw SSH via bash** (`ssh Docker '...'`, `bash("ssh ...")`) – ausschließlich `ssh-mcp`-Tools
- Befehle: `run-command({profile:"...", command:"..."})`; lesend: `read-command(...)`; sudo: `privileged-command(...)`
- Dateien: `sftp-list`, `sftp-download`, `sftp-upload` (große Dateien: `sftp-*-file`)
- Keine Secrets, Tokens oder private Keys im Repo

## Skills
`authentik`, `authentik-integration`, `authentik-troubleshooting`,
`docker-host-location` (Krefeld → `docker-host`),
`docker-host`, `docker-host-compose`, `docker-host-filesystem`, `docker-host-git`,
`create-skill`

> Die docker-host*-Skills sind generisch (geteilt mit dem cleve-Workspace).
> Das konkrete Profil steht im `docker-host-location`-Skill — hier: `docker-host`.

## Referenzen
- ssh-mcp: https://github.com/tufantunc/ssh-mcp
- authentik-mcp: https://github.com/nikitatsym/authentik-mcp
