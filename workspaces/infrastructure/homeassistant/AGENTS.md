# Home Assistant – OpenCode-Projekt

## Kontext
Verwaltung von Home Assistant über den nativen MCP-Server (`/api/mcp`, Streamable HTTP).

## Authentisierung
OpenCode lädt den HA-API-Key aus der Umgebungsvariable `HA_LLA_TOKEN`.
Der Wert liegt in OpenBao unter `secret/data/mcp/homeassistant` (Key `api_key`)
und wird von `startup.sh` in die OpenCode-Service-Umgebung injiziert.

**Kein Token im Repo, keine lokale Datei.**

## MCP-Server
- **homeassistant**: Remote MCP-Server
  - URL: `https://intern-homeassistant.mueller-nas.de/api/mcp`
  - Auth: Bearer-Header via `{env:HA_LLA_TOKEN}`
  - Requires header: `Accept: application/json`

## Richtlinien
- Der HA-MCP-Server (`/api/mcp`) liefert nur Intent-/State-Tools. Für Entity-Registry
  (Icons, Labels, Kategorien), YAML-Deploy, Logs und Neustarts hat er **keine** Tools.
- **SSH/ssh-mcp auf CT 100 (Profil `docker-host`) ist ausdrücklich erlaubt und der
  Standardweg** für Docker/Compose, Dateien unter `/etc/docker` (inkl. `homeassistant/`),
  Logs, `check_config`, Neustart und Registry-Operationen. Die frühere Regel
  „keine SSH-Befehle auf dem HA-Host" gilt **nicht mehr**.
- Direkte HA-REST-/WebSocket-Calls sind erlaubt, wenn der MCP-Server die Operation
  nicht anbietet (z. B. `config/entity_registry/update`). Token nur per stdin oder
  Umgebungsvariable übergeben — **niemals** in Kommandozeilen-Argumenten, Dateien,
  Git oder Logs.

## Pflicht bei Automationen, Skripten und Helfern
Vor dem Anlegen oder Ändern: `skill({id:"home-assistant-naming"})` laden.
Nach dem Anlegen oder Ändern **im selben Arbeitsgang** erledigen — nicht optional,
nicht „später", nicht weglassen:
1. **Icon** (`mdi:…`) setzen. Bei YAML-Automationen ist `icon:` in der YAML verboten
   (HA deaktiviert die Automation), es geht nur über die Entity-Registry.
2. **Labels** setzen — nur die tatsächlich benutzten Bausteine.
2a. **`ai_made` ist Pflicht** — jede vom Agenten erstellte Entity (Automation,
   Skript, Helfer, Sensor, Button, Label, Kategorie …) bekommt das Label
   `ai_made`. Keine Ausnahme, keine Rückfrage, kein automatisches Entfernen.
   Entfernt wird es nur auf ausdrücklichen Wunsch des Nutzers.
3. **Kategorie** setzen (Scope `automation` / `script` / `helpers`), vorhandene
   bevorzugen, nur bei echtem Fehlen neu anlegen.
4. Ergebnis prüfen (Registry-Liste): keine neu erstellte Entity darf ohne
   Icon/Label/Kategorie zurückbleiben.
Kann ein Schritt mit den verfügbaren Tools nicht ausgeführt werden, wird das dem
Nutzer **gemeldet** und ein Weg gesucht (SSH/Registry) — nicht stillschweigend
übersprungen.

## Werkzeuge
`tools/ha_registry.py` — liest und schreibt die HA-Entity-Registry über die
WebSocket-API (Icons, Labels, Kategorien). Der MCP-Server kann das nicht.
Dieselbe Datei liegt auf dem Host unter
`/etc/docker/homeassistant/tools/ha_registry.py` (= Container
`/config/tools/ha_registry.py`); nach Änderungen beide Stellen angleichen.

```
printf '%s\n' "$HA_LLA_TOKEN" | ssh -i /root/.ssh/id_ed25519_mcp root@10.0.10.10 \
  'docker exec -i homeassistant python /config/tools/ha_registry.py check'
```

`check` listet fehlende Icons/Labels/Kategorien pro Domain, `apply plan.json
[--dry-run]` setzt sie. Token **nur per stdin** — nie als Argument (sonst steht
er in der Prozessliste und in Fehlerausgaben). Kategorie-IDs im Plan immer
explizit angeben, `@Name`-Aliase sind scope-blind.

## Hinweise
- HA-Version bei Bestandsaufnahme: 1.26.0
- Integration: "Model Context Protocol Server" (`mcp_server`)
- Der MCP-Endpunkt liefert Tools, Prompts und Resources (Assist-API)
- Assist-Exposition der Entitäten wird in HA unter "Sprachassistenten" konfiguriert

## Skills
`home-assistant`, `home-assistant-naming`, `google-home-exposure`, `obsidian`,
`docker-host-location` (Krefeld → `docker-host`),
`docker-host`, `docker-host-compose`, `docker-host-filesystem`, `docker-host-git`,
`create-skill`

> Die docker-host*-Skills sind generisch (geteilt mit den anderen Workspaces).
> Das konkrete Profil steht im `docker-host-location`-Skill — hier: `docker-host`.
