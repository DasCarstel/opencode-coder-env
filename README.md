# opencode-coder-env

Konfigurations-Repository für OpenCode in Coder Workspaces.

## Struktur

```
opencode-coder-env/
├── tiers.json                           # Model-Router Konfiguration
├── workspaces/
│   └── homelab/
│       ├── homeassistant/               # Home Assistant MCP-Projekt
│       │   ├── opencode.json
│       │   └── AGENTS.md
│       └── server-management/           # SSH-MCP Projekt
│           ├── opencode.json
│           ── AGENTS.md
├── coder-templates/
│   └── homelab/                         # Coder-Template
│       ├── main.tf
│       └── startup.sh
└── skills/
    └── homelab/                         # Infrastruktur-Skills
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
In der Coder-UI → Templates → `homelab` → Settings → Secrets:
- `openbao_approle_secret_id` = AppRole Secret-ID aus OpenBao

### 2. Template veröffentlichen
```bash
coder login --url https://coder.mueller-nas.de --token <dein-token>
coder templates push homelab --directory coder-templates/homelab --yes --org=coder
```

### 3. Workspace starten
Der Workspace cloned automatisch dieses Repo und holt die Secrets aus OpenBao.

### 4. OpenCode starten
```bash
opencode-ha    # Home Assistant Projekt
opencode-ssh   # Server Management Projekt
```

## Bestandsaufnahme (04.10.2026)

- OpenBao: unsealed, SSH CA konfiguriert, AppRole `mcp-server` vorhanden
- SSH-Rolle `host-access` existiert
- HA `mcp_server` Integration aktiviert und getestet (v1.26.0)
- HA Token aus OpenBao (`secret/data/mcp/homeassistant` api_key)
- ssh-mcp v2.17.0 installiert und konfiguriert (`~/.config/ssh-mcp/config.toml`)
- OpenBao Policy erweitert (SSH-Signierung hinzugefügt)
- Coder-Infrastruktur: Docker-Container auf 10.0.10.17
