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

## MCP-Server (manuell gepflegt)

Die MCP-Server stehen in `config.yaml` unter `mcp_servers:` und werden von
Hestia **nicht** verwaltet — `hestia/ci/apply-hermes.sh` schreibt nur Modell-,
Fallback-, Pool- und API-Server-Felder. Neue Server werden dort manuell
ergänzt und in `toolsets.enabled` als `mcp-<name>` freigeschaltet (danach
`docker restart hermes`).

Aktuell: `homeassistant` (http), `notion` (http/oauth), `ocis-private` und
`ocis-muellerconnect` (stdio), `super-productivity` (http,
`https://mcp-superproductivity.mueller-nas.de/mcp`, Bearer-Token),
`spotify` (stdio, `node /opt/spotify-mcp/build/index.js`, 31 Tools),
`google-calendar` (stdio, `@cocal/google-calendar-mcp`, self-hosted) sowie
`yazio` (stdio, Fork von `tomastaker/yazio-mcp` — siehe unten).

> **Je Toolset eine eigene Listenzeile.** `- mcp-homeassistant - mcp-ocis-private`
> in einer Zeile ist **ein** Eintrag namens `mcp-homeassistant - mcp-ocis-private`
> — dann wird keiner der beiden Server geladen. Genau das war bis 2026-10-19 der
> Fall; seit dem Fix sind `ocis-private` (79 Tools) und `super-productivity`
> (28 Tools) verbunden.

### Spotify

