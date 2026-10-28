# Grafana – OpenCode-Projekt

## Kontext
Betrieb der Grafana-Instanz (Dashboards, Alerting, Datasources). Die Instanz läuft
auf dem Docker-Host (CT 100, 10.0.10.10) im Compose-Stack
`/opt/docker-compose/influxdb-grafana` (Grafana 12.3.3, Datenverzeichnis
`/etc/docker/grafana`). Data-Backend ist ein InfluxDB-Datasource.

Bestand (Stand 28.10.2026): genau eine Datasource (`InfluxDB`, `isDefault`),
Ordner `Infrastructure` und `Logic`, u. a. die Dashboards `Battery Overview`,
`Klima`, `Magnus`, `Possible Filters`, `Server Monitoring`. Es gibt **keine**
Provisioning-Dateien – alles wurde per UI angelegt und liegt in `grafana.db`.

## MCP-Server
- **grafana** (lokal) – offizieller Grafana-MCP `grafana/mcp-grafana`
  - Binary: `/root/.local/bin/mcp-grafana` (gepinnt über `GRAFANA_MCP_VERSION`)
  - `GRAFANA_URL=https://grafana.mueller-nas.de`
  - Token: OpenBao `secret/data/mcp/grafana` (`api_key`) → `GRAFANA_API_KEY`
  - **Vollzugriff inkl. Schreib-Tools** (kein `--disable-*`) → Schreibaktionen
    (Dashboards patchen, Alert-Rules, Contact-Points, Silences) nur nach
    Rückfrage bzw. bei explizitem Auftrag; **vor Änderungen bestehende
    Dashboards exportieren** (Backup per API, z. B. nach Obsidian oder /tmp).
  - `GRAFANA_USAGE_STATS=disabled` – die anonyme Telemetrie des MCP-Servers ist
    abgeschaltet (kein Datenversand an Grafana Labs).
  - Tool-Namen (v2.1.1, 79 Tools): `search_dashboards`, `get_dashboard_by_uid`,
    `update_dashboard`, `list_datasources`, `check_datasources_health`,
    `query_prometheus`, `alerting_rules_read` / `alerting_rules_write`,
    `alerting_silences_read` / `alerting_silences_write`,
    `alerting_manage_routing`, `create_annotation`, `generate_deeplink`.

## Host-Profile (`~/.config/ssh-mcp/config.toml`)

| Profile | Host | Zweck |
|---------|------|-------|
| `docker-host` | 10.0.10.10 | Docker Host (CT 100) – hier läuft Grafana |

## Richtlinien
- **NIEMALS raw SSH via bash** (`ssh Docker '...'`, `bash("ssh ...")`) –
  ausschließlich `ssh-mcp`-Tools; Profil `docker-host` (siehe
  `docker-host-location`-Skill).
- Keine Secrets, Tokens oder private Keys im Repo.

## Skills
`docker-host`, `docker-host-compose`, `docker-host-filesystem`, `docker-host-git`,
`docker-host-location` (Krefeld → `docker-host`), `obsidian`,
`grafana-oss`, `dashboarding`, `alerting-irm`.
> Der Skill `opencode` ist **built-in** (von OpenCode selbst mitgebracht) und wird
> trotzdem immer mitgeladen.

## Referenzen
- mcp-grafana: https://github.com/grafana/mcp-grafana
- Vendored Skills: https://github.com/grafana/skills (siehe `skills/grafana/README.md`)
- Grafana HTTP-API (Fallback/Backup): `https://grafana.mueller-nas.de/api/…` mit
  `Authorization: Bearer $GRAFANA_API_KEY`
