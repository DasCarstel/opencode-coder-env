---
name: google-home-exposure
description: Home Assistant Entities und Domains für Google Home/Assistant freigeben oder ausblenden. Workflow für neue Geräte, Buttons via Script-Wrapper, Troubleshooting.
license: MIT
---

# Google Home Exposure Skill

## Where the config lives

```
/etc/docker/homeassistant/configuration.yaml  →  google_assistant: block
/etc/docker/homeassistant/scripts.yaml         →  script definitions (button wrappers)
```

---

## Two-tier exposure model

| Tier | YAML key | Effect |
|------|----------|--------|
| Domain-wide | `exposed_domains` | ALL entities of this domain appear in Google Home |
| Per-entity | `entity_config` | Override specific entities: `expose: true` or `expose: false` |

### Critical rule

`entity_config` with `expose: true` works WITHOUT the domain in `exposed_domains` for **climate, sensor, switch** — but NOT for **script**. Scripts only appear if `script` is in `exposed_domains`.

---

## Decision matrix

| What you want | Strategy |
|---------------|----------|
| Most entities of a domain → Google Home | Add to `exposed_domains`, then `expose: false` for exceptions |
| Only a few entities → Google Home | Keep domain OUT of `exposed_domains`, add `expose: true` per entity |
| HA-Buttons via voice | Create a script wrapper → expose the script |
| New entity from an already-exposed domain | Nothing needed (or add `expose: true` if domain NOT in `exposed_domains`) |
| Docker containers / UniFi stuff | KEEP `switch` OUT of `exposed_domains` — expose individually |

---

## Current config snapshot (reference)

```
exposed_domains:  [cover, fan, input_boolean, light, scene, script, vacuum]

entity_config (expose: true):
  climate:  better_thermostat_zimmer, _badezimmmer, better_wohnkuche, midea AC
  switch:   windmaschine, logitech_speaker, sonoff_1000497296, steckdose_server, steckdose_3d_drucker
  sensor:   12 SwitchBot thermometer (6 rooms × temp + humidity)
  script:   5 Roborock (everything auto-exposed via domain, 22 others hidden)

entity_config (expose: false):
  input_boolean: zahneputz_blocker, dusch_blocker, aroma_diffuser_script_running, wecker_aktiv, toilette_benachrichtigung_gorkem
  light:  a1_03919c462700797_chamber_light (Bambu printer)
  fan:    a1_03919c462700797_aux_fan, _cooling_fan (Bambu printer)
  script: 22 non-Roborock scripts (spotify, jalousie, heating, aroma, debug, etc.)
```

---

## Workflow: Add new entity to Google Home

### Step 1: Find the entity ID
HA UI → Einstellungen → Geräte & Dienste → Entitäten → search → note the full `domain.name` (e.g. `climate.wohnkuche`)

### Step 2: Determine strategy
- Same domain already in `exposed_domains`? → either nothing needed, or add `expose: false` if you want to hide others
- New domain? → add to `exposed_domains`
- It's a button? → create script wrapper first (see below), then the script is auto-exposed
- Single entity from domain NOT in `exposed_domains`? → add `entity_config` entry with `expose: true`

### Step 3: Edit configuration.yaml
Use `filesystem-docker-host → edit_file` with `dryRun: true` first, then `false`.

### Step 4: Validate
```bash
ssh-multi → exec({host:"Docker", command:"docker exec homeassistant python -m homeassistant --script check_config --config /config"})
```

### Step 5: Git commit
```bash
git-docker-host → git_set_working_dir({path:"/etc/docker/homeassistant"})
git-docker-host → git_add({paths:["configuration.yaml"]})
git-docker-host → git_commit({message:"feat/fix: <what changed>"})
```
Or if scripts.yaml was also changed:
```bash
git-docker-host → git_add({paths:["configuration.yaml","scripts.yaml"]})
```

### Step 6: Restart HA
```bash
ssh-multi → exec({host:"Docker", command:"docker restart homeassistant"})
```

### Step 7: Sync with Google
"Hey Google, synchronisiere meine Geräte"

---

## Templates

### Template A: New domain (all entities exposed)

Add to `exposed_domains`:
```yaml
    - my_new_domain
```

If you need to hide specific entities from this domain, add in `entity_config`:
```yaml
    my_new_domain.unwanted_entity:
      expose: false
```

### Template B: Single entity (domain NOT in exposed_domains)

In `entity_config`:
```yaml
    my_domain.my_entity:
      expose: true
      name: Friendly Name  # optional
```

### Template C: Button → Script wrapper (for voice control of buttons)

1. Add to `scripts.yaml`:
```yaml
my_script_id:
  alias: Friendly Name for Google Home
  sequence:
  - action: button.press
    target:
      entity_id: button.exact_button_entity_id
  mode: single
  icon: mdi:some-icon
```

2. The script is auto-exposed (since `script` is in `exposed_domains`). No configuration.yaml changes needed.

### Template D: Hide entities from domain in exposed_domains

In `entity_config`:
```yaml
    domain.unwanted_entity:
      expose: false
```

---

## Common pitfalls

| Problem | Cause | Fix |
|---------|-------|-----|
| Scripts not visible in Google Home | `script` missing from `exposed_domains` | Add `script` to `exposed_domains` and `expose: false` all unwanted scripts |
| New Docker container switch appears | `switch` is in `exposed_domains` | Keep `switch` OUT, expose individually |
| Button not usable by voice | Google Home doesn't support `button` entities | Create a script wrapper |
| Entity config changes ignored | Only `exposed_domains`-listed domains are processed | Add domain to `exposed_domains` or use `expose: true` explicitly |
| "Synchronisiere Geräte" doesn't pick up changes | HA restart needed after config change | Always restart HA first, then sync |
