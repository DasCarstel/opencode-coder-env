# Secrets

Alle Zugangsdaten kommen zur Laufzeit aus **OpenBao**; im Repository steht kein
einziger Secret-Wert.

## Fluss

```
Coder-Parameter "openbao_approle_secret_id"  (main.tf, mutable)
  │  injiziert als Env
  ▼
OPENBAO_APPROLE_SECRET_ID  (+ OPENBAO_ROLE_ID = mcp-server)
  │  coder-templates/shared/startup.sh → POST /v1/auth/approle/login
  ▼
kurzlebiger Client-Token (BAO_TOKEN, nur in /etc/opencode.env)
  │  read_secret <pfad> <key> <VAR>  → curl + X-Vault-Token
  ▼
OpenBao KV v2:  secret/data/mcp/*
  ├── export VARIABLE=…           (Shell / start.sh-Phase)
  ├── /etc/opencode.env           (0600, pro Start neu)
  ├── /root/.ssh/id_ed25519_mcp   (statischer ssh-mcp-Key)
  └── opencode service set env …  (EPHEMER → bei jedem Container-Start neu)
       │
       ▼
MCP-Server lesen die Werte aus der Umgebung:  {env:AUTHENTIK_API_KEY}
```

Der Token wird nach dem Secret-Laden nicht weitergegeben; `startup.sh` legt ihn
ausschließlich in `/etc/opencode.env` (Mode 0600) ab.

## Secret-Pfade in OpenBao

| Pfad | Geladene Variablen |
|------|--------------------|
| `secret/data/mcp/homeassistant` | `HA_LLA_TOKEN` (`api_key`) |
| `secret/data/mcp/homeassistant-kleve` | `HA_KLEVE_LLA_TOKEN` (`api_key`) |
| `secret/data/mcp/authentik` | `AUTHENTIK_API_KEY` (`api_key`) |
| `secret/data/mcp/grafana` | `GRAFANA_API_KEY` (`api_key`) |
| `secret/data/mcp/opencloud` | `OPENCLOUD_API_KEY`, `OPENCLOUD_USERNAME` |
| `secret/data/mcp/opencloud-ocis` | `OCIS_PRIVATE_USER/TOKEN`, `OCIS_MUELLERCONNECT_USER/TOKEN` |
| `secret/data/mcp/opencode-go` | `OC_GO_DEFAULT1..3` — **nur diese drei Felder** |
| `secret/data/mcp/ssh-mcp` | statischer ed25519-Key → `/root/.ssh/id_ed25519_mcp` |
| `secret/data/mcp/tailscale` | `TS_AUTHKEY` |
| `secret/data/mcp/github` | GitHub-PAT (via `scripts/setup-git-credentials.sh`) |
| `secret/data/mcp/hermes-opencode-service` | `OPENCODE_SERVER_PASSWORD` |
| `secret/data/mcp/artificialanalysis` | `AA_API_KEY` (Artificial Analysis, **optional** — ohne Key laufen die Benchmarks nur über OpenRouter) |

> **Hinweis zu `opencode-go`:** Der Pfad enthält ausschließlich die drei Keys
> `default1`, `default2`, `default3`. Das frühere Feld `active` wird weder
> gelesen noch geschrieben — die aktive Konto-Wahl lebt im Repo (siehe unten).

**AppRoles** (Details in [`OPENBAO.md`](../OPENBAO.md)): `mcp-server` für die
Coder-Workspaces (`secret/data/mcp/*` lesen, `ssh/sign/host-access`),
`host-access` nur für die SSH-CA-Signierung. Die Host-/SSH-Credentials der
Zielmaschinen selbst sind nicht Teil dieses Flusses; sie liegen in
`authorized_keys` bzw. `TrustedUserCAKeys` auf den Hosts.

## Keine Secrets in

- ❌ Git-Repository (inkl. History — keine Secrets mehr committen, wenn welche drin sind)
- ❌ lokale Dateien außer der bewussten Ausnahme `bootstrap.env` auf dem Docker-Host
- ❌ Shell-History
- ❌ Container-Logs

Ausnahme im Container: `/etc/opencode.env`, `/root/.ssh/*` und
`~/.config/opencode/service.json` — alle mit restriktiven Rechten und alle
ephemer bzw. aus OpenBao befüllt.

## `oc-go`-Helper

`startup.sh` installiert den Helper nach `/usr/local/bin/oc-go`.

| Befehl | Wirkung |
|--------|---------|
| `oc-go list` | zeigt die im Container verfügbaren Keys (`default1..3`) und welcher **lokal** als `OPENCODE_API_KEY` gesetzt ist (nur Key-Länge, nie der Wert) |
| `oc-go use default1\|default2\|default3` | setzt den Key **in diesem Container**: `.bashrc` (dedupliziert) + OpenCode-Service-Env |

- Die **Keys** kommen aus OpenBao (`secret/data/mcp/opencode-go`).
- Der **aktive Account** kommt aus der SOT `.opencode/hestia-accounts.jsonc`
  (Feld `active`, gepflegt vom Hestia-Panel). `startup.sh` liest dieses Feld
  beim Start und setzt den passenden Key; ohne Repo-Checkout greift `default1`.
- `oc-go use` ist **lokal und temporär** — dauerhaft umschalten nur über das
  Hestia-Panel, damit alle Container synchron bleiben.
- **`oc-go default` existiert nicht.** Der Aufruf bricht mit einer
  entsprechenden Meldung ab.

## Weiterführend

- [`../OPENBAO.md`](../OPENBAO.md) — Policies, AppRoles, Secret-Pfade, SSH-CA,
  Bootstrap (`scripts/bootstrap-hosts.sh`), neues Secret-ID ausstellen
- [`hestia/ci/setup/README.md`](../hestia/ci/setup/README.md) — AppRole und
  GitHub-Secrets für das Hestia-Panel
- [`coder-templates.md`](coder-templates.md) — wo die Env-Injektion und das
  oCIS-Guardrail in `startup.sh` passieren