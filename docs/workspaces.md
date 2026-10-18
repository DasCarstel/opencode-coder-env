# Workspaces

Die Umgebung besteht aus **sechs OpenCode-Workspaces**. Jeder bekommt ein eigenes
Verzeichnis, eine eigene Konfigurationsdatei und damit eine eigene MCP-/Skill-Sicht.

| # | Workspace | Konfig | Zweck | Netzwerk | MCP-Server | Skills |
|---|-----------|--------|-------|----------|------------|--------|
| 1 | `infrastructure/server-management` | `workspaces/infrastructure/server-management/opencode.json` | Infrastruktur-Host-Verwaltung (Docker, Proxmox, TrueNAS, PBS, Coder, UniFi), Authentik, Obsidian-Vault | internes VLAN `10.0.10.x` (+ `10.0.0.1`), SSH-Key | `ssh-mcp` (config-full), `authentik`, `ocis` | `shared`, `server`, `obsidian` |
| 2 | `infrastructure/homeassistant` | `workspaces/infrastructure/homeassistant/opencode.json` | Home Assistant Krefeld + Obsidian | internes VLAN, SSH-Key | `ssh-mcp` (config-full), `homeassistant`, `ocis` | `homeassistant`, `server`, `shared`, `obsidian` |
| 3 | `infrastructure/svelte` | `workspaces/infrastructure/svelte/opencode.json` | Svelte/SvelteKit-Entwicklung + denselben Infra-Kontext | internes VLAN, SSH-Key | `ssh-mcp` (config-full), `authentik`, `ocis`, `svelte` | `shared`, `server`, `obsidian` |
| 4 | `infrastructure/cleve` | `workspaces/infrastructure/cleve/opencode.json` | Zweite Site Kleve (eigener Proxmox, TrueNAS, Docker-Host) | Kleve-Subnetz `192.168.178.x` (Tailscale-Tunnel/Subnet-Route) | `ssh-mcp` (**config-cleve**), `homeassistant-kleve` | `shared`, `cleve` |
| 5 | `general` | `workspaces/general/workspace-global.json` | Allgemeine Aufgaben ohne Site-Bindung | **kein internes VLAN**; nur Carsten-PC (`10.0.0.20`) per NAT erreichbar, eigener Key | `ocis`, `pdf`, `ssh-mcp` (**config-general**, nur Carsten-PC) | `shared`, `general`, `obsidian` |
| 6 | `minijob` | `workspaces/minijob/workspace-global.json` | MuellerConnect-Space (externer Kunde) | öffentlich, **kein internes VLAN**, kein SSH-Key | `ocis`, `pdf` | `shared`, `minijob` |

Die vier Workspaces 1–4 liegen unter `workspaces/infrastructure/<space>/` und bringen
je `opencode.json` **und** `AGENTS.md` mit (projektspezifische Arbeitsanweisungen für
den Agenten). General und minijob sind **globale** Configs: `workspace-global.json`
im jeweiligen Verzeichnis.

## Kurzbeschreibung je Workspace

### 1. Server Management
Der Standard-Workspace für alle Aufgaben, die auf die Krefeld-Hosts gehen:
Container, Compose-Dateien unter `/etc/docker` und `/opt/docker-compose`, Git auf dem
`/etc/docker`-Repo, Proxmox VE, TrueNAS, Proxmox Backup Server, der Coder-Host und das
UniFi-Gateway. Zusätzlich Authentik (Apps, Flows, Policies, SSO-Diagnose) und der
Obsidian-Vault als Ablage. Details: [mcp-skills.md](mcp-skills.md).

### 2. Home Assistant
Automatisierung der Krefeld-Instanz (Entities, Automationen, Szenen, Aufrufe über den
LLA-Endpunkt) plus der volle Infra-Zugriff aus Workspace 1 — praktisch dieselben MCPs
mit zusätzlich `homeassistant`.

### 3. Svelte
Entwicklung an der Svelte-/SvelteKit-Oberfläche. Technisch identisch zu
Server Management, ergänzt um den **offiziellen Svelte-MCP** (`mcp.svelte.dev`) für
Dokumentation und Best Practices. Die Infra-MCPs bleiben, weil Backend-Änderungen fast
immer ein Rollout auf den Hosts nach sich ziehen.

### 4. Cleve
Die **zweite Site**. Sie bekommt bewusst einen eigenen, abgeschotteten
`ssh-mcp`-Mount: `opencode.json` startet `ssh-mcp --config=/root/.config/ssh-mcp/config-cleve.toml`,
und diese Datei enthält **ausschließlich Kleve-Profile**. Krefeld-Hosts sind dort nicht
definiert — eine harte Trennlinie, keine Konvention. Statt `homeassistant` gibt es
`homeassistant-kleve`; Skills kommen aus `shared` + `cleve`.

Zusätzlich setzt der Cleve-Workspace **`model` und `small_model` explizit**
(aktuell `opencode-go/glm-5.3-flash` als Session-Modell und
`opencode-go/longcat-2.5-preview-free` als Small-Model), weil er sich nicht aus
dem Router-Tier ableiten soll.

