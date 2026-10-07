# opencode-coder-env

Konfigurations-Repository für OpenCode in Coder Workspaces.

## Struktur

```
opencode-coder-env/
├── tiers.json                                # Model-Router Konfiguration
├── workspaces/
│   ├── infrastructure/
│   │   ├── homeassistant/                    # Home-Assistant-Workspace
│   │   │   ├── opencode.json                 # HA-MCP + HA-Skills
│   │   │   └── AGENTS.md
│   │   ├── server-management/                # Server-Management-Workspace
│   │   │   ├── opencode.json                 # authentik-MCP + Server-Skills
│   │   │   └── AGENTS.md
│   │   └── svelte/                           # Svelte-Entwicklung
│   │       ├── opencode.json                 # Svelte-MCP (remote) + oCIS privat
│   │       └── AGENTS.md
│   ├── general/
│   │   └── workspace-global.json
│   └── minijob/
│       └── workspace-global.json
├── coder-templates/
│   ├── shared/
│   │   └── startup.sh                  # gemeinsames Setup (Profil: infrastructure|general|muellerconnect)
│   ├── infrastructure/
│   │   ├── main.tf
│   │   ├── Dockerfile
│   │   └── config/
│   │       └── global-opencode.json          # globale Config (nur ssh-mcp)
│   ├── general/
│   │   ├── main.tf
│   │   └── Dockerfile
│   └── muellerconnect/
│       ├── main.tf
│       └── Dockerfile
└── skills/
    ├── homeassistant/                        # nur Home-Assistant-Workspace
    │   ├── home-assistant/
    │   └── google-home-exposure/
    ├── obsidian/                             # private Obsidian-Vault (HA + Server-Management)
    │   └── obsidian/
    ├── server/                               # nur Server-Management-Workspace
    │   ├── authentik/                        # + authentik-integration/ -troubleshooting/
    │   ├── docker-host/
    │   ├── docker-host-compose/
    │   ├── docker-host-filesystem/
    │   └── docker-host-git/
    ├── minijob/                              # nur MuellerConnect/minijob-Workspace
    │   └── obsidian/
    └── shared/                               # beide Workspaces
        └── create-skill/
```

## Wichtige Hinweise

- **Keine Secrets in Git**: Keine Tokens, private SSH-Keys, Unseal-Shares oder `.env`-Dateien
- **Kein eigener MCP-Code**: Nur deklarative Konfiguration, keine `node_modules` oder Build-Schritte
- **Per-Workspace-Trennung**: Jeder Herdr-Workspace bekommt nur die benötigten MCP-Server und Skills
- **OpenBao als Secret-Quelle**: Alle API-Keys werden zur Laufzeit aus OpenBao bezogen
- **GitHub ohne MCP**: private Repos werden mit `git` + Token aus OpenBao geklont/gepusht (kein GitHub-MCP, kein `gh` im Workspace)

## Workspaces

| Workspace | Zweck | Netzwerk | MCP-Server | Skills |
|-----------|-------|----------|------------|--------|
| Home Assistant | HA-Verwaltung | `coder-infra` | `homeassistant`, `ocis` (privat) | `home-assistant`, `google-home-exposure`, `obsidian` |
| Server Management | Infrastruktur-Hosts | `coder-infra` | `authentik`, `ocis` (privat), `ssh-mcp` | `authentik`, `docker-host*`, `create-skill`, `obsidian` |
| Svelte | Svelte-Entwicklung | `coder-infra` | `svelte` (remote), `ocis` (privat), `ssh-mcp` | `create-skill`, `obsidian` |
| General | Allgemeine Aufgaben | `coder-gen` | `ocis` (privat), `pdf` | `create-skill`, `obsidian` |
| MuellerConnect/minijob | Mini-Job | `coder-mc` | `ocis` (Arbeit), `pdf` | `create-skill`, `obsidian` |

- **General** und **MuellerConnect/minijob** sind isoliert: kein `ssh-mcp`, keine
  SSH-Keys, kein Zugriff auf das interne VLAN. Nur der oCIS-MCP (Obsidian-Vault).
- **General** nutzt den privaten Obsidian-Vault, **MuellerConnect/minijob** den
  Arbeits-Vault (Hard-Guardrail, siehe oCIS-Artikel).
- Das komplette Setup liegt in `coder-templates/shared/startup.sh` (Profil:
  `infrastructure` | `general` | `muellerconnect`). Die Templates `general/` und
  `muellerconnect/` sind dünne Wrapper.

## MCP-Server

