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

```bash
# Repo klonen
git clone <repo-url> ~/opencode-coder-env

# Symlinks setzen (Beispiel)
ln -sf ~/opencode-coder-env/tiers.json ~/.cache/opencode/opencode-model-router/tiers.json

# Herdr Integration installieren
herdr integration install opencode

# OpenCode aus Projekt-Verzeichnis starten
cd ~/opencode-coder-env/workspaces/homelab/homeassistant
opencode
```

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