### 5. General
Allgemeiner Workspace ohne Site-Bezug. Er hat **`ssh-mcp` mit genau einem Profil**:
`carsten-pc` (Windows, `10.0.0.20`) über `--config=/root/.config/ssh-mcp/config-general.toml`.
Der Schlüssel ist exklusiv (`secret/data/mcp/ssh-mcp-pc`), und der Workspace läuft mit der
minimal berechtigten AppRole `mcp-general` (Policy `mcp-server-general`) statt der
gemeinsamen `mcp-server`. Krefeld- und Kleve-Hosts bleiben unerreichbar: deren Profile
existieren in dieser Config nicht und das Krefelder/Kleve-VLAN ist nicht angebunden. Verfügbar
sind außerdem OpenCloud (privater Space) und ein PDF-MCP.

### 6. minijob (MuellerConnect)
Der Workspace des externen Kunden-Spaces. Vollständig abgeschnitten: kein SSH-Zugang
(anders als General, das den Carsten-PC erreicht), kein internes Netz. Zusätzlich zum privaten oCIS-Zugriff hat er den **MuellerConnect-
Service-Account**; siehe [oCIS-Guardrail](mcp-skills.md#ocis-guardrail). Skills kommen
aus `shared` + `minijob`.

## Globale Basiskonfiguration

`coder-templates/infrastructure/config/global-opencode.json` ist die Basis, die alle
Container bekommen:

```json
{
  "plugins": ["opencode-model-router", "@tarquinen/opencode-dcp@3.2.0"],
  "mcp": { "servers": {} }
}
```

Sie enthält **ausschließlich die Plugins** (Model-Router + DCP) — bewusst keine
MCP-Server und keine Skills. Alles Weitere kommt pro Workspace dazu.
`workspace-global.json` in `general`, `minijob` und `uni` aktiviert dieselben
Plugins ebenfalls explizit, weil sie keine Coder-Template-Basis bekommen. Den
DCP-Pin (`@3.2.0`) setzt `startup.sh` aus `DCP_VERSION` in `versions.env` — siehe
[Plugins](mcp-skills.md#plugins).

## Wie die Trennung funktioniert

OpenCode führt globale Config und Workspace-`opencode.json` zusammen. MCP-Server und
Skills werden dabei **pro Workspace** festgelegt:

- Ein Server, der nicht in der Workspace-Config steht, ist für den Agenten **nicht
  existent** — kein Kontext, kein Tool-Schema, keine Versuchung.
- Ein Skill, dessen Ordner nicht geladen wird, taucht nicht in der
  Skill-Liste der Session auf.
- `config-full.toml` vs. `config-cleve.toml` ist die zweite Schicht: selbst wenn ein
  Cleve-Workspace `ssh-mcp` startet, sieht er nur die Profile, die in *seiner*
  Config-Datei stehen.

Deshalb sind die Workspace-Dateien bewusst ausführlich statt minimalistisch: die
Duplikate zwischen Server Management, Home Assistant und Svelte sind der Preis für
eine nachvollziehbare, explizite Sichtbarkeit pro Workspace.

## Skills-Zuordnung

Die Skills liegen zentral in `skills/<gruppe>/` und werden per **relativem Pfad**
aus der jeweiligen Config referenziert. Die Gruppen:

| Ordner | Skills | Geladen von |
|--------|--------|-------------|
| `skills/shared/` | `docker-host`, `docker-host-compose`, `docker-host-filesystem`, `docker-host-git`, `create-skill` | allen sechs Workspaces |
| `skills/server/` | `authentik`, `authentik-integration`, `authentik-troubleshooting`, `docker-host-location` (Krefeld) | server-management, homeassistant, svelte |
| `skills/cleve/` | `docker-host-location` (Kleve) | cleve |
| `skills/homeassistant/` | `home-assistant`, `google-home-exposure` | homeassistant |
| `skills/general/` | `carsten-pc` | general |
| `skills/obsidian/` | `obsidian` | server-management, homeassistant, svelte, general |
| `skills/minijob/` | `obsidian` | minijob |

Die `docker-host*`-Skills sind **generisch** und liegen deshalb in `shared`, nicht im
jeweiligen Site-Ordner. Welcher SSH-Host gemeint ist, entscheidet der
Location-Skill: `skills/server/docker-host-location` löst auf den Krefeld-Host
`docker-host` auf, `skills/cleve/docker-host-location` auf `cleve-docker`. Details und
Lade-Matrix siehe [mcp-skills.md](mcp-skills.md#skills-architektur).

Neben den Repo-Skills gibt es die **Built-in-Skills** von OpenCode (`opencode`,
`report`), die immer verfügbar sind.

## Agent-Konventionen (`AGENTS.md`)

OpenCode lädt `AGENTS.md` aus dem Workspace-Verzeichnis **und allen übergeordneten
Ordnern** (bis zum Repo-Root) sowie die globale `~/.config/opencode/AGENTS.md`.
Repo-weite Konventionen stehen daher **nur einmal** im Root-`AGENTS.md`
([`../AGENTS.md`](../AGENTS.md)); die Verzeichnis-`AGENTS.md` beschreiben nur ihren
Workspace.

- **Rückfragen an den Nutzer werden immer über das native Tool `question` gestellt**
  (klickbare Optionen), **nie** als Freitext — festgelegt im Root-`AGENTS.md`.
- Workspace-`AGENTS.md`: Server Management, Home Assistant, Svelte, Cleve, General,
  minijob (die letzten beiden seit 2026-10-13).