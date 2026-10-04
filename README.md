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

### 1. Coder Secrets eintragen
In der Coder-Template-Konfiguration folgende Secrets setzen:
- `OPENBAO_APPROLE_SECRET_ID`: Neue Secret-ID (lokal in `/tmp/new-approle-secret-id.txt` auf WSL)

⚠️ **WICHTIG:** Die Secret-ID muss nach dem Eintragen in Coder gelöscht werden!
```bash
# Nach dem Eintragen in Coder:
rm /tmp/new-approle-secret-id.txt
```

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

## TODO

- [ ] HA Umgebungsvariable `HA_LLA_TOKEN` setzen (Coder Secret oder lokal)
- [ ] AppRole Secret-ID in Coder Secrets eintragen (`OPENBAO_APPROLE_SECRET_ID`)
- [ ] ssh-mcp Canary-Test über OpenCode durchführen
- [ ] Coder-Template testen
- [ ] Repo auf GitHub pushen (Repo muss erstellt werden)
