# Coder-Templates

Drei Templates, ein gemeinsames Setup-Skript. Jeder Container startet über
`entrypoint.sh`, das `coder-templates/shared/startup.sh <profil>` im Hintergrund
startet und danach den Coder-Agent-Bootstrap per `exec "$@"` ausführt.

> Der **Hermes-Agent** ist kein Coder-Template, sondern ein Host-Deployment auf
> dem Coder-Host — siehe [`hermes.md`](hermes.md).

## Die drei Templates

| Template | Netz | Image / Container | `startup.sh`-Profil |
|----------|------|-------------------|---------------------|
| `infrastructure` | `coder-infra` | `workspace-infrastructure:v2` | `infrastructure` |
| `general` | `coder-gen` | `workspace-general:v2` | `general` |
| `muellerconnect` | `coder-mc` | `workspace-muellerconnect:v2` | `muellerconnect` |

### `infrastructure`
HA-, Server-Management-, Svelte- und Cleve-Workspaces. `main.tf`-Besonderheiten:
zusätzliche Capabilities (`NET_ADMIN`) und `/dev/net/tun` für Tailscale, ein
eigenes persistentes Tailscale-State-Volume, Netzwerk `coder-infra`. Coder-App
„Infrastructure Workspace" → `herdr`.

### `general`
Isolierter Workspace: oCIS-MCP (privater Obsidian-Vault), PDF-MCP und `ssh-mcp` mit
**ausschließlich** dem Carsten-PC (`config-general.toml`). `main.tf`-Besonderheiten:
Netzwerk `coder-gen` (kein internes VLAN), eigener Session-Pfad
`/etc/opencode-data/<user>-general`. Der Container bekommt die Zugangsdaten über eine eigene,
minimal berechtigte AppRole `mcp-general`; Role-ID und Secret-ID kommen aus den Coder-Parametern
`openbao_role_id` und `openbao_approle_secret_id` (beide standardmäßig leer). Coder-App „General Workspace".

### `muellerconnect`
Wie `general`, aber Arbeits-Vault: Netzwerk `coder-mc`, eigener Session-Pfad
`/etc/opencode-data/<user>-mc`. Coder-App „MuellerConnect Workspace".

## Aufrufkette

```
Docker-Start
  └─ ENTRYPOINT /usr/local/bin/entrypoint.sh
       1. Grund-Tools + Node 22 (nur wenn fehlend)
       2. Repo klonen bzw. git fetch + reset --hard FETCH_HEAD
       3. nohup: coder-templates/shared/startup.sh <profil>
       4. exec "$@"  → Coder-Agent-Bootstrap aus main.tf
```

`general` und `muellerconnect` nutzen denselben Ablauf mit eigenem `PROFILE`.

## `shared/startup.sh` — Ablauf

Aufruf: `startup.sh <infrastructure|general|muellerconnect>` — ein anderes oder
fehlendes Profil bricht mit Fehler ab.

1. **Versions-Pins laden** — `source ../versions.env`; fehlt die Datei, werden alle
   Pins leer gesetzt (Blöcke warnen, laufende Version bleibt).
2. **OpenBao AppRole-Login** — `POST /v1/auth/approle/login` mit
   `OPENBAO_ROLE_ID` (Default `mcp-server`) + `OPENBAO_APPROLE_SECRET_ID`; Token
   landet in `/etc/opencode.env`.
3. **Secrets laden** — profilabhängig via `read_secret <pfad> <key> <VAR>`:
   - `infrastructure`: HA (Krefeld + Kleve), authentik, grafana, opencloud,
     opencloud-ocis (privat), tailscale
   - `general`: opencloud-ocis (privat)
   - `muellerconnect`: opencloud-ocis (Arbeits-Vault)
   - **alle Profile**: `opencode-go` `default1..3`, `hermes-opencode-service`
     → Passwort für den OpenCode-Server
   Der OpenBao-Token selbst wird nicht weitergereicht.
4. **Git-Credentials** — `scripts/setup-git-credentials.sh` (GitHub-PAT aus OpenBao).
5. **SSH + ssh-mcp** *(`infrastructure`; `general` nur ssh-mcp mit PC-Config)* — ed25519-Key erzeugen, per
   `ssh/sign/host-access` (24 h) signieren, `~/.ssh/config` schreiben; statischen
   ssh-mcp-Key aus OpenBao installieren; `uvx` (für authentik) und **`ssh-mcp` per
   npm** installieren (Stempel-Datei, idempotent); `config.toml`,
   `config-full.toml` (Krefeld+Kleve+Carsten-PC)
   und `config-cleve.toml` (nur Kleve) unter `/root/.config/ssh-mcp/` erzeugen.
   Für `general` läuft ein eigener Abschnitt: `ssh-mcp` installieren, den exklusiven
   PC-Key `secret/data/mcp/ssh-mcp-pc` nach `/root/.ssh/id_ed25519_mcp_pc` schreiben und
   ausschließlich `config-general.toml` (Profil `carsten-pc`) erzeugen.
6. **Tailscale** *(nur `infrastructure`)* — `tailscaled` starten, `tailscale up`
   mit Auth-Key, `--accept-routes`, `--ssh=false`.
7. **OpenCode-CLI** — Abgleich gegen npm-latest, ggf. Neuinstallation + Service-Neustart
   (bewusst ungepinnt).
8. **Globale MCP-Config** — profilabhängig installiert:
   `infrastructure` → `coder-templates/infrastructure/config/global-opencode.json`
   (bewusst **leer**, MCP-Trennung läuft über die Projekt-Configs),
   `general`/`muellerconnect` → `workspaces/{general,minijob}/workspace-global.json`.
   Zusätzlich werden die Model-Router-Overrides global abgelegt.