OpenCode V2 konfiguriert MCP-Server unter `mcp.servers`. Konfig-Dateien werden
über die Verzeichnis-Hierarchie gemerged (global → Workspace).

### Global – alle Workspaces
`coder-templates/infrastructure/config/global-opencode.json` (installiert nach
`~/.config/opencode/opencode.json`):

- **ssh-mcp** – SSH-Zugriff auf die Infrastruktur-Hosts (siehe unten)

### Home-Assistant-Workspace
`workspaces/infrastructure/homeassistant/opencode.json`:

- **homeassistant** – remote MCP, `https://intern-homeassistant.mueller-nas.de/api/mcp`,
  Auth: `Authorization: Bearer {env:HA_LLA_TOKEN}`
- **ocis** – lokaler offizieller ownCloud-MCP (`owncloud/ocis-mcp-server` v1.1.0, Go-Binary),
  Env `OCIS_MCP_OCIS_URL=https://opencloud.mueller-nas.de`,
  `OCIS_MCP_APP_TOKEN_USER={env:OCIS_PRIVATE_USER}`, `OCIS_MCP_APP_TOKEN_VALUE={env:OCIS_PRIVATE_TOKEN}`

### Server-Management-Workspace
`workspaces/infrastructure/server-management/opencode.json`:

- **authentik** – lokaler Community-MCP (`nikitatsym/authentik-mcp` via `uvx`),
  Env `AUTHENTIK_URL=https://auth.mueller-nas.de`, `AUTHENTIK_TOKEN={env:AUTHENTIK_API_KEY}`
- **ocis** – lokaler offizieller ownCloud-MCP (`owncloud/ocis-mcp-server` v1.1.0),
  Env wie im Home-Assistant-Workspace (`OCIS_PRIVATE_USER` / `OCIS_PRIVATE_TOKEN`)

### Svelte-Workspace
`workspaces/infrastructure/svelte/opencode.json`:

- **svelte** – offizieller Remote-MCP, `https://mcp.svelte.dev/mcp` (kein API-Key, nichts lokal installiert)
- **ocis** – lokaler offizieller ownCloud-MCP (`owncloud/ocis-mcp-server` v1.1.0),
  Env wie im Home-Assistant-Workspace (`OCIS_PRIVATE_USER` / `OCIS_PRIVATE_TOKEN`)
- **ssh-mcp** – aus der globalen Config (`~/.config/opencode/opencode.json`)

### General / MuellerConnect-Workspace
`workspaces/general/workspace-global.json` bzw. `workspaces/minijob/workspace-global.json`:

- **ocis** – lokaler offizieller ownCloud-MCP (`owncloud/ocis-mcp-server` v1.1.0),
  Env: `OCIS_PRIVATE_USER`/`OCIS_PRIVATE_TOKEN` (general) bzw. `OCIS_MUELLERCONNECT_USER`/`OCIS_MUELLERCONNECT_TOKEN` (muellerconnect)
- **pdf** – lokaler MCP `@sylphx/anymd` v8.5.1 (Nachfolger von `@sylphx/pdf-reader-mcp`),
  Rust-Binary via npm, lokales Lesen von PDFs und URLs, kein API-Key

> `uvx` wird vom Template-Startup-Skript installiert (`/root/.local/bin/uvx`).
> Das `ocis-mcp-server`-Binary lädt `startup.sh` nach `/root/.local/bin/ocis-mcp-server`.

### oCIS (OpenCloud / Obsidian) – Hard-Guardrail

Der offizielle ownCloud-MCP `owncloud/ocis-mcp-server` (v1.1.0, Go) stellt den
Obsidian-Vault über die oCIS-APIs bereit — die Dateien bleiben ausschließlich
auf OpenCloud (kein zweiter Speicher).

Die Trennung erfolgt über **zwei getrennte OpenCloud-Service-Accounts** mit je
einem App-Token, das nur den eigenen Space sieht:

| Workspace | Account | Space | Space-ID (Beginn) |
|-----------|---------|-------|-------------------|
| Home Assistant, Server Management | `obsidian-private` | `Obsidian` | `a0ca6a90-…!8da8246b-…` |
| MuellerConnect/minijob | `obsidian-muellerconnect` | `MuellerConnect` | `8da8246b-…$fb65ae72-…` |

Tokens liegen in OpenBao unter `secret/data/mcp/opencloud-ocis`. Das
**Infrastructure-Template injiziert nur** `OCIS_PRIVATE_*` und entfernt das
MuellerConnect-Token aktiv — im Infrastructure-Workspace ist es physisch nicht
vorhanden. Die passende Skill (`obsidian`) liegt pro Workspace unter
`skills/obsidian/obsidian/` (privat) bzw. `skills/minijob/obsidian/` (Arbeit).

