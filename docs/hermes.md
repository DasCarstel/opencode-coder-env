# Hermes-Agent

Der **Hermes-Agent** (Nous Research) ist ein dauerhaft laufender Dienst auf dem
**Coder-Host** (`10.0.10.17`). Er ist **kein** Coder-Template und bekommt keine
Workspace-Session — er ist ein Host-Deployment im Verzeichnis [`hermes/`](../hermes/).

## Zweck

Hestia steuert den Hermes-Agent: Modell, Provider, Fallback-Kette und Abo-Pool
kommen aus der SOT-Datei `.opencode/hestia-hermes.jsonc` und werden von
`hestia/ci/apply-hermes.sh` per `docker exec` in den laufenden Container
geschrieben. Hermes wiederum nutzt OpenCode als MCP-Server-Backend.

Der Container ist außerdem der einzige Ort, an dem **beide** oCIS-Identitäten
(privater und Arbeits-Vault) gleichzeitig ankommen — siehe
[`mcp-skills.md`](mcp-skills.md) — der Hermes-Container als Ausnahme.

## Komponenten

| Datei | Inhalt |
|-------|--------|
| [`hermes/Dockerfile`](../hermes/Dockerfile) | `nousresearch/hermes-agent:latest` + `opencode-mcp@3.0.0` + `ocis-mcp-server` v1.1.0 |
| [`hermes/docker-compose.yml`](../hermes/docker-compose.yml) | Service `hermes`, Netz `coder-infra` (extern), Ports **9119** (Dashboard) und **8642** (API-Server), Neustart `unless-stopped` |
| [`hermes/README.md`](../hermes/README.md) | Kurzfassung für den Ordner |

Eigenes `entrypoint.sh`: **nein**. Der Container nutzt das Entrypoint des
Basis-Images (`/opt/hermes/docker/entrypoint-dispatch.sh`).

## Bind-Mounts und Laufzeit-Zustand

| Host-Pfad | Im Container | Inhalt |
|-----------|---------------|--------|
| `/etc/docker/hermes/data` (Volume) | `/opt/data` | `config.yaml`, `profiles/<name>/{config.yaml,.env}` |
| `/opt/docker-compose/hermes/.env` | `/opt/data/.env` | Provider-/Pool-Keys beider oCIS-Identitäten |

Beides liegt **außerhalb** des Repos und ist bewusst nicht versioniert:

- **`config.yaml`** wird von `hestia/ci/apply-hermes.sh` per
  `hermes config set …` im Container gesetzt (`HOME=/opt/data`).
- **`.env`** schreibt dasselbe Skript als Datei (`cat > /opt/data/.env`,
  danach `chown hermes:hermes`, `chmod 600`). Der Deploy-Workflow schließt sie
  per `rsync --exclude '.env'` aus — sonst würde `--delete` die Hestia-verwaltete
  Datei entfernen.

Der OpenCode-Pool (Abo-Keys) landet ebenfalls in dieser `.env`; ohne
`opencode-go`-Pool bricht der Lauf bewusst **vor** dem Schreiben ab.

## Deploy

```text
Push auf master (Pfade hermes/** oder .github/workflows/deploy-hermes.yml)
  → .github/workflows/deploy-hermes.yml
      rsync -a --delete --exclude '.env' --exclude '*.bak*' \
            hermes/ → root@10.0.10.17:/opt/docker-compose/hermes/
      cd /opt/docker-compose/hermes && docker compose build && docker compose up -d
      Healthcheck: docker inspect -f '{{.State.Running}}' hermes == true
```

- `docker compose build` läuft **ohne `--pull`** — das Basis-Image ist ein
  floating Tag (`nousresearch/hermes-agent:latest`) und soll nicht bei jedem
  Deploy unangekündigt wechseln.
- Secrets kommen ausschließlich aus dem GitHub-Secret `CODER_DEPLOY_KEY`
  (Deploy-Key, wie bei Hestia). Im Workflow steht kein Hermes-Secret.
- Port 9119 (Dashboard) wird nur informativ geprüft, ist kein harter Fehler.

## API-Server und externe Frontends

