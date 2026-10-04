---
name: home-assistant
description: Home Assistant diagnostics: log analysis, config editing, .storage inspection, git management at /etc/docker/homeassistant
license: MIT
---

## Quick Reference (MCP Cheat Sheet)

| Was | MCP-Tool |
|-----|----------|
| Log lesen/durchsuchen | `ssh-multi` → `exec({host:"Docker", command:"grep ... /etc/docker/homeassistant/home-assistant.log"})` **oder** `homeassistant` → `get_error_log()` / `get_support_logs()` |
| Log vorheriger Session | gleicher Befehl mit `...log.1` |
| Config lesen | `filesystem-docker-host` → `read_text_file` |
| Config editieren | `filesystem-docker-host` → `edit_file` (erst `dryRun: true`) |
| .storage lesen | `filesystem-docker-host` → `read_text_file` |
| Git Status | `git-docker-host` → `git_status()` |
| Git Commit | `git-docker-host` → `git_add` + `git_commit` |
| YAML validieren | `ssh-multi` → `exec({host:"Docker", command:"docker exec homeassistant ha core check"})` |
| HA neustarten | `ssh-multi` → `exec({host:"Docker", command:"docker restart homeassistant"})` |
| HA reload (YAML) | UI: Einstellungen → System → YAML-Konfiguration |

## Direkter HA-MCP-Server (`homeassistant`)

Der `homeassistant`-MCP-Server läuft standalone gegen die HA-Core-API (env: `HA_API_BASE_URL`, Token via `HA_LLAT`). Er ist `ssh-multi`/`filesystem-docker-host` für Live-Daten vorzuziehen.

| Was | MCP-Tool |
|-----|----------|
| States aller Entities / Domain | `homeassistant` → `get_states({domain:"light"})` |
| Entity suchen | `homeassistant` → `search_entities({query:"Wohnzimmer"})` |
| Service aufrufen (Modifies State) | `homeassistant` → `call_service({domain, service, target, service_data})` |
| Verlauf / History | `homeassistant` → `get_history({entity_id})` |
| Logbook | `homeassistant` → `get_logbook({entity_id})` |
| Template rendern | `homeassistant` → `render_template({template})` |
| Config prüfen (YAML) | `homeassistant` → `validate_config()` |
| Areas / Devices | `homeassistant` → `get_areas()` / `get_devices()` |
| Kalender | `homeassistant` → `get_calendars()` |
| Anomalien / Diagnose | `homeassistant` → `detect_anomalies()` / `diagnose_entity()` |
| ESPHome (Ingress) | `homeassistant` → `esphome_*` (Token via `HA_ACCESS_TOKEN`) |
| Logs (API) | `homeassistant` → `get_error_log({lines})` / `get_support_logs({source})` |
| Builder/Admin | `homeassistant` → `hab_run({command})` — Dashboard/Automation/Script/Scene/Helper CRUD, Areas/Labels/Persons, repairs, notifications |
| Zigbee | `homeassistant` → `zigporter_run({command})` — Cascade-Rename, Device-Inspektion, Stale-Cleanup, Mesh-Map |
| Supervisor/Updates | `homeassistant` → `get_supervisor_health()`, `update_component()`, `get_update_progress()` |

## Verzeichnisstruktur

```
/etc/docker/homeassistant/          ← Config (in Container /config)
├── configuration.yaml               ← Haupt-Config
├── automations.yaml                 ← Automationen
├── scripts.yaml                     ← Skripte
├── .gitignore
├── .storage/                        ← HA-interne DB (Entity-Registry, Device-Registry, Input-Booleans etc.)
├── home-assistant.log               ← Aktuelle Session
├── home-assistant.log.1             ← Vorherige Session
└── home-assistant_v2.db             ← SQLite-DB

/opt/docker-compose/homeassistant-mqtt5-zigbee2mqtt/
└── docker-compose.yml               ← HA + MQTT + Zigbee2MQTT
```

## Log-Daten

**Format:** `YYYY-MM-DD HH:MM:SS.mmm LEVEL (Thread) [component] message`

**Wichtige grep-Muster:**
```bash
# Alle ERROR/WARNING an einem Tag
grep "YYYY-MM-DD" /etc/docker/homeassistant/home-assistant.log | grep "ERROR\|WARNING"

# Bestimmte Automation
grep "automation.AUTOMATION_NAME" .../home-assistant.log

# Script-Fehler
grep "\[homeassistant.components.script" .../home-assistant.log

# Sensor-Status
grep "sensor.SENSOR_NAME" .../home-assistant.log
```

## Diagnose-Workflow

1. **Log** → `grep "YYYY-MM-DD" .../log | grep "ERROR\|WARNING"`
2. **Automation/Script** → `grep "AUTOMATION_NAME" .../log`
3. **Sensor** → `grep "SENSOR_NAME" .../log`
4. **Device suchen** → Device-ID aus YAML → `read_text_file({path:"/etc/docker/homeassistant/.storage/core.device_registry"})`
5. **Helper suchen** → Input-Booleans, Timer etc. → `read_text_file({path:"/etc/docker/homeassistant/.storage/input_boolean"})`
6. **Reload** → UI: Einstellungen → System → YAML-Konfiguration

## Git (.gitignore)

Nur 5 YAML-Dateien getrackt, Rest ignoriert:
```
*
!.gitignore
!configuration.yaml
!automations.yaml
!groups.yaml
!scenes.yaml
!scripts.yaml
```

## Google Assistant Integration

Für Google-Home-Exposure (Entities/Domains freigeben, Buttons via Scripts wrappen):
→ `skill({name:"google-home-exposure"})`
