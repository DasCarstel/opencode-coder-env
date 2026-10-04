---
name: docker-host
description: Connect to Docker host at 10.0.10.10 via SSH using Bitwarden SSH-Agent
license: MIT
---

## CRITICAL: No raw SSH via bash

**DO NOT use `bash("ssh ...")` for ANY remote host operation. This is FORBIDDEN.**

Use MCP tools instead. Every time you think `bash("ssh Docker ...")`, you MUST use:
- `ssh-multi` → `exec({host: "Docker", command: "..."})` for commands
- `ssh-multi` → `read_file({host: "Docker", path: "..."})` for files
- `ssh-multi` → `write_file({host: "Docker", path: "...", content: "..."})` for writes
- `docker-docker-host` MCP tools for Docker operations
- `compose-docker-host` MCP tools for Docker Compose operations

This rule is enforced by global AGENTS.md and remote-shell.md instructions.

---

## CRITICAL: Tool Selection — Use the RIGHT MCP Server

**`ssh-multi exec({host:"Docker", ...})` is ONLY for commands without a dedicated MCP server.**

| Du willst... | ❌ Falsch | ✅ Richtig |
|-------------|-----------|-----------|
| Compose hoch-/runterfahren | exec({host:"Docker",command:"docker compose up -d"}) | compose_up({service:"jellyfin"}) |
| Compose Logs | exec(...) | compose_logs({service:"traefik"}) |
| Compose Pull | exec(...) | compose_pull({service:"immich"}) |
| Container-Status | exec({host:"Docker",command:"docker ps"}) | docker-docker-host → list_containers() |
| Container-Logs | exec({host:"Docker",command:"docker logs ..."}) | docker-docker-host → fetch_container_logs() |
| Git in /etc/docker | exec({host:"Docker",command:"cd /etc/docker && git add -A && git commit ..."}) | git-docker-host → git_add(), git_commit() |
| Datei editieren | write_file({host:"Docker",content:"...500 Zeilen..."}) | filesystem-docker-host → edit_file({dryRun:true, ...}) |
| Datei lesen (Docker) | exec({host:"Docker",command:"cat ..."}) | filesystem-docker-host → read_text_file({...}) |
| Datei suchen (Docker) | exec({host:"Docker",command:"find ..."}) | filesystem-docker-host → search_files({...}) |
| Verzeichnis-Baum (Docker) | exec({host:"Docker",command:"tree ..."}) | filesystem-docker-host → directory_tree({...}) |

**ssh-multi exec ist NUR für:** System-Status (df, free, uptime), Nicht-Docker Hosts (Proxmox, TrueNAS), Befehle ohne dedizierten MCP Server.

---

## Docker Host SSH Connection

This skill enables SSH connections to the Docker host server using the Bitwarden SSH-Agent.

> **Adding a new MCP server?** Load the `create-mcp-server` skill first — it has step-by-step instructions, code templates, and a mandatory checklist of all files to update.

### 🔌 MCP Servers

Seven MCP servers are available:

| MCP Server | Tools | Best for |
|-----------|-------|----------|
| `docker-docker-host` | 19 tools: list_containers, start_container, stop_container, fetch_container_logs, list_images, pull_image, run_container, remove_container, list_volumes, list_networks, etc. | Docker container/image/volume/network management on Docker Host |
| `compose-docker-host` | 7 tools: compose_list, compose_ps, compose_up, compose_down, compose_pull, compose_logs, compose_restart | Docker Compose: pull, up, down, restart, logs, status. Runs on Docker Host, base dir: /opt/docker-compose |
| `filesystem-docker-host` | 14 tools: read_file, read_text_file, read_multiple_files, write_file, edit_file, create_directory, list_directory, list_directory_with_sizes, directory_tree, move_file, search_files, get_file_info, read_media_file, list_allowed_directories | File ops on Docker Host (/opt/docker-compose, /etc/docker): search, tree, info, move |
| `ssh-multi` | 6 tools: list_servers, exec, read_file, write_file, edit_file, upload_file | SSH commands & file ops on ANY host from ~/.ssh/config |
| `wait` | 3 tools: sleep, wait_for_command, wait_for_http | Sleep/poll/wait for conditions (local). Use sleep before polling instead of retrying every few seconds. |
| `git` | 28 tools: git_status, git_diff, git_log, git_add, git_commit, etc. | Git operations on LOCAL repositories |
| `git-docker-host` | 28 tools, runs ON Docker Host via SSH stdio proxy (base dir: /etc/docker) | Git operations on /etc/docker repository |

