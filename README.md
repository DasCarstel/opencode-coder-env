# opencode-coder-env

Konfigurations-Repository für OpenCode in Coder Workspaces.

## Struktur

```
opencode-coder-env/
├── tiers.json                           # Model-Router Konfiguration
── workspaces/
│   └── homelab/
│       ├── homeassistant/               # Home Assistant MCP-Projekt
│       │   ├── opencode.json
│       │   └── AGENTS.md
│       └── server-management/           # SSH-MCP Projekt
│           ├── opencode.json
│           └── AGENTS.md
├── coder-templates/
│   └── homelab/                         # Coder-Template (TODO)
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
- **Per-Projekt MCP-Trennung**: Jeder Herdr-Workspace hat nur seine benötigten MCPs
- **Minijob ausgeschlossen**: Dieser Branch enthält nur Infrastruktur-Konfiguration

## Setup in Coder Workspace

### 1. AppRole Secret-ID rotieren (manuell über OpenBao UI)

**Problem:** Die Secret-ID-Rotation über CLI schlägt mit 403 fehl (vermutlich Berechtigungsproblem mit dem Token in `bootstrap.env`).

**Lösung:** Secret-ID manuell über die OpenBao UI rotieren:

1. Öffne `https://openbao.mueller-nas.de` im Browser
2. Logge dich ein mit dem Root-Token aus `/opt/docker-compose/openbao/bootstrap.env`
3. Gehe zu **Access** → **approle** → **mcp-server**
4. Klicke auf **Create secret ID** (oder lösche die alte und erstelle eine neue)
5. Kopiere die neue Secret-ID
6. Trage sie in Coder Secrets ein als `OPENBAO_APPROLE_SECRET_ID`

⚠️ **WICHTIG:** Die alte Secret-ID (`03a91ad8-23d7-a0e9-6138-aaeb8fdab91c`) wurde im Chat offengelegt und sollte widerrufen werden!

### 2. Workspace starten
Das Startup-Script läuft automatisch und:
1. Authentisiert sich bei OpenBao mit AppRole
2. Holt einen kurzlebigen Token
3. Liest den HA API Key aus OpenBao
4. Setzt `HA_LLA_TOKEN` als Umgebungsvariable (nur im RAM)
5. Startet OpenCode mit der HA-Integration

### 3. Manueller Start
```bash
# Alias verwenden
open-ha

# Oder direkt
~/opencode-coder-env/coder-templates/homelab/startup.sh
```

## Sicherheitsarchitektur

```
Coder Workspace
  ↓ (OPENBAO_APPROLE_SECRET_ID aus Coder Secrets)
OpenBao AppRole-Auth
  ↓ (kurzlebiger Token, 1h TTL)
OpenBao KV: secret/data/mcp/homeassistant
  ↓ (api_key gelesen)
HA_LLA_TOKEN Umgebungsvariable (nur im RAM)
  ↓
OpenCode → HA MCP-Server
```

**Keine Secrets in:**
-  Git Repository
- ❌ Lokale Dateien (außer bootstrap.env auf Docker Host)
- ❌ Shell-History
- ❌ Logs

**Nur in:**
- ✅ Coder Secrets (OPENBAO_APPROLE_SECRET_ID)
- ✅ RAM während der Session
- ✅ Docker Host: bootstrap.env (Root-Token)

## Bestandsaufnahme (04.10.2026)

- OpenBao: unsealed, SSH CA konfiguriert, AppRole `mcp-server` vorhanden
- SSH-Rolle `host-access` existiert
- ✅ HA `mcp_server` Integration aktiviert und getestet (v1.26.0)
- ✅ HA Token aus OpenBao (`secret/data/mcp/homeassistant` api_key)
- ✅ ssh-mcp v2.17.0 installiert und konfiguriert
- ✅ OpenBao Policy erweitert (SSH-Signierung hinzugefügt)

## Offene Probleme

### AppRole Secret-ID Rotation
Die Secret-ID-Rotation über CLI schlägt mit 403 permission denied fehl, obwohl der Root-Token verwendet wird. Mögliche Ursachen:
- Token in `bootstrap.env` ist nicht der echte Root-Token (nur Token mit "root" Policy)
- Root Policy wurde modifiziert und beschränkt AppRole-Operationen
- OpenBao-Konfiguration verhindert Secret-ID-Operationen

**Workaround:** Secret-ID manuell über OpenBao UI rotieren (siehe Setup-Anleitung oben).

### HA MCP-Server 401
Der HA MCP-Server gab initial 401 zurück. Lösung:
- `Accept: application/json` Header hinzufügen
- Token aus OpenBao (`secret/data/mcp/homeassistant` → `api_key`) verwenden
- Erfolgreich getestet mit HA v1.26.0
