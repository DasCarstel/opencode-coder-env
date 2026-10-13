# MCP-Server & Skills

## Konfig-Merge: global → Workspace

OpenCode liest die globale Basis und mergt die Workspace-Config darüber. MCP-Server
und Skills werden dabei **nicht** zusammengeführt, sondern **pro Workspace gesetzt**:
was in der Workspace-Config nicht steht, ist für den Agenten nicht sichtbar.

```
coder-templates/infrastructure/config/global-opencode.json   ← Basis (nur Plugin opencode-model-router, leere mcp.servers)
        +
workspaces/<bereich>/<space>/opencode.json                   ← MCPs + Skills + optional model/small_model
workspaces/<bereich>/workspace-global.json                   ← dito für general / minijob
```

Server-spezifische Details: [workspaces.md](workspaces.md#wie-die-trennung-funktioniert).

## MCP-Server im Überblick

| Server | Typ | Herkunft | Env / Auth | Verfügbar in |
|--------|-----|----------|------------|--------------|
| `homeassistant` | remote | `https://intern-homeassistant.mueller-nas.de/api/mcp` | `Authorization: Bearer {env:HA_LLA_TOKEN}` | homeassistant |
| `homeassistant-kleve` | remote | `http://192.168.178.52:8123/api/mcp` | `Authorization: Bearer {env:HA_KLEVE_LLA_TOKEN}` | cleve |
| `ocis` | local | `owncloud/ocis-mcp-server` v1.1.0 (Binary `/root/.local/bin/ocis-mcp-server`) | `OCIS_MCP_OCIS_URL`, `OCIS_MCP_APP_TOKEN_USER`, `OCIS_MCP_APP_TOKEN_VALUE` | server-management, homeassistant, svelte, general, minijob |
| `authentik` | local (via `uvx`) | Community-MCP `nikitatsym/authentik-mcp` | `AUTHENTIK_URL`, `AUTHENTIK_TOKEN` | server-management, svelte |
| `svelte` | remote | `https://mcp.svelte.dev/mcp` | – | svelte |
| `pdf` | local | `npx -y @sylphx/anymd@8.5.1` | – | general, minijob |
| `ssh-mcp` | local (stdio) | `ssh-mcp` v2.18.0, eigener Container-Pfad | statischer ed25519-Key aus OpenBao | server-management, homeassistant, svelte (**config-full**), cleve (**config-cleve**) |

### `ocis` (OpenCloud)

Lokal als Binär, per `versions.env` gepinnt auf **v1.1.0** und von
`coder-templates/shared/startup.sh` gegen die Release von `owncloud/ocis-mcp-server`
geladen. Es wird nicht per `npm`/`uvx` installiert, sondern als vorkompiliertes Binary
nach `/root/.local/bin/` gelegt (Stempel-Datei, damit der Download nur bei
Versionswechsel läuft).

Die drei Env-Namen sind fest vorgegeben und dürfen nicht umbenannt werden:

```
OCIS_MCP_OCIS_URL          → https://opencloud.mueller-nas.de
OCIS_MCP_APP_TOKEN_USER    → Service-Account
OCIS_MCP_APP_TOKEN_VALUE   → App-Token des Service-Accounts
```

Welcher Service-Account verwendet wird, entscheidet der Workspace (siehe
[oCIS-Guardrail](#ocis-guardrail)).

### `authentik`

Kein offizieller Authentik-MCP, sondern der Community-Server `nikitatsym/authentik-mcp`,
gestartet über `uvx` mit `--extra-index-url`:

```json
"command": ["/root/.local/bin/uvx", "--extra-index-url",
            "https://nikitatsym.github.io/authentik-mcp/simple",
            "authentik-mcp"]
```

`AUTHENTIK_URL` zeigt auf die Instanz, `AUTHENTIK_TOKEN` wird aus der
Umgebungsvariable `AUTHENTIK_API_KEY` durchgereicht (Startskript holt den Wert aus
OpenBao `secret/data/mcp/authentik`).

## `ssh-mcp`

`ssh-mcp` ist kein weiterer Community-Container, sondern eine im
**Workspace-Container selbst installierte CLI**, die OpenCode als lokalen
stdio-Server einbindet. Damit fällt der komplette Fernzugriff über eine einzige,
klar abgegrenzte Stelle — kein `ssh` in Bash-Befehlen, keine Credentials in
Prompt oder Kontext.

### Erzeugung

`coder-templates/shared/startup.sh` (Profil `infrastructure`) erzeugt bei jedem Start
die Konfiguration neu. Es ist die **einzige** Quelle: Es gibt keine
`config.toml` im Repo.

Ablauf:

1. Key aus OpenBao `secret/data/mcp/ssh-mcp` nach `/root/.ssh/id_ed25519_mcp`
   (statischer ed25519-Key; derselbe Key steht in `authorized_keys` der Zielhosts).
2. `uvx` installieren (für den authentik-MCP) und **`ssh-mcp` per npm**,
   gepinnt über `SSH_MCP_VERSION` aus `coder-templates/versions.env`
   (aktuell **2.18.0**), mit Stempel-Datei, damit ein bereits passender Stand
   nicht erneut installiert wird.
3. Drei TOML-Dateien unter `/root/.config/ssh-mcp/` schreiben, Verzeichnis `chmod 700`,
   Dateien `chmod 600`.
4. `/root/.ssh-mcp-transfers` anlegen (`chmod 700`) als `transferRoot` für SFTP.

Fehlt der Key in OpenBao, warnt das Skript und fährt ohne weiter — der Container startet
trotzdem, nur SSH-Zugriffe scheitern dann.

### Die drei Config-Dateien

| Datei | Default-Profil | Gruppen | Enthält |
|-------|----------------|---------|---------|
| `config.toml` | `docker-host` | `prod`, `kleve` | Krefeld- + Kleve-Profile |
| `config-full.toml` | `docker-host` | `prod`, `kleve` | identisch zu `config.toml` |
| `config-cleve.toml` | `cleve-docker` | nur `kleve` | **ausschließlich** Kleve-Profile |

`config.toml` und `config-full.toml` sind aktuell inhaltlich gleich: `config.toml` ist
die Basisdatei, `config-full.toml` der explizite Name, den die Workspaces
Server Management, Home Assistant und Svelte per `--config=` referenzieren. So kann die
Kleve-Variante später abweichen, ohne die Infra-Workspaces anzufassen.

### Profile

**Krefeld / Site-Prod** (Gruppe `prod`) — in `config.toml` und `config-full.toml`:

| Profil | Host | Zweck |
|--------|------|-------|
| `docker-host` | `10.0.10.10` | Docker Host (CT 100) |
| `proxmox` | `10.0.10.20` | Proxmox VE |
| `truenas` | `10.0.10.30` | TrueNAS (VM 200) |
| `pbs` | `10.0.10.21` | Proxmox Backup Server (CT 101) |
| `coder` | `10.0.10.17` | Coder-Host (CT 120) |
| `unifi` | `10.0.0.1` | UniFi Gateway (UCG-Ultra) |
| `media` | `10.0.10.25` | Media (CT 115) |
| `test` | `10.0.10.40` | Test (CT 105) |

**Kleve** (Gruppe `kleve`) — **nur** in `config-cleve.toml`:

| Profil | Host | Zweck |
|--------|------|-------|
| `cleve-proxmox` | `192.168.178.50` | Proxmox VE Kleve |
| `cleve-truenas` | `192.168.178.51` | TrueNAS Kleve |
| `cleve-docker` | `192.168.178.52` | Docker Host Kleve |

Erreichbar über Tailscale-Tunnel bzw. Subnet-Route.

### Policy

```toml
[defaults]
defaultProfile = "docker-host"     # config-cleve.toml: "cleve-docker"
approvalMode = "auto"
transferRoot = "/root/.ssh-mcp-transfers"

[policy.roleBindings.admin]
prod  = ["read-only", "safe", "destructive", "privileged"]
kleve = ["read-only", "safe", "destructive", "privileged"]

[[profiles]]
role = "admin"
approvalPolicy = "auto"
```

Erklärung:

- **`role = "admin"`** — jeder Host bekommt die volle Rolle; die eigentliche
  Beschränkung sitzt an der Rollen-Bindung der Gruppe.
- **Jede Befehlsklasse ist erlaubt** (`read-only`, `safe`, `destructive`,
  `privileged`), inklusive `sudo`. Das ist Absicht: ohne `privileged` wären
  Docker-, Proxmox- und Systemarbeiten nicht möglich.
- **Deshalb bleibt die eingebaute Forbidden-Liste des Servers aktiv** (`rm -rf /`,
  `mkfs`, Partitionieren o. ä.). Sie ist der Ersatz für eine Rollen-Differenzierung:
  nicht die Operation wird verboten, sondern die bekannten Unfallbefehle.
- **`approvalPolicy = "auto"`** — keine interaktive Rückfrage je Befehl. Für einen
  unbeaufsichtigten Container-Workflow ist eine Bestätigungsprompt, die niemand
  beantwortet, schlechter als die klare Regel: alles darf, außer was die
  Forbidden-Liste kennt.
- **`transferRoot`** begrenzt SFTP auf `/root/.ssh-mcp-transfers`, nicht auf das
  gesamte Dateisystem.

### Harte Site-Trennung

`config-cleve.toml` enthält **kein `prod`-Binding und keine Krefeld-Profile**. Ein
Cleve-Workspace kann also nicht auf `docker-host`, `proxmox` & Co. zugreifen — nicht
weil das unterbinden würde, sondern weil die Profile dort schlicht nicht existieren.
Die Site-Trennung ist damit eine Eigenschaft der Datei, nicht eine Regel, die jemand
einhalten muss.

Der Cleve-Workspace bindet `ssh-mcp` entsprechend mit
`--config=/root/.config/ssh-mcp/config-cleve.toml` an; die Infra-Workspaces nutzen
`config-full.toml`.

## oCIS-Guardrail

Zwei OpenCloud-Service-Accounts existieren:

| Account | Env-Paar | verwendet von |
|---------|----------|---------------|
| privat | `OCIS_PRIVATE_USER` / `OCIS_PRIVATE_TOKEN` | infrastructure, general |
| MuellerConnect | `OCIS_MUELLERCONNECT_USER` / `OCIS_MUELLERCONNECT_TOKEN` | muellerconnect (minijob) |

Beide stammen aus **einem** OpenBao-Secret `secret/data/mcp/opencloud-ocis` (Felder
`obsidian_private_*`, `obsidian_muellerconnect_*`). Es gibt also genau eine
Secret-Quelle, aber zwei getrennte Identitäten.

Der Guardrail wird in `shared/startup.sh` pro Profil durchgesetzt — und zwar aktiv,
nicht nur durch Weglassen:

```
infrastructure|general   →  set env OCIS_PRIVATE_*
                         →  unset env OCIS_MUELLERCONNECT_*
muellerconnect            →  set env OCIS_MUELLERCONNECT_*
                         →  unset env OCIS_PRIVATE_*
```

Das `unset` ist der eigentliche Schutz. Ein Workspace, der das falsche Paar
referenziert, bekommt nicht irgendein anderes gültiges Token, sondern gar keins —
der Aufruf schlägt sichtbar fehl, statt still in den falschen Space zu greifen.

**Ausnahme:** der **Hermes-Agent** — ein Host-Deployment auf dem Coder-Host, kein
Coder-Template (siehe [`hermes.md`](hermes.md)) — bekommt bewusst **beide** Paare
über seine vom Hestia-Panel gepflegte `.env` und definiert daraus zwei getrennte
MCP-Server (`ocis-private`, `ocis-muellerconnect`). Er ist der einzige Ort, an dem
beide Identitäten gleichzeitig ankommen — der OpenCode-Guardrail greift dort nicht.

## Skills-Architektur

### Generisch vs. Site-spezifisch

Die `docker-host*`-Skills (`docker-host`, `docker-host-compose`,
`docker-host-filesystem`, `docker-host-git`) sind **generisch** und liegen in
`skills/shared/` — sie werden von *allen* sechs Workspaces geladen. Was sie nicht
wissen, ist, *welcher* Host gemeint ist.

Das weiß der **Location-Skill `docker-host-location`**. Er existiert zweimal, mit
unterschiedlicher Zielauflösung:

| Skill-Ordner | geladen von | löst auf |
|--------------|-------------|----------|
| `skills/server/docker-host-location` | server-management, homeassistant, svelte | Krefeld → Profil `docker-host` |
| `skills/cleve/docker-host-location` | cleve | Kleve → Profil `cleve-docker` |

Wer `docker-host-compose` in Kleve lädt, bekommt automatisch das Kleve-Target, ohne
dass eine einzige Skill-Datei doppelt existieren muss. Das ist das einzige, was ein
Site-Skill jemals wissen muss — und der einzige Ort, an dem sich die beiden Sites
wirklich unterscheiden.

**Regel für neue Skills:** wenn eine Skill-Datei nur Inhalt beschreibt und keinen
Host nennt → `skills/shared/`. Wenn sie einen Host nennt → in den Site-Ordner.

### Lade-Matrix

| Skill-Ordner | server-mgmt | homeassistant | svelte | cleve | general | minijob |
|--------------|:-----------:|:------------:|:------:|:-----:|:-------:|:-------:|
| `shared` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `server` | ✅ | ✅ | ✅ | — | — | — |
| `cleve` | — | — | — | ✅ | — | — |
| `homeassistant` | — | ✅ | — | — | — | — |
| `obsidian` | ✅ | ✅ | ✅ | — | ✅ | — |
| `minijob` | — | — | — | — | — | ✅ |

Inhalt der Gruppen:

- `skills/shared/` — `docker-host`, `docker-host-compose`, `docker-host-filesystem`,
  `docker-host-git`, `create-skill`
- `skills/server/` — `authentik`, `authentik-integration`,
  `authentik-troubleshooting`, `docker-host-location` (→ Krefeld)
- `skills/cleve/` — `docker-host-location` (→ Kleve)
- `skills/homeassistant/` — `home-assistant`, `google-home-exposure`
- `skills/obsidian/` — `obsidian` (Vault-Zugriff)
- `skills/minijob/` — `obsidian` (Vault-Zugriff mit MuellerConnect-Zugang)

### Built-in-Skills

Unabhängig vom Repo liefert OpenCode selbst zwei Skills mit, die immer verfügbar sind:

- **`opencode`** — alles über OpenCode selbst: Provider, Modelle, MCP, Skills,
  Permissions, TUI, Server, SDK, Plugins, Beiträge zur OpenCode-Codebase.
- **`report`** — sammelt die Standard-Diagnostik und eröffnet ein GitHub-Issue über
  die GitHub-CLI.

Ein neuer Skill wird mit `create-skill` aus `skills/shared/` angelegt — inklusive
korrektem Frontmatter und Ordnerkonvention.