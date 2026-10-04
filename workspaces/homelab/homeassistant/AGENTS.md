# Home Assistant - OpenCode Projekt

## Kontext
Dieses Projekt verwaltet die Home Assistant Integration über den nativen MCP-Server.

## MCP-Server
- **homeassistant**: Native HA MCP-Server unter `/api/mcp` (Streamable HTTP, OAuth)

## Richtlinien
- Keine SSH-Befehle auf dem HA-Host
- Nur HA-spezifische Operationen über den MCP-Server
- Token wird aus OpenBao bezogen (nicht hardcoded)

## TODO
- [ ] HA `mcp_server` Integration in HA aktivieren (Settings → Integrations → "Model Context Protocol Server")
- [ ] OpenBao-Token-Auflösung für HA-MCP konfigurieren (Secret-Gate prüfen)
- [ ] OAuth-Flow testen oder dedizierten HA-Benutzer mit begrenzten Rechten erstellen
