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
- Keine SSH-Befehle auf dem HA-Host
- Keine direkten API-Calls gegen HA außerhalb des MCP
- Token niemals in Logs, Git oder Shell-Ausgaben ausgeben

## Hinweise
- HA-Version bei Bestandsaufnahme: 1.26.0
- Integration: "Model Context Protocol Server" (`mcp_server`)
- Der MCP-Endpunkt liefert Tools, Prompts und Resources (Assist-API)
- Assist-Exposition der Entitäten wird in HA unter "Sprachassistenten" konfiguriert

## Skills
`home-assistant`, `google-home-exposure`, `obsidian`,
`docker-host-location` (Krefeld → `docker-host`),
`docker-host`, `docker-host-compose`, `docker-host-filesystem`, `docker-host-git`,
`create-skill`

> Die docker-host*-Skills sind generisch (geteilt mit den anderen Workspaces).
> Das konkrete Profil steht im `docker-host-location`-Skill — hier: `docker-host`.