Hermes bringt einen **OpenAI-kompatiblen API-Server** mit (Port **8642**). Er ist
kein Modell-Proxy, sondern der **Agent selbst**: Open WebUI spricht `/v1/chat/completions`
mit Hermes an, Hermes führt Tools/MCP/Skills/Memory serverseitig aus. Upstream
[Nous-Doku](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/open-webui),
[Open WebUI-Doku](https://docs.openwebui.com/getting-started/quick-start/connect-an-agent).

Aktiviert über die **SOT-Sektion `api_server`** in `.opencode/hestia-hermes.jsonc`
(panel → *Hermes-Karte* → „API-Server an" / Port / Host). `apply-hermes.sh`
schreibt daraus nach `hermes/ci/apply-hermes.sh` die Variablen nach `/opt/data/.env`:

```dotenv
API_SERVER_ENABLED=true
API_SERVER_PORT=8642
API_SERVER_HOST=0.0.0.0   # Default wäre 127.0.0.1 → von anderen Hosts nicht erreichbar
API_SERVER_KEY=<Bearer-Key>
```

> **Warum nicht `config.yaml`:** die wird von `hestia/ci/apply-hermes.sh` komplett
> überschrieben. Die `.env` wird nur auf den `OPENCODE_GO_API_KEY*`-** und den
> `API_SERVER_{ENABLED,PORT,HOST}`-Zeilen angefasst — `API_SERVER_KEY` und alle
> übrigen Zeilen laufen unverändert durch. `write_api_server()` läuft **immer**,
> unabhängig von `pool.followActive`.

Danach `docker compose up -d` auf dem Coder-Host. `/v1/models` liefert genau **einen**
Eintrag `hermes-agent` — die in Hermes konfigurierten Modelle und Provider bleiben
ausschließlich in Hermes/Hestia und sind in Open WebUI weder sichtbar noch änderbar.

### Open WebUI

Open WebUI spricht den API-Server über **Traefik** an, nicht über die direkte
Container-IP. Das ist im Setup der etablierte Weg für Dienste auf einem anderen
LXC (vgl. `rules/coder.yml`, `rules/hestia.yml`) und liefert TLS plus stabilen
Namen. Regel: `/etc/docker/traefik/rules/hermes-api.yml` auf dem Docker-Host.

Gleiches Host-Pattern wie Speaches (PRO-337) — ein Hostname, zwei Ziele:

| Pfad | Ziel | Regel |
|------|------|-------|
| `hermes.mueller-nas.de/` | Dashboard (9119) | `rules/hermes.yml` |
| `hermes.mueller-nas.de/v1` | API-Server (8642) | `rules/hermes-api.yml` |

Connection unter *Admin → Settings → Connections → OpenAI*:

| Feld | Wert |
|------|------|
| Base URL | `https://hermes.mueller-nas.de/v1` |
| API Key | `API_SERVER_KEY` aus `/opt/docker-compose/hermes/.env` |

Damit der Aufruf im LAN bleibt statt durch den Cloudflare-Tunnel zu laufen,
steht in `ollama/docker-compose.yml` zusätzlich:

```yaml
extra_hosts:
  - "hermes.mueller-nas.de:10.0.10.10"
```

Ohne diesen Eintrag liefe der Aufruf über die öffentliche DNS-Auflösung; die
Quell-IP käme dann nicht aus den erlaubten Ranges und der Filter greift nicht.

### Sicherheit

Der Endpoint vergibt **Shell- und Dateizugriff auf den Coder-Host inklusive der
oCIS-Tokens**. Zwei Schichten:

**1. Netz-Isolation (die tragende Maßnahme).** Port 8642 lauscht
**ausschließlich** auf `10.99.0.2`, der Adresse des Coder-Hosts auf einem
dedizierten Segment:

```
vmbr10 auf dem Proxmox-Host   bridge-ports none, keine IP
CT 100 (Docker)  eth1  10.99.0.1/30
CT 120 (Coder)   eth1  10.99.0.2/30
```

In diesem Segment existieren **nur diese zwei Hosts**. Weder das LAN noch
Tailscale noch der Proxmox-Host haben eine Adresse darin.

Der Port wird im Compose explizit gebunden (`"10.99.0.2:8642:8642"`). Ohne die
Adresse — also als `"8642:8642"` — läge er wieder im ganzen LAN offen.
Aufbau, Wiederherstellung und Reboot-Persistenz des Segments stehen in
[`architecture.md`](architecture.md).

**2. Bearer-Key** (`API_SERVER_KEY`, 64 Zeichen) — die Authentifizierung für
alles, was das Segment erreicht. `forwardAuth` ist hier **unbrauchbar**: Open
WebUI arbeitet server-to-server ohne Browser-Session, jeder Request ohne Cookie
würde abgelehnt.

**Der Traefik-Filter bleibt nötig** (`hermes-api-lan-only`,
`rules/hermes-api.yml`): Traefik steht selbst im Segment und erreicht den
Agenten. Ohne den Filter wäre der API-Server über den Cloudflare-Tunnel aus dem
Internet erreichbar — nur durch den Key geschützt. Der Tunnel läuft mit
Remote-Config (`TUNNEL_TOKEN`), die Ingress-Regeln liegen in der Cloudflare-Doku
und sind vom Host aus nicht einsehbar; für das Dashboard ist
`hermes.mueller-nas.de` getunnelt.

> **Nicht verwechseln:** „WAN geblockt" heißt **nicht** „Endpunkt abgesichert".
> Der Filter ist die zweite Schicht, nicht die erste — wer das Segment erreicht,
> erreicht den Agenten.

Nicht zu verwechseln: `hermes proxy` (Port 8645) ist ein **reiner OAuth-Modell-Proxy**
für Nous/xAI ohne Agent-Funktionalität — nicht der API-Server.

## Pflege des Laufzeit-Zustands

```
.opencode/hestia-hermes.jsonc            (SOT, Hestia-Panel)
  → POST /api/hermes/apply
  → .github/workflows/apply-hermes.yml
  → hestia/ci/apply-hermes.sh
      hermes config set / fallback_providers / Pool aus OpenBao
      /opt/data/profiles/<name>/… je Profil
      Restart (restart: true)
```

Details zur Karte und zu den Profil-Overrides: [`hestia.md`](hestia.md#hermes-steuerung).

## Abgrenzung zum früheren Coder-Template

Bis zuletzt lag zusätzlich ein **Coder-Template** `coder-templates/hermes/`
im Repo — mit eigenem `entrypoint.sh`, das AppRole-Login, OpenBao-Secrets und den
Start der Hermes-CLI übernommen hätte. Dieses Template war **nirgends
referenziert und nicht veröffentlicht**.

Es wurde zusammen mit seiner `entrypoint.sh` gelöscht. Der Dienst läuft
ausschließlich als Host-Deployment aus `hermes/`; die Modell-/Provider-Pflege
erfolgt über Hestia, nicht über ein Template. Wer nach `coder-templates/hermes/`
sucht, findet nichts mehr — das ist beabsichtigt.

## Verwandte Doku

- [`hestia.md`](hestia.md) — Hestia-Panel, SOT-Dateien, `apply-hermes`
- [`architecture.md`](architecture.md) — Netz `coder-infra`, Tailscale-Ausnahmen
- [`mcp-skills.md`](mcp-skills.md) — oCIS-Guardrail und die Hermes-Ausnahme