9. **Env-Injektion** *(nur `infrastructure`)* — `HA_LLA_TOKEN`,
   `HA_KLEVE_LLA_TOKEN`, `AUTHENTIK_API_KEY` per `opencode service set env`.
10. **`oc-go`-Helper** — wird nach `/usr/local/bin/oc-go` geschrieben. Der aktive
    Key kommt aus der SOT `.opencode/hestia-accounts.jsonc` (Feld `active`);
    `OPENCODE_API_KEY` wird dedupliziert in `.bashrc` und ins (ephemere)
    Service-Env gesetzt. Siehe [`secrets.md`](secrets.md).
11. **Primary-Agents** — `derive-agents.py` (Details unten).
12. **OpenCode-Server** — `hostname 0.0.0.0`, Port **49374**, Passwort aus
    `hermes-opencode-service` (Fleet-Zugriff für Hestia/Hermes), Service-Restart.
13. **`ocis-mcp-server`** — gepinntes Go-Binary aus GitHub Releases nach
    `/root/.local/bin/ocis-mcp-server` (Stempel-Datei).
14. **oCIS-Guardrail** — das eigene Profil-Token wird ins Service-Env gesetzt, das
    jeweils andere aktiv **entfernt** (`opencode service unset env`). Ein
    Infrastructure-/General-Workspace kann den Arbeits-Vault physisch nicht sehen.
15. **Herdr** — Install auf Pin: Host-Bind-Mount `/opt/opencode-bin/herdr` bzw.
    Download von `herdr.dev`, Version wird verifiziert.
16. **Herdr-Workspaces** — Server starten, Workspaces idempotent anlegen und je
    einen OpenCode-Agenten starten:

    | Profil | Workspaces (Label → Verzeichnis) |
    |--------|----------------------------------|
    | `infrastructure` | Home Assistant, Server Management, Svelte, Cleve |
    | `general` | General |
    | `muellerconnect` | MuellerConnect |

17. **`ha-forward.py`** — startet bei Bedarf das HA-Kleve-TCP-Relay
    (`127.0.0.1:18123` → HA Kleve), damit Container ohne Tailscale den
    HA-MCP-Endpunkt erreichen. Läuft das Relay schon, wird es nicht neu gestartet.

## Die drei Profile im Vergleich

| Aspekt | `infrastructure` | `general` | `muellerconnect` |
|--------|------------------|-----------|------------------|
| Netz | `coder-infra` | `coder-gen` | `coder-mc` |
| SSH/ssh-mcp | ✅ | ✅ (nur Carsten-PC) | ❌ |
| Tailscale | ✅ | ❌ | ❌ |
| oCIS-Vault | privat | privat | Arbeit (MuellerConnect) |
| Extra-Secrets | HA, authentik, grafana, opencloud, tailscale | `ssh-mcp-pc` | — |
| Herdr-Workspaces | HA, Server Mgmt, Svelte, Cleve | General | MuellerConnect |

`muellerconnect` ist vollständig abgeschnitten: kein `ssh-mcp`, keine SSH-Keys, kein
internes VLAN. `general` ist ebenfalls abgeschnitten, mit einer Ausnahme: Es hat `ssh-mcp`
mit **nur** dem Carsten-PC (`config-general.toml`, exklusiver Key). Kein internes VLAN.

## `derive-agents.py`

Quelle der Wahrheit ist `.opencode/hestia-primary-agents.json` (SOT, vom
Hestia-Panel gepflegt, ein Commit):

```json
{ "build": { "model": "…", "variant": "" },
  "plan":  { "model": "…", "variant": "max" } }
```

Das Skript merged die **effektiven** Modelle nach `agents` in
`/root/.config/opencode/opencode.json` — nur das Feld `model`, die System-Prompts
der Built-ins bleiben unangetastet. Fehlt die Datei, wird aus `@medium`
abgeleitet (Standardvariante für `build`, höchste Variante für `plan`), wobei die
Varianten-Liste vom laufenden OpenCode-Server (`/api/model`) kommt.

## `ha-forward.py`

Ein asyncio-TCP-Relay: `[LISTEN_PORT] [TARGET_HOST] [TARGET_PORT]`, Standard
`18123` → HA Kleve. Zweck: der Infrastructure-Container darf HA Kleve (Tailscale)
nur über das Tailnet erreichen; Container ohne Tailscale — vor allem `hermes` —
brauchen diesen Relay-Weg.

## Versions-Pins (`versions.env`)

```ini
SSH_MCP_VERSION=2.18.0
OCIS_MCP_VERSION=1.1.0
HERDR_VERSION=0.9.3
```

Die **OpenCode-CLI ist bewusst nicht gepinnt** (Hybrid, siehe
[`architecture.md`](architecture.md)). Update-Ablauf: eine Version in
`versions.env` anpassen, committen, pushen — beim nächsten Workspace-Start ziehen
alle Container automatisch und identisch nach. Veraltete Pins zeigt
`scripts/check-updates.sh` (read-only).

## Template veröffentlichen

```bash
coder login --url https://coder.mueller-nas.de --token <token>
coder templates push <name> --directory coder-templates/<name> --yes --org=coder
```

`<name>` = `infrastructure` | `general` | `muellerconnect`.
Das Image wird lokal aus dem jeweiligen `Dockerfile` gebaut; der `CACHE_DATE`
Build-Arg erzwingt den Rebuild beim Push.