### ssh-mcp – Hosts & Policy
Konfiguration: `~/.config/ssh-mcp/config.toml` (von `startup.sh` erzeugt).

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

- **Auth**: statischer ed25519-Key aus OpenBao (`secret/data/mcp/ssh-mcp`),
  geschrieben nach `/root/.ssh/id_ed25519_mcp`
- **Policy**: `role = "admin"`, `group = "prod"`, `approvalPolicy = "auto"` für
  alle Profile; `[policy.roleBindings.admin] prod` schließt `privileged` (sudo) mit ein
- **SFTP**: `transferRoot = "/root/.ssh-mcp-transfers"` aktiviert die Streaming-Tools
  (`sftp-upload-file` / `sftp-download-file`)
- Die eingebaute **Forbidden-Liste** (`rm -rf /`, `mkfs`, Schreibzugriff auf
  `authorized_keys`, …) bleibt immer aktiv

> **Kleve** (192.168.178.50/51/52) ist noch nicht eingebunden – die Profile
> folgen, sobald das Netz erreichbar ist.

## Skills

Skills werden über das `skills`-Array in der jeweiligen `opencode.json`
eingebunden. Pfade sind relativ zum Arbeitsverzeichnis des Workspace
(`../../../skills/...`).

| Skill | Home Assistant | Server Management |
|-------|:--------------:|:-----------------:|
| `home-assistant` | ✅ | – |
| `google-home-exposure` | ✅ | – |
| `obsidian` | ✅ | ✅ |
| `authentik` (+ `authentik-integration`, `authentik-troubleshooting`) | – | ✅ |
| `docker-host` (+ `docker-host-compose`, `-filesystem`, `-git`) | – | ✅ |
| `create-skill` | – | ✅ |

Die Built-in-Skills von OpenCode (`opencode`, `report`) sind in beiden
Workspaces verfügbar.

## Secrets-Architektur

```
Coder Secret (OPENBAO_APPROLE_SECRET_ID)
  ↓ (wird in den Workspace injiziert)
startup.sh → AppRole-Auth bei OpenBao
  ↓ (kurzlebiger Token, 1h TTL)
OpenBao KV (secret/data/mcp/*):
  - homeassistant  → HA_LLA_TOKEN
  - authentik      → AUTHENTIK_API_KEY
  - grafana        → GRAFANA_API_KEY
  - opencloud      → OPENCLOUD_API_KEY / OPENCLOUD_USERNAME
  - opencloud-ocis → OCIS_PRIVATE_USER/TOKEN, OCIS_MUELLERCONNECT_USER/TOKEN
  - opencode-go    → OC_GO_DEFAULT1..3 / OC_GO_ACTIVE
  - ssh-mcp        → private_key (statischer ssh-mcp-Key)
  - github         → GITHUB_TOKEN (git-Credentials)
  ↓ (geschrieben nach /etc/opencode.env bzw. /root/.ssh/)
OpenCode MCPs nutzen die Umgebungsvariablen ({env:...})
```

**Keine Secrets in:**
- ❌ Git Repository
- ❌ Lokale Dateien (außer `bootstrap.env` auf dem Docker Host)
- ❌ Shell-History
- ❌ Logs

## Setup in Coder Workspace

### 1. Coder Secret setzen
In der Coder-UI → Templates → `infrastructure` → Settings → Secrets:
- `openbao_approle_secret_id` = AppRole Secret-ID aus OpenBao

### 2. Template veröffentlichen
```bash
coder login --url https://coder.mueller-nas.de --token <dein-token>
coder templates push infrastructure --directory coder-templates/infrastructure --yes --org=coder
```

### 3. Workspace starten
Der Workspace cloned automatisch dieses Repo und holt die Secrets aus OpenBao.

### 4. OpenCode starten

Die Coder-App **„Infrastructure Workspace"** öffnet Herdr mit zwei Workspaces:

- **Home Assistant** → OpenCode mit HA-MCP
- **Server Management** → OpenCode mit authentik-MCP + SSH-MCP

Beide OpenCode-Instanzen laufen parallel in separaten Herdr-Panes und
beenden sich nicht gegenseitig.

## Architektur (aktueller Stand)

- **Herdr** ist der Einstiegspunkt (Coder-App). Es verwaltet beide
  OpenCode-Sitzungen in einem persistenten Server.