**Usage:**
- Docker ops: `docker-docker-host` MCP tools (structured data)
- Docker Compose: `compose-docker-host` MCP tools → compose_up({service: "jellyfin"}), compose_pull({service: "traefik"}), etc.
- List compose services: compose_list() — currently 43 services
- Docker Host file ops: `filesystem-docker-host` → search_files, directory_tree, edit_file, get_file_info, etc.
- SSH commands: `ssh-multi` → exec({host: "Host", command: "..."})
- Remote files (all hosts): `ssh-multi` → read_file / write_file / edit_file / upload_file
- Docker Host git: `git-docker-host` MCP tools
- Local git: `git` MCP tools
- Local commands: `bash` tool ONLY for WSL/local operations
- List all hosts: `ssh-multi` → list_servers()

### Connection Details
- **Host:** 10.0.10.10
- **User:** root
- **SSH-Agent:** Bitwarden (auto-start via ~/.bashrc, socket: ~/.ssh/sockets/bitwarden-agent.sock)

## Svelte Development on Docker Host

Svelte source code lives at `/opt/docker-compose/<name>/` and is edited via `filesystem-docker-host`.

### Dev-Server Compose Template

```yaml
services:
  <name>:
    image: node:22-bookworm
    container_name: <name>
    restart: unless-stopped
    working_dir: /app
    command: sh -c "npm install --prefer-offline && npm run dev -- --host 0.0.0.0"
    volumes:
      - /opt/docker-compose/<name>:/app
      - node-modules:/app/node_modules
    networks:
      - proxy
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.<name>.entrypoints=http"
      - "traefik.http.routers.<name>.rule=Host(`<name>.mueller-nas.de`)"
      - "traefik.http.middlewares.<name>-https-redirect.redirectscheme.scheme=https"
      - "traefik.http.routers.<name>.middlewares=<name>-https-redirect"
      - "traefik.http.routers.<name>-secure.entrypoints=https"
      - "traefik.http.routers.<name>-secure.rule=Host(`<name>.mueller-nas.de`)"
      - "traefik.http.routers.<name>-secure.tls=true"
      - "traefik.http.routers.<name>-secure.service=<name>"
      - "traefik.http.routers.<name>-secure.middlewares=authentik@docker"
      - "traefik.http.services.<name>.loadbalancer.server.port=5173"
      - "traefik.docker.network=proxy"

volumes:
  node-modules:

networks:
  proxy:
    external: true
```

**Svelte project init on Docker Host:**
```
cd /opt/docker-compose/<name> && PATH="/root/.nvm/versions/node/v22.22.3/bin:$PATH" && npx create-vite@latest . --template svelte && npm install
```

Note: Docker Host system node is v18, use nvm node v22 at `/root/.nvm/versions/node/v22.22.3/bin/node` for scaffolding + npm install.



**NEVER run these commands without explicit user confirmation:**

1. **DO NOT use `dd` on block devices** (`/dev/sd*`, `/dev/nvme*`)
   - Never run: `dd if=... of=/dev/sdX`
   - Even "harmless" tests can destroy partition tables and ZFS labels

2. **DO NOT run destructive partitioning commands**
   - `fdisk -l` is OK (read-only)
   - Never use: `fdisk /dev/sdX` without knowing exact consequences

3. **DO NOT run raw disk commands on remote systems**
   - Always verify target with `lsblk` or `fdisk -l` first
   - Ask for confirmation before any disk-related operation

4. **Verify BEFORE executing**
   - On Proxmox: `/dev/sda` is often the BOOT disk, NOT the data disks
   - On TrueNAS: Always verify which disks are pool disks

---

