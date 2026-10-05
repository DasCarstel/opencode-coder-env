# opencode-coder-env

Konfigurations-Repository für OpenCode in Coder Workspaces.

## Struktur

```
opencode-coder-env/
├── tiers.json                           # Model-Router Konfiguration
├── workspaces/
│   └── infrastructure/
│       ├── homeassistant/               # Home Assistant MCP-Projekt
│       │   ├── opencode.json
│       │   └── AGENTS.md
│       └── server-management/           # SSH-MCP Projekt
│           ├── opencode.json
│           ── AGENTS.md
├── coder-templates/
│   └── infrastructure/                         # Coder-Template
│       ├── main.tf
│       └── startup.sh
└── skills/
    └── infrastructure/                         # Infrastruktur-Skills
        ├── authentik/
        ├── docker-host/
        ├── home-assistant/
        └── google-home-exposure/
```

## Wichtige Hinweise

- **Keine Secrets in Git**: Keine Tokens, private SSH-Keys, Unseal-Shares oder `.env`-Dateien
- **Kein eigener MCP-Code**: Nur deklarative Konfiguration, keine `node_modules` oder Build-Schritte
- **Per-Projekt MCP-Trennung**: Jeder Herdr-Workspace hat nur die benötigten MCPs
- **OpenBao als Secret-Quelle**: Alle API-Keys werden zur Laufzeit aus OpenBao bezogen

## Secrets-Architektur

```
Coder Secret (OPENBAO_APPROLE_SECRET_ID)
  ↓ (wird in den Workspace injiziert)
startup.sh → AppRole-Auth bei OpenBao
  ↓ (kurzlebiger Token, 1h TTL)
OpenBao KV:
  - secret/data/mcp/homeassistant → HA_LLA_TOKEN
  - secret/data/mcp/authentik     → AUTHENTIK_API_KEY
  - secret/data/mcp/grafana       → GRAFANA_API_KEY
  - secret/data/mcp/opencloud     → OPENCLOUD_API_KEY
  ↓ (geschrieben nach /etc/opencode.env)
OpenCode MCPs nutzen die Umgebungsvariablen
```

**Keine Secrets in:**
-  Git Repository
- ❌ Lokale Dateien (außer bootstrap.env auf Docker Host)
-  Shell-History
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

- **Home Assistant** → OpenCode mit HA-MCP (27 Tools)
- **Server Management** → OpenCode mit SSH-MCP (14 Tools)

Beide OpenCode-Instanzen laufen parallel in separaten Herdr-Panes und
beenden sich nicht gegenseitig.

## Architektur (aktueller Stand)

- **Herdr** ist der Einstiegspunkt (Coder-App). Es verwaltet beide
  OpenCode-Sitzungen in einem persistenten Server.
- **Secrets** kommen zur Laufzeit aus OpenBao (AppRole `mcp-server`).
- **SSH** läuft über kurzlebige Zertifikate der OpenBao-SSH-CA
  (Rolle `host-access`, Principal `root`) – kein statischer Key.
- **Herdr-Binary** liegt auf dem Coder-Host unter `/opt/opencode-bin/herdr`
  und wird per Bind-Mount in den Workspace gemountet (GitHub ist aus dem
  Container nicht erreichbar).

### Voraussetzungen auf den Hosts

- Docker/Proxmox/TrueNAS vertrauen der OpenBao-CA:
  `TrustedUserCAKeys /etc/ssh/openbao-ca.pub`
  (Key muss dem aktuellen OpenBao-CA-Key entsprechen)

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

### MCP-Trennung

- **Home Assistant Workspace**: HA-MCP (27 Tools) + SSH-MCP (14 Tools)
- **Server Management Workspace**: nur SSH-MCP (14 Tools)

Die Trennung erfolgt über `.config/opencode/opencode.json` im jeweiligen
Workspace-Verzeichnis.

## Bestandsaufnahme (04.10.2026)

- OpenBao: unsealed, SSH CA konfiguriert, AppRole `mcp-server` vorhanden
- SSH-Rolle `host-access`: `allowed_users = root`
- HA `mcp_server` Integration aktiviert und getestet (v1.26.0)
- HA Token aus OpenBao (`secret/data/mcp/homeassistant` api_key)
- ssh-mcp v2.17.0 installiert und konfiguriert (`~/.config/ssh-mcp/config.toml`)
- OpenBao Policy erweitert (SSH-Signierung hinzugefügt)
- Coder-Infrastruktur: Docker-Container auf 10.0.10.17
