# Server Management – OpenCode-Projekt

## Kontext
Verwaltung von Infrastruktur-Hosts (Docker Host, Proxmox, TrueNAS) über den Community SSH-MCP.

## MCP-Server
- **ssh-mcp**: `tufantunc/ssh-mcp@2.17.0` (lokal, stdio)
  - Konfiguration: `~/.config/ssh-mcp/config.toml` (nicht im Repo)
  - Installiert auf dem Docker Host via `npm install -g ssh-mcp@2.17.0`

## Richtlinien
- **NIEMALS raw SSH via bash** (`ssh Docker '...'`, `bash("ssh ...")`)
- Ausschließlich ssh-mcp Tools verwenden
- Nur feste, überprüfte Host-Profile in `~/.config/ssh-mcp/config.toml`
- Gepinnte SSH-Host-Key-Fingerprints
- Keine Secrets, Tokens oder private Keys im Repo

## Host-Profile (Beispiel, muss angepasst werden)
- Docker Host (10.0.10.10)
- Proxmox (10.0.10.20)
- TrueNAS (10.0.10.30)

## OpenBao SSH-CA (optional, nicht zwingend)
Eine CA ist in OpenBao konfiguriert (`host-access` Rolle). Die Nutzung
kurzlebiger Zertifikate ist möglich, aber nicht zwingend – ein fester
SSH-Key funktioniert ebenfalls.

## Referenzen
- [OpenBao SSH User-Zertifikate](https://openbao.org/docs/secrets/ssh/signed-ssh-certificates)
- [ssh-mcp GitHub](https://github.com/tufantunc/ssh-mcp)