- **Secrets** kommen zur Laufzeit aus OpenBao (AppRole `mcp-server`).
- **SSH (ssh-mcp)** nutzt einen **statischen Key** aus OpenBao
  (`secret/data/mcp/ssh-mcp`). Der öffentliche Teil liegt in `authorized_keys`
  der Zielhosts. Grund: ssh-mcp verwendet `ssh2`, das keine OpenSSH-CA-Zertifikate
  unterstützt (`cert = true` schlägt fehl).
- **SSH (interaktiv)** in `~/.ssh/config` nutzt weiterhin kurzlebige Zertifikate
  der OpenBao-SSH-CA (Rolle `host-access`, Principal `root`).
- **Herdr-Binary** liegt auf dem Coder-Host unter `/opt/opencode-bin/herdr`
  und wird per Bind-Mount in den Workspace gemountet.

### Voraussetzungen auf den Hosts

- `authorized_keys` enthält den ssh-mcp-Public-Key (alle Profile)
- Docker / Proxmox / TrueNAS vertrauen zusätzlich der OpenBao-CA für interaktives SSH:
  `TrustedUserCAKeys /etc/ssh/openbao-ca.pub`

### OpenCode Go (mehrere Keys)

Die API-Keys liegen in OpenBao unter `secret/data/mcp/opencode-go`
(Felder `default1`, `default2`, `default3`, `active`). Beim Start wird der in
`active` hinterlegte Key als Umgebungsvariable `OPENCODE_API_KEY` gesetzt.

Im Workspace umschalten:

```bash
oc-go list              # verfügbare Keys + aktiver Key
oc-go use default1      # auf default1 wechseln (Session, Service-Restart)
oc-go default default1  # Standard-Key dauerhaft setzen (OpenBao + .bashrc)
```

Der Helper `oc-go` wird von `startup.sh` nach `/usr/local/bin/oc-go` installiert.
Der Standard-Key wird in OpenBao gespeichert und überlebt Container-Neuerstellungen.

### Host-Bootstrap (einmalig)

Das Skript `scripts/bootstrap-hosts.sh` richtet die OpenBao-Rolle `host-access`
ein und verteilt den CA-Key auf Docker, Proxmox und TrueNAS:

```bash
export OPENBAO_ROOT_TOKEN=<root-token>
bash scripts/bootstrap-hosts.sh
```

### Persistente Speicherung

| Was | Host-Pfad | Container-Pfad | Inhalt |
|-----|-----------|---------------|--------|
| Workspace | Docker-Volume `coder-...-data` | `~/opencode-coder-env` | Workspace-Dateien, Repo |
| OpenCode-Sessions | `/etc/opencode-data/<user>` | `~/.local/share/opencode` | Sessions/Chats (SQLite-DB), Configs |

**Wichtig:** OpenCode-Sessions (Chats) liegen in `~/.local/share/opencode/opencode.db`.
Der Ordner `/etc/opencode-data/<user>` auf dem Coder-Host persistiert sie über
Container-Neuerstellungen hinweg.

Beim Start wird das Repo im Volume per `git fetch` + `reset --hard FETCH_HEAD`
hart auf `origin/master` gesetzt (lokale Abweichungen werden verworfen).

### MCP- und Skill-Trennung

| | Home Assistant | Server Management |
|---|---|---|
| MCP-Server | `homeassistant`, `ocis`, `ssh-mcp` | `authentik`, `ocis`, `ssh-mcp` |
| Skills | `home-assistant`, `google-home-exposure`, `obsidian` | `authentik`, `docker-host*`, `create-skill`, `obsidian` |

Die Trennung erfolgt über `opencode.json` im jeweiligen Workspace-Verzeichnis
(Projekt-Config) plus die globale Config (nur `ssh-mcp`).

## Aktueller Stand (07.10.2026)

- OpenBao: unsealed, SSH-CA konfiguriert, AppRole `mcp-server` vorhanden
- HA `mcp_server` Integration aktiv, Token in OpenBao (`secret/data/mcp/homeassistant`)
- ssh-mcp v2.17.0 mit statischem Key (`secret/data/mcp/ssh-mcp`), 8 Host-Profile
- authentik-MCP als Community-Server (`uvx`) im Server-Management-Workspace
- oCIS-MCP (`owncloud/ocis-mcp-server` v1.1.0) für Obsidian-Vaults, Hard-Guardrail via pro-space Service-Accounts
- Gemeinsames Setup in `coder-templates/shared/startup.sh` (Profile: `infrastructure`, `general`, `muellerconnect`)
- Coder-Templates: `infrastructure` (HA + Server Management), `general` (isoliert), `muellerconnect` (isoliert)
- Coder-Infrastruktur: Docker-Container auf 10.0.10.17