### System Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│  Proxmox Host (10.0.10.20)                                     │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │  TrueNAS VM (10.0.10.30)                                   ││
│  │  Pool-HDD: RAID-Z1 (4x 4TB WD Red)                        ││
│  │  NFS Export: /mnt/Pool-HDD/Mediathek                       ││
│  └─────────────────────────────────────────────────────────────┘│
│  ┌─────────────────────────────────────────────────────────────┐│
│  │  Docker LXC (10.0.10.10)                                  ││
│  │  - Containers: jellyfin, sonarr, radarr, sabnzbd, etc.   ││
│  │  - Mounts: /mnt/Mediathek (NFS from TrueNAS)             ││
│  │  - Local Storage: /mnt/SABnzbd, /mnt/transcode (ZFS)      ││
│  └─────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────┘
```

### Disk/Storage Mapping

| Path | Type | Host | Notes |
|------|------|------|-------|
| `/mnt/Mediathek` | NFS | TrueNAS | Media library |
| `/mnt/SABnzbd` | ZFS (local) | Docker | Downloads |
| `/mnt/transcode` | ZFS (local) | Docker | Jellyfin transcode |
| `/mnt/OneDrive` | ZFS (local) | Docker | OneDrive sync |

### Common Locations on Docker Host
- Docker compose files: `/opt/docker-compose/`
- Docker configs: `/etc/docker/`
- Traefik config: `/etc/docker/traefik/`
- Pihole DNS config (v6): `/etc/docker/pihole/config/pihole.toml`
- Pihole dnsmasq overrides: `/etc/docker/pihole/dnsmasq.d/`

---

## Verzeichnisstruktur (Zwingend)

Bei jedem neuen Service MUSS diese Struktur verwendet werden:

| Ressource | Pfad | Git-getrackt? |
|-----------|------|--------------|
| Compose | `/opt/docker-compose/<service>/` | Nein |
| `.env` | `/opt/docker-compose/<service>/.env` | Nein |
| `.env.example` | `/opt/docker-compose/<service>/.env.example` | Nein |
| Config | `/etc/docker/<service>/` | Ja (nur handgemachte Configs) |
| Runtime-Volumes (data, scripts, uploads, etc.) | `/etc/docker/<service>/<volume>/` | Nein (von `.gitignore` ausgeschlossen) |

### Warum diese Struktur?
- **Trennung**: Compose von Config getrennt
- **Git-fähig**: `/etc/docker/` enthält nur Config, kann mit git gesichert werden
- **Sauber**: Jeder Service hat eigene Verzeichnisse
- **Runtime-Volumes** (Datenbanken, Uploads, Scripts, Caches) kommen ebenfalls unter `/etc/docker/<service>/` — `.gitignore` verhindert Tracking

---

### Git-Repo für Configs (/etc/docker)

`/etc/docker/` ist ein Git-Repo das NUR handgemachte Config-Dateien tracked.

#### Root `.gitignore` (/etc/docker/.gitignore)

```gitignore
*
!.gitignore
!*/
```

- `*` ignoriert alles auf Root-Ebene
- `!.gitignore` tracked die Root-.gitignore selbst
- `!*/` tracked alle Service-Verzeichnisse (leer), damit deren eigene .gitignore gelesen wird

#### Per-Service `.gitignore` (/etc/docker/<service>/.gitignore)

JEDER Service MUSS eine eigene `.gitignore` haben. Schema:
```
*
!.gitignore
!config.yml              # <-- nur handgemachte Configs mit ! prefix
!configuration.yaml
```

### `.env` + `.env.example` Regel

- **Service mit Secrets:** `.env` enthält die echten Werte, `.env.example` enthält Platzhalter (`change-me`)
- **Service ohne Secrets:** `.env` ist leer (nur Kommentar), `.env.example` dokumentiert dass keine Secrets nötig sind

### Git-Tracking Regeln

Nur tracken: Dateien die ein Mensch von Hand erstellt/angepasst hat.
Nicht tracken:
- Datenbanken (`*.db`, `*.sqlite`, `*.couch`)
- Logs (`*.log`, `logs/`)
- Caches, MediaCover, Uploads
- Temporäre Dateien
- Downloads (NZB, Musik, Videos)
- Aus dem Internet nachladbare Resourcen
- Backup-Dateien
- Von der Applikation generierte Configs

**Unterverzeichnisse:** Das Elternverzeichnis muss auch freigegeben werden:
```
# Korrekt für config/custom.list:
!config/
!config/custom.list
```

---

## Neuen Service erstellen (MCP)

Jeder neue Service folgt diesem Ablauf über MCP:

```
1. write_file({host:"Docker", path:"/opt/docker-compose/<service>/docker-compose.yml", content:"..."})
2. write_file({host:"Docker", path:"/opt/docker-compose/<service>/.env", content:"..."})
3. write_file({host:"Docker", path:"/opt/docker-compose/<service>/.env.example", content:"..."})
4. exec({host:"Docker", command:"mkdir -p /etc/docker/<service>/config /etc/docker/<service>/data"})
5. write_file({host:"Docker", path:"/etc/docker/<service>/.gitignore", content:"*\n!.gitignore\n!config.yml"})
6. write_file({host:"Docker", path:"/etc/docker/<service>/config/config.yml", content:"..."})
7. DNS-Eintrag: pihole → hosts-Liste in pihole.toml ergänzen + Container neustarten
8. git-docker-host → git_set_working_dir({path:"/etc/docker"}), dann git_add, git_commit
9. Authentik SSO konfigurieren — load skill({name:"authentik"}) and follow "Full Example":
   a. Check https://integrations.goauthentik.io/<service>/ for native OIDC/SAML support
   b. If Proxy needed: create Provider (forward_single) + Application + Policy Stack
   c. Assign Provider to Traefik Proxy Outpost
   d. Ensure docker-compose.yml has "authentik@docker" middleware in Traefik labels
