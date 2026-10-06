---
name: home-assistant
description: Home Assistant diagnostics and configuration via native MCP server. Load for HA troubleshooting, config editing, or automation work.
license: MIT
---

# Home Assistant Skill

## Native MCP Server

The `homeassistant` MCP server is configured in `opencode.json`:

```json
{
  "mcp": {
    "servers": {
      "homeassistant": {
        "type": "remote",
        "url": "https://intern-homeassistant.mueller-nas.de/api/mcp",
        "headers": {
          "Authorization": "Bearer {env:HA_LLA_TOKEN}"
        }
      }
    }
  }
}
```

## Available Operations

| Category | Operations |
|----------|-----------|
| States | Get states, search entities, call services |
| History | Get history, logbook, render templates |
| Config | Validate config, get areas/devices, calendars |
| Diagnostics | Detect anomalies, diagnose entities |
| Logs | Get error logs, support logs |
| Admin | Dashboard/automation/script CRUD, repairs, notifications |
| ESPHome | ESPHome device management |
| Zigbee | Zigbee device inspection, cleanup, mesh map |
| Supervisor | Health checks, updates, component management |

## Directory Structure

```
/etc/docker/homeassistant/          ← Config (in Container /config)
├── configuration.yaml              ← Main config
├── automations.yaml                ← Automations
├── scripts.yaml                    ← Scripts
├── .gitignore
├── .storage/                       ← HA internal DB (entity registry, etc.)
├── home-assistant.log              ← Current session log
└── home-assistant.log.1            ← Previous session log

/opt/docker-compose/homeassistant-mqtt5-zigbee2mqtt/
└── docker-compose.yml              ← HA + MQTT + Zigbee2MQTT
```

## Diagnostic Workflow

1. **Check logs** → Use `homeassistant` MCP to get error logs
2. **Identify component** → Look for errors in specific automation/script/sensor
3. **Diagnose entity** → Use `diagnose_entity()` for detailed analysis
4. **Validate config** → Use `validate_config()` after changes
5. **Reload** → UI: Settings → System → YAML Configuration

## Git Rules

Only 5 YAML files are tracked:
```
*
!.gitignore
!configuration.yaml
!automations.yaml
!groups.yaml
!scenes.yaml
!scripts.yaml
```

## Related Skills

- **Google Home Exposure:** `skill({id:"google-home-exposure"})` for exposing entities to Google Assistant
