# Server Management - OpenCode Projekt

## Kontext
Dieses Projekt verwaltet SSH-Zugriff auf Infrastruktur-Hosts über den Community SSH-MCP mit OpenBao-signierten SSH-Zertifikaten.

## MCP-Server
- **ssh-mcp**: `tufantunc/ssh-mcp@2.17.0` (lokal, stdio)
  - Konfiguration: `~/.config/ssh-mcp/config.toml`
  - OpenBao SSH CA: `host-access` Rolle
  - Kurzlebige Zertifikate (nicht permanente Keys)

## Richtlinien
- **NEVER** raw SSH via bash (`ssh Docker '...'`, etc.)
- Nur ssh-mcp Tools verwenden
- Nur feste, überprüfte Host-Profile
- Gepinnte SSH-Host-Key-Fingerprints
- Zertifikats-Principal: Nicht-Root-User (oder root mit sudoers)
- Keine Secrets, Tokens oder private Keys im Repo

## Host-Profile (TODO)
- Docker Host (10.0.10.10)
- Proxmox (10.0.10.20)
- TrueNAS (10.0.10.30)
- Weitere nach Bedarf

## TODO
- [ ] ssh-mcp konfigurieren (`~/.config/ssh-mcp/config.toml`)
- [ ] OpenBao Policy `mcp-server` erweitern um SSH-Signierung:
  ```hcl
  path "ssh/sign/host-access" {
    capabilities = ["update"]
  }
  ```
- [ ] AppRole `mcp-server` Secret-ID rotieren (alte Secret-ID wurde im Chat offengelegt)
- [ ] Canary-Test: kurzer Befehl, SFTP-Test, Zertifikatserneuerung, Host-Key-Pins
- [ ] Nicht-Root-SSH-User oder sudoers-Modell festlegen

## Referenzen
- [OpenBao SSH User-Zertifikate](https://openbao.org/docs/secrets/ssh/signed-ssh-certificates)
- [ssh-mcp GitHub](https://github.com/tufantunc/ssh-mcp)
