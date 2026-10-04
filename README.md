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
│           └── AGENTS.md
├── coder-templates/
│   └── homelab/                         # Coder-Template
│       └── main.tf
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

## Secrets-Verwaltung

Secrets werden **nicht** im Repo gespeichert. Stattdessen:

| Secret | Ort |
|--------|-----|
| HA API-Key | Coder Secret `HA_LLA_TOKEN` (wird als Umgebungsvariable in den Workspace injiziert) |
| OpenBao Root-Token | `/opt/docker-compose/openbao/bootstrap.env` auf Docker Host (nicht im Git) |

OpenBao wird **manuell über die Authentik-OIDC-UI** administriert (`https://openbao.mueller-nas.de`). Keine automatisierten Rotationen über CLI.

## Setup in Coder Workspace

### 1. Coder Secret setzen
In der Coder-UI → Templates → `homelab` → Settings → Secrets:
- `HA_LLA_TOKEN` = Home Assistant Long-Lived Access Token

### 2. Template veröffentlichen
```bash
# Von einem Rechner mit Coder CLI:
coder templates push homelab --directory coder-templates/homelab --yes
```

### 3. Workspace starten
Der Workspace cloned automatisch dieses Repo beim Start.

## Bestandsaufnahme (04.10.2026)

- OpenBao: unsealed, SSH CA konfiguriert, AppRole `mcp-server` vorhanden
- SSH-Rolle `host-access` existiert
- HA `mcp_server` Integration aktiviert und getestet (v1.26.0)
- HA Token aus OpenBao (`secret/data/mcp/homeassistant` api_key)
- ssh-mcp v2.17.0 installiert und konfiguriert (`~/.config/ssh-mcp/config.toml`)
- OpenBao Policy erweitert (SSH-Signierung hinzugefügt)
- Coder-Infrastruktur: Docker-Container auf 10.0.10.17

## Offene Punkte

- [ ] Coder CLI auf dem Coder-Host installieren
- [ ] Workspace-Provisionierung klären (Docker? Proxmox?)
- [ ] HA-Token als Coder Secret eintragen
- [ ] Template veröffentlichen und testen
