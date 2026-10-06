---
name: google-home-exposure
description: Configure Home Assistant entities for Google Home/Assistant exposure. Load when setting up voice control or exposing devices to Google.
license: MIT
---

# Google Home Exposure

Configure which Home Assistant entities and domains are exposed to Google Home/Assistant.

## When to Load This Skill

Load when:
- Setting up Google Assistant integration
- Exposing new devices to Google Home
- Troubleshooting voice control issues
- Creating script wrappers for buttons

## Configuration

Entity exposure is configured in `configuration.yaml` under the `google_assistant` integration.

### Expose Entities

```yaml
google_assistant:
  entity_config:
    light.wohnzimmer:
      name: Wohnzimmer Licht
      expose: true
    switch.steckdose_tv:
      name: TV Steckdose
      expose: true
```

### Expose by Domain

```yaml
google_assistant:
  exposed_domains:
    - light
    - switch
    - climate
    - cover
```

## Script Wrappers for Buttons

Google Home cannot trigger buttons directly. Create scripts to wrap button presses:

```yaml
script:
  gc_button_press:
    alias: "GC Button"
    sequence:
      - service: button.press
        target:
          entity_id: button.gc
```

Then expose the script to Google:

```yaml
google_assistant:
  entity_config:
    script.gc_button_press:
      expose: true
      name: "GC drücken"
```

## Troubleshooting

### Entity not showing in Google Home
- Check `expose: true` in config
- Reload Google Assistant integration
- Sync devices in Google Home app

### Voice command not working
- Check entity name matches what you're saying
- Verify entity is exposed
- Check Google Home app for errors

## Related Skills

- **Home Assistant:** `skill({name:"home-assistant"})` for HA configuration
