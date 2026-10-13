# opencode-coder-env

Konfigurations-Repository für die OpenCode-Fleet in Coder Workspaces: Coder-Templates,
MCP- und Skill-Konfiguration, Hestia-SOT-Dateien und die Betriebs-Dokumentation.

## Dokumentation

| Datei | Inhalt |
|-------|--------|
| [`docs/architecture.md`](docs/architecture.md) | Systemüberblick: Coder-Host, Netzwerke, Herdr, Secret-Flow, Persistenz, Update-Verhalten |
| [`docs/coder-templates.md`](docs/coder-templates.md) | Die drei Coder-Templates, Aufrufkette und der Ablauf von `shared/startup.sh` |
| [`docs/hermes.md`](docs/hermes.md) | Hermes-Agent als Host-Deployment: Compose, Bind-Mounts, Laufzeit-Zustand, Deploy |
| [`docs/workspaces.md`](docs/workspaces.md) | Workspaces mit MCP-Servern, Skills und Projekt-Configs |
| [`docs/mcp-skills.md`](docs/mcp-skills.md) | MCP-Server im Detail (ssh-mcp, authentik, oCIS …) und die Skills-Architektur |
| [`docs/secrets.md`](docs/secrets.md) | Secret-Fluss OpenBao → Container, Pfadliste, `oc-go`-Helper |
| [`docs/hestia.md`](docs/hestia.md) | Hestia — OpenCode Model Panel, SOT-Dateien, CI/CD |

## Projektstruktur

```
opencode-coder-env/
├── README.md                  # ← diese Datei
├── OPENBAO.md                 # OpenBao-Policies, -AppRoles, -Secrets, SSH-CA
├── .gitignore
├── .opencode/                 # SOT-Dateien (Hestia-managed)
│   ├── opencode-model-router.overrides.jsonc   # Tier-Anbindung @fast/@medium/@heavy
│   ├── hestia-accounts.jsonc                   # OpenCode-Go-Konten + Feld "active"
│   ├── hestia-primary-agents.json              # build/plan-Modelle
│   ├── hestia-fallbacks.json                  # bezahlte Ausfall-Modelle
│   ├── hestia-failover.jsonc                  # Ausfallschutz-Einstellungen
│   ├── hestia-suggest.jsonc                   # KI-Vorschlag-Automatik
│   └── hestia-hermes.jsonc                     # Hermes-Steuerung
├── docs/                      # diese Dokumentation
├── coder-templates/
│   ├── versions.env           # Versions-Pins (Hybrid: OpenCode ungepinnt)
│   ├── shared/                # startup.sh, derive-agents.py, ha-forward.py
│   ├── infrastructure/        # HA, Server-Management, Svelte, Cleve (coder-infra)
│   ├── general/               # isoliert (coder-gen)
│   ├── muellerconnect/        # isoliert (coder-mc)
│   └── (kein hermes-Template — der Hermes-Agent ist ein Host-Deployment)
├── hermes/                    # Host-Compose des Hermes-Agenten (Coder-Host 10.0.10.17)
├── workspaces/
│   ├── infrastructure/{homeassistant,server-management,svelte,cleve}/
│   ├── general/
│   └── minijob/
├── skills/{homeassistant,obsidian,server,cleve,minijob,shared}/
├── hestia/
│   ├── backend/               # Python-Stdlib-Backend
│   ├── frontend/              # Svelte-Source + dist/
│   ├── ci/                    # validate.py, rollout.sh, setup/, check-oc-credentials.sh
│   └── skills/                # model-curator (Hermes-Skill)
├── scripts/                   # bootstrap-hosts.sh, check-updates.sh, setup-git-credentials.sh
└── .github/workflows/         # validate + rollout + deploy + Modell-/Hermes-Steuerung
```

> Das Setup-Skript liegt **ausschließlich** in `coder-templates/shared/startup.sh`;
> die drei Wrapper-Templates `infrastructure`, `general` und `muellerconnect`
> rufen es mit ihrem Profil auf.

## Grundsätze

- **Keine Secrets in Git** — Tokens, private Keys und `.env`-Dateien gehören in
  OpenBao, nicht ins Repository.
- **Deklarative Konfiguration** — nur JSON/TOML/Terraform/Bash-Setup, kein eigener
  MCP-Code, keine `node_modules`, keine Build-Schritte für MCP-Server.
- **Per-Workspace-Trennung** — jeder Herdr-Workspace bekommt nur die MCP-Server und
  Skills, die er braucht (globale Config enthält bewusst keine MCP-Server).
- **OpenBao als Secret-Quelle** — alle Zugangsdaten werden zur Laufzeit über die
  AppRole `mcp-server` bezogen.
- **OpenCode V2 merged Configs** — `global → Workspace`: `~/.config/opencode/opencode.json`
  enthält das Profil, die Projekt-`opencode.json` im Workspace ergänzt es.

## Erste Schritte

1. **Coder-Secret setzen** — in der Coder-UI beim jeweiligen Template unter
   *Settings → Secrets* den Parameter `openbao_approle_secret_id` auf die
   AppRole-Secret-ID aus OpenBao setzen.
2. **Template veröffentlichen**:

   ```bash
   coder templates push <name> --directory coder-templates/<name> --yes --org=coder
   ```

   `<name>` ist `infrastructure`, `general` oder `muellerconnect`.
3. **Workspace starten** — der Container klont das Repo, holt die Secrets aus
   OpenBao und startet Herdr mit den Workspaces des Profils.
   Details: [`docs/coder-templates.md`](docs/coder-templates.md).

> Der **Hermes-Agent** ist kein Template, sondern ein Host-Deployment unter
> [`hermes/`](hermes/) auf dem Coder-Host; er wird über
> `.github/workflows/deploy-hermes.yml` ausgerollt und von Hestia konfiguriert.
> Details: [`docs/hermes.md`](docs/hermes.md).

## Weitere Doku

- [`OPENBAO.md`](OPENBAO.md) — Policies, AppRoles, Secret-Pfade, SSH-CA, Bootstrap
- [`hestia/ci/setup/README.md`](hestia/ci/setup/README.md) — One-Time-Setup für Hestia
  (GitHub-Secrets, Runner, OpenBao-AppRole)
- Ausführliche Betriebs- und Projektdokumentation liegt im privaten Obsidian-Vault
  (Projekt **PRO-302**).