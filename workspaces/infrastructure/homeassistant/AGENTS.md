# Home Assistant – OpenCode-Projekt

## Kontext
Verwaltung von Home Assistant über den nativen MCP-Server (`/api/mcp`, Streamable HTTP).

## Authentisierung
OpenCode lädt den HA-API-Key aus der Umgebungsvariable `HA_LLA_TOKEN`.
Diese wird im Coder-Template als **Coder Secret** gesetzt und automatisch in den Workspace injiziert.

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