10. compose → compose_up({service:"<service>"})
```

---

### Traefik Labels Pattern

When adding Traefik labels to a new service, use this pattern:

```yaml
labels:
  - "traefik.enable=true"
  - "traefik.http.routers.<service>.entrypoints=http"
  - "traefik.http.routers.<service>.rule=Host(`<service>.mueller-nas.de`)"
  - "traefik.http.middlewares.<service>-https-redirect.redirectscheme.scheme=https"
  - "traefik.http.routers.<service>.middlewares=<service>-https-redirect"
  - "traefik.http.routers.<service>-secure.entrypoints=https"
  - "traefik.http.routers.<service>-secure.rule=Host(`<service>.mueller-nas.de`)"
  - "traefik.http.routers.<service>-secure.tls=true"
  - "traefik.http.routers.<service>-secure.service=<service>"
  - "traefik.http.routers.<service>-secure.middlewares=authentik@docker"
  - "traefik.http.services.<service>.loadbalancer.server.port=<port>"
  - "traefik.docker.network=proxy"
networks:
  proxy:
    external: true
```

### Service Compose Template

```yaml
services:
  <service>:
    image: <image>
    container_name: <service>
    restart: unless-stopped
    volumes:
      - /etc/docker/<service>/config:/config
      - /etc/docker/<service>/data:/data
    networks:
      - proxy
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.<service>.rule=Host(`<service>.mueller-nas.de`)"
      - "traefik.http.routers.<service>.entrypoints=https"
      - "traefik.http.routers.<service>.tls=true"
      - "traefik.http.routers.<service>.tls.certresolver=cloudflare"
      - "traefik.http.routers.<service>.middlewares=authentik@docker"
      - "traefik.http.services.<service>.loadbalancer.server.port=<port>"
      - "traefik.docker.network=proxy"

networks:
  proxy:
    external: true
```

**Wenn die App per Traefik exponiert wird und die `authentik@docker` Middleware nutzt, MUSS zusätzlich Authentik SSO konfiguriert werden.**
→ `skill({ name: "authentik" })` laden und "Full Example: Adding a New Proxy-Protected App" befolgen.

---

### TLS-Zertifikate mit Traefik (Referenz)

#### Traefik docker-compose.yml

```yaml
services:
  traefik:
    image: traefik:v3.0
    container_name: traefik
    restart: unless-stopped
    security_opt:
      - no-new-privileges:true
    networks:
      - proxy
    ports:
      - "80:80"
      - "443:443"
      - "127.0.0.1:8080:8080"
    environment:
      - TZ=Europe/Berlin
      - CF_API_EMAIL=your-email@example.com
      - CF_API_KEY=your-cloudflare-api-key
    volumes:
      - /etc/localtime:/etc/localtime:ro
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - ./data/traefik.yml:/traefik.yml:ro
      - ./data/acme.json:/acme.json
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.dashboard.rule=Host(`traefik.mueller-nas.de`)"
      - "traefik.http.routers.dashboard.service=api@internal"
      - "traefik.http.routers.dashboard.entrypoints=https"
      - "traefik.http.routers.dashboard.tls=true"
      - "traefik.http.routers.dashboard.middlewares=authentik@docker"