Der Spotify-MCP-Server
([`marcelmarais/spotify-mcp-server`](https://github.com/marcelmarais/spotify-mcp-server))
steuert Wiedergabe, Suche und Playlisten über Spotify. Er läuft als **stdio**-
Server (`node /opt/spotify-mcp/build/index.js`, 31 Tools) und ist im
[Dockerfile](../hermes/Dockerfile) eingebaut.

**Build im Image.** Der Server verlangt Node >= 26.8.1; das Basis-Image bringt
aber nur 26.7.0 mit. Deshalb liegt eine eigene Node-Version unter `/opt/node`
(`ARG NODE_VERSION=26.12.1`). Bewusst **nicht** `/usr/local/bin/node`
überschrieben: Dieser Symlink zeigt in den von Hermes selbst verwalteten Baum
`/opt/hermes/tools` und würde beim nächsten Hermes-Update zurückfallen. Gebaut
wird aus einem gepinnten Commit (`ARG SPOTIFY_MCP_COMMIT`); es gibt kein
npm-Release. Runtime-Dependencies bleiben im Image (`npm prune --omit=dev`).

**Zugangsdaten: OpenBao ist die Quelle.** Der Server liest seine Zugangsdaten
**nicht** aus Umgebungsvariablen, sondern ausschließlich aus
`../spotify-config.json` relativ zu seinem `build/`-Verzeichnis — im Container
also `/opt/spotify-mcp/spotify-config.json`, ein Symlink auf
`/opt/data/spotify/spotify-config.json` (Docker-Volume `/opt/data`, Rechte
**0600**, außerhalb des Repos).

Damit Client-ID und Client-Secret nicht in der Anwendung selbst gepflegt
werden, liegen sie in OpenBao unter `secret/data/mcp/spotify`
(`client_id`, `client_secret`, `redirect_uri`). Die JSON-Datei ist nur
abgeleiteter Laufzeit-Zustand und wird von
[`hestia/scripts/spotify_config.py`](../hestia/scripts/spotify_config.py)
daraus gerendert:

```sh
# auf dem Coder-Host; OPENBAO_TOKEN ist ein lesendes Token
OPENBAO_TOKEN=... python3 hestia/scripts/spotify_config.py [--dry-run]
```

Das Skript behält vorhandene Tokens (`accessToken`, `refreshToken`,
`expiresAt`) bei — sonst würde jedes Rendern die OAuth-Sitzung zerstören. Die
Tokens schreibt ausschließlich der Server selbst zurück, wenn er das
Access-Token erneuert. Ausgegeben werden nur Längen und Feldnamen, nie Werte.

**OAuth einmalig (headless).** Der Server kann sich im Container nicht selbst
autorisieren: er lauscht für den Callback auf `127.0.0.1` und öffnet einen
Browser. Deshalb derselbe Authorization-Code-Flow in zwei Schritten über
[`hestia/scripts/spotify_auth.py`](../hestia/scripts/spotify_auth.py):

```sh
python3 hestia/scripts/spotify_auth.py --print-url        # URL im Browser öffnen
python3 hestia/scripts/spotify_auth.py --callback '<komplette Redirect-URL aus der Adresszeile>'
```

Der Browser zeigt nach dem Zustimmen einen Fehler (auf `127.0.0.1:8888`
lauscht nichts) — der `code` steht in der Adresszeile. Der `state` wird
zwischen den beiden Aufrufen in `<config>.state` gemerkt und geprüft. Redirect-URI
der Developer-App: `http://127.0.0.1:8888/callback` (Loopback-IP, **nicht**
`localhost`), Scopes wie vom Server vorgegeben. Ein erneutes Autorisieren ist
nur nötig, wenn der Zugriff in der Spotify-App entzogen wurde oder das Passwort
geändert wurde — ansonsten erneuert der Server das Access-Token selbst über den
Refresh-Token.

**Aktivierung.** Der Eintrag wird über die Hermes-CLI geschrieben (Prompt
„Enable all tools?" mit `y` bestätigen), anschließend `mcp-spotify` in
`toolsets.enabled` und in `platform_toolsets.api_server` der SOT ergänzen:

```sh
docker exec -u 0 hermes sh -lc 'HOME=/opt/data /opt/hermes/bin/hermes mcp add spotify \
  --command /opt/node/bin/node --args /opt/spotify-mcp/build/index.js'
docker exec -u 0 hermes sh -lc 'HOME=/opt/data /opt/hermes/bin/hermes mcp test spotify'
```

**Voraussetzungen.** Eine Spotify-Developer-App **und** Spotify Premium. Im
Developer-Mode (seit 11.02.2026) sind maximal 5 Nutzer erlaubt und Premium ist
Pflicht.

## Google Calendar

Der Google-Calendar-MCP-Server ([`@cocal/google-calendar-mcp`](https://www.npmjs.com/package/@cocal/google-calendar-mcp),
Version 2.7.0) verwaltet Termine in Google Calendar: Listen, Suchen, Anlegen,
Ändern, Löschen, Zu- und Absagen sowie Free/Busy-Abfragen. Er läuft als
**stdio**-Server (npm, global im Image installiert via `ARG GCAL_MCP_VERSION`
im [Dockerfile](../hermes/Dockerfile)) und steht in der `config.yaml` auf dem
Coder-Host unter `mcp_servers.google-calendar`.

**Warum nicht der offizielle Google-Server?** Der offizielle Endpunkt
(`https://calendarmcp.googleapis.com/mcp/v1`) antwortet auf jeden Tool-Aufruf
mit `Access to this tool requires that your Google Cloud project (…) is
enrolled in the Google Workspace Developer Preview Program`. Das Enrollment im
Developer Preview Program (DPP) setzt ein Google-Workspace-Konto voraus; das
genutzte Konto ist privat (`@gmail.com`). OAuth selbst funktioniert dort
(Token, Scopes, Refresh) — nur die Tool-Aufrufe sind gesperrt.

**Aktivierte Tools.** Die Offenlegung läuft über die Umgebungsvariable
`ENABLED_TOOLS`:

```text
list-calendars,list-events,get-event,search-events,create-event,
update-event,delete-event,respond-to-event,get-freebusy,get-current-time
```

Hermes registriert daraus **11 Tools**: die zehn oben genannten plus
`manage_accounts`, das immer aktiv ist. Kalender-Ebene (umbenennen, Farben,
Freigaben, Kalender anlegen) deckt der Server nicht ab — nur Termine.

**Zugangsdaten.** Beide Dateien liegen außerhalb des Repos, Owner
`10000:10000`, Modus `600`; das Verzeichnis selbst ebenfalls `10000:10000`,
Modus `750` — sonst kann der Server beim Refresh keine temporäre Datei anlegen
(`EACCES` auf `tokens.json.tmp-…`) und der neue Access-Token geht beim Neustart
verloren:

| Host-Pfad | Inhalt |
|-----------|--------|
| `/etc/docker/hermes/data/gcal/credentials.json` | OAuth-Client im Google-Format `{"web": {client_id, client_secret, redirect_uris: ["http://127.0.0.1:8888/callback"]}}` |
| `/etc/docker/hermes/data/gcal/tokens.json` | Account-Map des Servers, `{"normal": {access_token, refresh_token, scope, token_type, expiry_date}}` |

Im Container liegen sie entsprechend unter `/opt/data/gcal/…` (Envs
`GOOGLE_OAUTH_CREDENTIALS` und `GOOGLE_CALENDAR_MCP_TOKEN_PATH`). `expiry_date`
ist in **Millisekunden** angegeben; der Server schreibt die Token-Datei nach
jedem Refresh selbst zurück.

**OAuth einmalig / Re-Auth (headless).** Wie bei Spotify in zwei Schritten —
im Container gibt es weder Browser noch einen erreichbaren Loopback-Callback —
über [`hermes/scripts/gcal_auth.py`](../hermes/scripts/gcal_auth.py):

```sh
python3 hermes/scripts/gcal_auth.py --print-url --state hermes-gcal   # URL im Browser öffnen
python3 hermes/scripts/gcal_auth.py --callback 'http://127.0.0.1:8888/callback?code=…&state=…'
docker restart hermes
```

Der Browser zeigt nach der Zustimmung wieder einen Fehler (auf `127.0.0.1:8888`
lauscht nichts) — die komplette Adresszeile kopieren und beim zweiten Aufruf
übergeben.

**Freischaltung.** `mcp-google-calendar` in `toolsets.enabled` (config.yaml)
**und** in `platform_toolsets.api_server` der
[`.opencode/hestia-hermes.jsonc`](../.opencode/hestia-hermes.jsonc) ergänzen,
danach `docker restart hermes`.

> **Falle: Testing-Status der Google-App.** Solange die Google-Auth-App
> (Projekt `hermes-calendar-512619`) im Status **„Testing"** ist, laufen die
> Refresh-Tokens nach **7 Tagen** ab — der Kalender wäre dann wöchentlich tot.
> Lösung: Die App in der Google Auth Platform auf **„In Produktion"**
> veröffentlichen. Der Scope ist sensitiv, nicht restriktiv; die Warnung
> „App nicht verifiziert" bleibt auch danach normal. In Produktion laufen die
> Tokens nicht mehr ab.

## YAZIO

Der YAZIO-MCP-Server
([`tomastaker/yazio-mcp`](https://github.com/tomastaker/yazio-mcp), MIT) macht
das Ernährungstagebuch für den Agenten les- **und** schreibbar: Produktsuche in
der deutschen YAZIO-Datenbank, Tagebuch lesen, Einträge anlegen/ändern/löschen,
Wasser, Gewicht und Auswertungen.

**Warum ein Fork.** Upstream ist ein Ein-Personen-Projekt (letzter Commit
2026-10-14). Erwartung ist bewusst niedrig: das Projekt wird **nicht** als
gepflegte Abhängigkeit behandelt, sondern als gepinnter Fremdcode. Der Fork
`DasCarstel/yazio-mcp` hält unseren Stand; das Image baut **einen festen
Commit** (`ARG YAZIO_MCP_COMMIT`) — kein Auto-Update.

Eigene Patches gegenüber Upstream:

| Patch | Zweck |
|-------|-------|
| `feat(saved-meals)` | Tools `list_saved_meals`, `log_saved_meal`, `save_meal` — benannte Mahlzeiten aus `saved-meals.json` in einem Aufruf buchen |
| `feat(user-meals)` | Tools `list_user_meals`, `log_user_meal` — die in YAZIO angelegten Mahlzeiten (`/user/meals`) nativ buchen |
| `feat(compact)` | `search_products`/`get_diary` kompakt per Default (kleinere Tool-Ergebnisse, weniger Kontext) |

**API-Basis ist `/v19`.** Verifiziert am 2026-11-08: Login per Password-Grant,
Produktsuche (DE/`de_DE`), Tagebuch und der komplette Schreibzyklus
(anlegen → per Re-Read verifiziert → Betrag ändern → löschen) funktionieren mit
unserem Account. Die App-Header (`x-yazio-app-version` …) sind auf `/v19`
**nicht** nötig; `/v22` wird nur vom Exporter genutzt.

**Zugangsdaten.** OpenBao `secret/data/mcp/yazio` (`username`, `password`,
`client_id`, `client_secret`) ist die Quelle. `hestia/scripts/yazio_env.py`
rendert daraus zwei Dateien ins Volume:

| Host-Pfad | Inhalt |
|-----------|--------|
| `/etc/docker/hermes/data/yazio/.env` | Umgebungsvariablen, **0600** |
| `/etc/docker/hermes/data/yazio/run.sh` | Wrapper: `.env` laden, `exec /opt/node/bin/node /opt/yazio-mcp/dist/index.js`, **0700** |

`config.yaml` enthält damit **keine** YAZIO-Secrets, nur
`command: /opt/data/yazio/run.sh`. Der Token-Cache liegt unter
`/opt/data/yazio/token.json` (0700-Verzeichnis), `saved-meals.json` daneben.

> **Falle: `$` im Passwort.** In `yazio.env` des Exporters steht `$$`, weil
> Compose sonst interpoliert (sonst HTTP 400 mit leerem Body). `yazio_env.py`
> liest den OpenBao-Wert direkt und quotet ihn mit `shlex.quote` — hier wird
> nichts unescaped.

**Gespeicherte Mahlzeiten.** Zwei Ebenen:

1. **YAZIO-eigene Mahlzeiten** (`GET /user/meals`) — die im App-Konto
   angelegten Vorlagen (aktuell 10: Butter Chicken, Drink, Teriyaki,
   Putenhack Burger, Epische Haferflocken …). Tools: `list_user_meals` und
   `log_user_meal` — bucht die Produkte der Mahlzeit über `add_meal_batch`,
   `daytime` ist Pflicht, `amount_factor` skaliert alle Mengen.
2. **Lokale Templates** (`saved-meals.json`) als Fallback für Kombinationen,
   die es in YAZIO nicht gibt: `save_meal`, `list_saved_meals`,
   `log_saved_meal`. `/opt/data/yazio/saved-meals.json`:

```json
{ "meals": [ { "name": "Chicken Teriyaki", "aliases": ["teriyaki"],
  "default_daytime": "lunch",
  "items": [ { "product_id": "<uuid>", "amount": 250, "serving": "gram",
               "serving_quantity": null } ] } ] }
```

**Kompakte Antworten (Default an).** `search_products` liefert per Default
`limit: 5` und `compact: true` (`product_id`, `name`, `brand`, `serving`,
`per_100g`), `get_diary` per Default `compact: true` (kein Nährwert pro
Eintrag, Tages-Totals bleiben). Grund: gemessen am 2026-11-08 lieferte eine
Suche 7,7 kB pro Aufruf und eine Tagebuch-Range 82 kB, die Hermes auf Platte
auslagern musste. `compact: false` liefert weiterhin das volle Format.

**Aktivierung.** `mcp-yazio` in `toolsets.enabled` **und** in
`platform_toolsets.api_server` ergänzen, danach `docker restart hermes`.

## Picnic

Der Picnic-MCP-Server ([`ivo-toby/mcp-picnic`](https://github.com/ivo-toby/mcp-picnic),
MIT) macht den Picnic-Online-Supermarkt für den Agenten bedienbar: Produktsuche,
Wochenangebote, Rezepte, Warenkorb, Liefer-Slots, Lieferverfolgung und Wallet —
rund 40 Tools. Upstream ist ein Solo-Projekt, deshalb derselbe Umgang wie bei
YAZIO: fester Pin (`ARG PICNIC_MCP_VERSION`), kein Auto-Update.

**Nur im Default-Profil.** Das Profil `kleve` bleibt bei genau einem
MCP-Server (`homeassistant-kleve`) — Picnic gehört dort nicht hin.

**Build im Image.** `npm install -g mcp-picnic@${PICNIC_MCP_VERSION}` im
[Dockerfile](../hermes/Dockerfile). Node ≥ 18 ist erfüllt (Basis-Image 26.7.0),
`/opt/node` wird nicht gebraucht. Das npm-`bin` heißt irrtümlich
`mcp-server-template` (Template-Rest des Upstreams); der Wrapper spricht deshalb
`bin/mcp-server.js` direkt an und hängt nicht am Bin-Namen.

**Zugangsdaten.** OpenBao `secret/data/mcp/picnic` (`username`, `password`,
optional `country_code`, Default `DE`) ist die Quelle.
[`hestia/scripts/picnic_env.py`](../hestia/scripts/picnic_env.py) rendert daraus
zwei Dateien ins Volume:

| Host-Pfad | Inhalt |
|-----------|--------|
| `/etc/docker/hermes/data/picnic/.env` | Umgebungsvariablen, **0600** |
| `/etc/docker/hermes/data/picnic/run.sh` | Wrapper, **0700** |

`config.yaml` enthält damit **keine** Picnic-Secrets, nur
`command: /opt/data/picnic/run.sh`.

**Session und 2FA.** `PICNIC_SESSION_FILE` und `PICNIC_DEVICE_FILE` zeigen nach
`/opt/data/picnic/`. Beide Dateien überleben Deploy und Rebuild — ohne sie wäre
nach jedem Neustart eine neue 2FA fällig. Das Skript fasst sie nicht an. 2FA
läuft über die Tools `picnic_generate_2fa_code` und `picnic_verify_2fa_code`:
Code anfordern, SMS-Code im Chat nennen, verifizieren. Während einer offenen
2FA bleibt der Server oben, damit der Client den Code nachreichen kann.

**Schreib-Tools.** Warenkorb ändern, Liefer-Slot buchen, Lieferung stornieren,
Rechnung per Mail senden — das kostet Geld. Die Verhaltensregel steht in
[`SOUL.md`](../hermes/SOUL.md) und in `MEMORY.md`: bestellen, buchen und
stornieren nur nach ausdrücklicher Bestätigung im selben Turn.

**Aktivierung.** `mcp-picnic` in `toolsets.enabled` (config.yaml) **und** in
`platform_toolsets.api_server` der SOT ergänzen, danach `docker restart hermes`.



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

### Bilder (Vision)

Die Hauptmodelle (z. B. `opencode-go/beam-free`) sind text-only. Hermes routet
Bild-Anhänge deshalb über ein Hilfsmodell:

```yaml
auxiliary:
  vision:
    provider: opencode-go
    model: glm-5.3-flash        # chat_completions, Abrechnung über das Go-Abo
    timeout: 120
    download_timeout: 30
```

- Der Block muss in **jeder** Profil-Config stehen (`config.yaml` und `profiles/*/config.yaml`) — Profile erben `auxiliary` nicht vom Root.
- Das Toolset `vision` muss aktiv sein, sonst fehlt das Werkzeug `vision_analyze`.
- In Open WebUI braucht der Modelleintrag `meta.capabilities.vision = true`, sonst blendet die Oberfläche den Bild-Anhang aus.
- Gepflegt wird der Wert im Hestia-Panel (Hermes-Tab → „Bilder ansehen"); die SOT-Felder stehen in `.opencode/hestia-hermes.jsonc`.

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

## Persona (`SOUL.md`)

Die Verhaltensvorgabe des Agenten liegt **im Repo**:
[`hermes/SOUL.md`](../hermes/SOUL.md). Sie wird — anders als `config.yaml` —
nicht von `apply-hermes.sh` geschrieben, sondern über den normalen Deploy
ausgeliefert:

```text
hermes/SOUL.md  --rsync-->  /opt/docker-compose/hermes/SOUL.md
                --Datei-Mount-->  /opt/data/SOUL.md
```

Der Datei-Mount liegt über dem Ordner-Mount `/etc/docker/hermes/data:/opt/data`,
das Muster ist dasselbe wie bei `.env`. Eine im Volume vorhandene Altdatei wird
dadurch **verdeckt**, nicht gelöscht — sie ist ab dem Mount toter Ballast.

Wirksam wird eine Änderung mit dem Deploy, weil dieser den Container neu
erzeugt (`docker compose up -d`); das nächste Gespräch nutzt dann die neue
Persona. Ein `apply` aus Hestia ist dafür nicht nötig und ändert daran nichts.

## Turn-Limit

`agent.max_turns` in der SOT begrenzt, wie viele Agent-Schleifen ein Auftrag
laufen darf. Seit 2026-11-06 steht der Wert auf `null` — kein Limit.

Der Grund: die Grenze schützt nichts, sie schneidet nur ab. Die Meldung „das
dauert länger" kommt **nicht** von Hermes, sondern aus der Assist-Anbindung
(`hermes_async.py` in Home Assistant). Sie wartet 8 s auf die HTTP-Antwort, gibt
dann die Quittung an Assist zurück und lässt den Turn im Hintergrund
weiterlaufen — Hermes merkt davon nichts und arbeitet weiter. Ein
`max_turns`-Limit würde also mitten in einer Recherche abbrechen, ohne dass
irgendwo eine Meldung dazu erscheint.

Verbleibende Bremsen:

| Bremse | Wo | Wirkung |
|--------|----|---------|
| `max_runtime` 1800 s | HA-Wrapper | Assist gibt nach 30 min auf, bricht den Request ab und schickt die Zwischenmeldung als letztes Lebenszeichen |
| Provider-/Kontextlimit | Hermes/Modell | Turn endet mit Fehler |

Für Turns, die **nicht** über Assist laufen (ntfy, Dashboard, API), gibt es damit
keine Zeitbremse mehr. Das ist bewusst so: dort sieht der Nutzer das Ergebnis
ohnehin erst am Ende.

Historie: `12` (bis 2026-11-06, zu knapp für Hintergrund-Recherchen), `60` (kurze
Zwischenstufe), dann `null`.

Diese Einstellung gilt **nur fürs Default-Profil**. Das Profil `kleve` hat einen
eigenen, davon unabhängigen Stand — siehe [Profile](#profile).

## Profile

Am 2026-11-07 existiert neben dem Default-Profil genau eines: **`kleve`**
(Haus-Hilfe für Kleve — Licht, Heizung, Temperatur — in einfacher Sprache).
Es ist **kein** eigener Standort-Agent: es gibt nur die eine Hermes-Instanz in
Krefeld, das Profil läuft über den Default-Profil-Multiplexer desselben
Containers.

| Ort | Inhalt |
|-----|--------|
| `/opt/data/profiles/kleve/config.yaml` | Modell, MCP-Server `homeassistant-kleve` (über das TCP-Relay), Toolsets `clarify` + `mcp-homeassistant-kleve`, deaktivierte Skills |
| `/opt/data/profiles/kleve/.env` | Zugangsdaten des Profils |
| `/opt/data/profiles/kleve/SOUL.md` | Persona des Profils |

**Nicht versioniert:** Diese Dateien liegen im Volume und sind — anders als
[`hermes/SOUL.md`](../hermes/SOUL.md) fürs Default-Profil — nirgends im Repo
gespiegelt. Deploy und Apply stellen sie nicht wieder her.

### Keine Vererbung des `agent`-Blocks

Ein Profil erbt den `agent`-Block des Default-Profils **nicht**. Nachweis
(2026-11-07): `hermes --profile kleve config get agent.reasoning_effort` →
`Config key not set`, obwohl das Default-Profil `low` setzt;
`agent.api_max_retries` liefert `3` (Hermes-Default) statt `2`.

Für `agent.max_turns` gilt im Profil damit der **Hermes-Default `None`** —
unbegrenzt. Die `12`, die bis 2026-11-06 im Default-Profil stand, hat dort nie
gewirkt. Das ist bewusst so geblieben: das Profil hat zwar keinen
Async-Wrapper vor sich, aber auch nur zwei Toolsets, und ein Limit würde
lediglich Antworten abschneiden.

### Was die Apply-Kette für Profile tut

`apply-hermes.sh` schreibt je Profil ausschließlich `model.provider`,
`model.default`, `fallback_providers` und den `.env`-Pool. Ein `agent`-Block im
Profil wird **nicht** angefasst: er überlebt einen Apply, bleibt aber
unversioniert. Sollen Profile eigene Agent-Grenzen bekommen, muss das erst in
der SOT und im Skript ergänzt werden.

## Persistentes Gedächtnis (Memory-Slots)

Hermes hat ein eingebautes Gedächtnis, das **nicht** in einer Notiz liegt,
sondern im Volume des Containers:

| Datei | Zweck | Limit |
|-------|-------|-------|
| `/opt/data/memories/MEMORY.md` | eigene Fakten/Umgebung/Regeln des Agenten | 2200 Zeichen |
| `/opt/data/memories/USER.md` | Nutzerprofil | 1375 Zeichen |

Beide Dateien werden bei **jedem** Turn als Snapshot in den Prompt geladen und
vom `memory`-Tool (`add`/`replace`/`remove`) fortgeschrieben. Sie überleben
Container-Restart und Deploy (Bind-Mount `/etc/docker/hermes/data`), werden aber
von `apply-hermes.sh` nicht angefasst — es gibt (noch) keinen Seed im Repo.

**Voraussetzung ist das Toolset:** `memory` steht in der SOT unter
`platform_toolsets.api_server`. Fehlt es, hat der Agent auf der Assist-/Voice-
Oberfläche *kein* Memory-Tool — er kann dann nichts dauerhaft merken (genau das
war bis 2026-10-25 der Fall; dort waren nur `mcp-*`-Toolsets aktiv).

Bewusst **nicht** auf dem api_server aktiviert (Messung 2026-10-25, identischer
Prompt, je ein frischer Turn):

| Konfiguration | Prompt-Tokens | Ergebnis |
|---------------|---------------|----------|
| `mcp-*` + `memory` + `skills` | 10 541 | `skills` kostet 1 901 Tokens |
| `mcp-*` + `memory` | **8 640** | aktueller Stand |
| `mcp-*` + `memory`, `mcp-ocis-private` entfernt | 8 640 | Gegenprobe: MCP kostet 0 |

Die MCP-Tools kosten **0** Prompt-Tokens — sie werden über `tool_search`
nachgeladen (Gegenprobe: `mcp-ocis-private` entfernt ⇒ unverändert 8 640).
`terminal`, `file` und `code_execution` bleiben aus: Vault-Zugriff läuft
ausschließlich über `ocis-private`. Die Skill `obsidian-ocis` (v3, MCP-direkt)
ist damit eine CLI/PC-Ressource; auf der Voice-Oberfläche trägt `MEMORY.md` den
Ablauf (Read-Modify-Write + `space_id`), damit kein Skill-Index nötig ist.

### Notizen im Obsidian-Vault bearbeiten

Die oCIS-MCP-Tools haben **kein** Update-/Append-Tool. Bearbeiten ist
Read-Modify-Write und funktioniert, weil `ocis_upload_file` eine bestehende
Datei überschreibt:

1. `ocis_download_file {space_id, path}` → kompletter Text (`truncated=false`)
2. Text im Kontext ändern
3. `ocis_upload_file {space_id, path, content, content_type: "text/markdown"}`
   mit dem **vollständigen** neuen Text

Nur das Delta hochzuladen würde die Notiz abschneiden. Die Skill
`hestia/skills/obsidian-ocis/SKILL.md` (v3, MCP-direkt) beschreibt den Ablauf
inklusive `space_id`; die frühere Fast-Path-Variante über
`/opt/data/bin/obsidian_fast.py` und das `terminal`-Tool ist auf der
Assist-Oberfläche **nicht** nutzbar und wird dort nicht mehr verwendet.

### Dauerregel `Ideen.md`

`/Ideen.md` (Vault-Root) wird bei „füg etwas zu den Ideen hinzu“ **nur
erweitert**: ganze Datei lesen, neuen Abschnitt anhängen, kompletten Text
zurückschreiben. Die Regel steht in `MEMORY.md` und in der Skill, damit sie in
jedem neuen Chat gilt.

## Erweiterte Sektionen (Hermes Voice)

Neben `primary`/`fallback`/`pool`/`api_server`/`profiles` kennt die SOT fünf
erweiterte Sektionen, die das Hermes-Verhalten feinjustieren:

| Sektion | Inhalt |
|---------|--------|
| `agent` | `reasoning_effort` (z. B. `low`), `api_max_retries`, `auto_recovery_cycles`; unbekannte Keys bleiben erhalten |
| `providers` | Timeouts je Provider, z. B. `opencode-go.request_timeout_seconds` / `stale_timeout_seconds` |
| `platform_toolsets` | je Plattform die erlaubten **eingebauten** Toolsets, z. B. `api_server: [web, todo, memory, clarify, skills, terminal]` |
| `platforms` | z. B. `platforms.api_server.extra.model_routes` (benannte Modellrouten für den API-Server); andere Keys wie `platforms.ntfy` bleiben unangetastet |
| `skills` | `disabled`-Liste der deaktivierten Skills (z. B. `[pdf, github, notion]`); die Liste ersetzt beim Merge die Live-Liste und wird nie konkateniert |

Hestia **erhält** diese Sektionen: Das Panel schickt sie nicht mit — `apply()`
in `hestia/backend/hermes.py` trägt sie aus der aktuellen SOT weiter und mergt
ein Teil-Payload per Deep-Merge darunter (nicht genannte Keys überleben).
Validiert werden Toolset-Namen, Timeout-Bereiche, Alias-Form und Modellrouten
(Provider-Allowlist + Katalog) — Unfug bricht den Apply ab, ohne zu committen.

`hestia/ci/apply-hermes.sh` mergt die fünf Sektionen per Deep-Merge in die
`config.yaml` im Container (ruamel Round-Trip — Kommentare, Reihenfolge und
alle anderen Keys wie `mcp_servers`, `toolsets.enabled`, `tools` und
`model.base_url` bleiben unangetastet). Vor dem Merge gibt es ein dediziertes
Backup; schlägt das anschließende `hermes config check` fehl, wird es
automatisch wiederhergestellt und der Lauf endet mit Fehler.

## Pflege des Laufzeit-Zustands

```
.opencode/hestia-hermes.jsonc            (SOT, Hestia-Panel)
  → POST /api/hermes/apply
  → .github/workflows/apply-hermes.yml
  → hestia/ci/apply-hermes.sh
      hermes config set / fallback_providers / Pool aus OpenBao
      erweiterte Sektionen (agent/providers/platform_toolsets/platforms/skills)
        per Deep-Merge in die config.yaml (Backup + Restore bei config-check-Fehler)
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