networks:
  proxy:
    name: proxy
    external: true
```

#### traefik.yml

```yaml
api:
  dashboard: true
  insecure: true

entryPoints:
  http:
    address: ":80"
    http:
      redirections:
        entryPoint:
          to: https
          scheme: https
  https:
    address: ":443"

certificatesResolvers:
  cloudflare:
    acme:
      email: your-email@example.com
      storage: acme.json
      dnsChallenge:
        provider: cloudflare
        resolvers:
          - "1.1.1.1:53"
          - "1.0.0.1:53"

providers:
  docker:
    endpoint: "unix:///var/run/docker.sock"
    exposedByDefault: false
    network: proxy
  file:
    directory: /config
    watch: true

log:
  level: INFO
  filePath: /logs/traefik.log

accessLog:
  filePath: /logs/access.log
```

---

### Pihole Local DNS (Pi-hole v6)

**Use `/etc/docker/pihole/config/pihole.toml` only.**
The legacy `custom.list` is deprecated and should remain empty.

```bash
# Add new DNS entry via ssh-multi exec
exec({host:"Docker", command:"python3 - <<'PY'
from pathlib import Path
path = Path('/etc/docker/pihole/config/pihole.toml')
text = path.read_text()
needle = 'hosts = ['
entry = '    \"10.0.10.10 <service>.mueller-nas.de\",\n'
if entry in text:
    raise SystemExit('entry already exists')
idx = text.find(needle)
if idx == -1:
    raise SystemExit('hosts list not found')
insert_at = text.find('\n', idx) + 1
path.write_text(text[:insert_at] + entry + text[insert_at:])
PY"})

# Restart Pihole
docker → stop_container({container_id: "pihole"})
docker → start_container({container_id: "pihole"})
```

### Pihole IPv4-only wildcard

`/etc/docker/pihole/dnsmasq.d/02-local-ipv4-only.conf`

```conf
# Default all mueller-nas.de to Docker host (IPv4 only)
address=/.mueller-nas.de/10.0.10.10
address=/.mueller-nas.de/::

# PTR for DNS server display name
ptr-record=10.10.0.10.in-addr.arpa,unifi.mueller-nas.de
```

---

### Performance Testing Guidelines

1. **NEVER test on production data** — use test files, clean up afterward

2. **Safe test commands:**
   ```bash
   # Read-only network test
   iperf3 -c <target>
   
   # Write test to local ZFS (SAFE)
   dd if=/dev/zero of=/mnt/transcode/.test bs=1M count=1024
   
   # Read test from NFS (SAFE)
   dd if=/mnt/Mediathek/.testfile of=/dev/null bs=1M
   ```

3. **NEVER use these:**
   - `dd if=/dev/zero of=/dev/sdX` (destroys partition table!)
   - `dd if=/dev/zero of=/dev/nvmeXn1` (destroys data!)
   - Any `dd` with `oflag=direct` on raw devices

---

### Recovery Notes

1. **STOP immediately** - don't write anything else
2. **Check ZFS status:**
   ```
   exec({host:"Docker", command:"zpool status"})
   exec({host:"Docker", command:"zpool list"})
   ```
3. **Don't reboot** without consulting documentation
4. **SystemRescue+ZFS ISO** is available for recovery

---

### Bitwarden SSH-Agent Bridge

Der docker-host Skill nutzt den Bitwarden SSH-Agent, der in WSL2 läuft.

**So funktioniert es:**
1. Bitwarden auf Windows stellt SSH-Keys bereit
2. WSL2 bridge (socat + npiperelay) verbindet zum Windows Named Pipe
3. SSH in WSL nutzt automatisch die Keys aus dem Agent

**Wichtig:**
- Bitwarden Vault muss entsperrt sein für SSH-Keys
- Die Bridge startet automatisch bei neuen Shells
- Keine temporären Key-Dateien nötig
