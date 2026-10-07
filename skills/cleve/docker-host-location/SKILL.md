---
name: docker-host-location
description: Resolves which ssh-mcp profile to use for Docker ops in this workspace. Kleve Docker Host (192.168.178.52, via Tailscale) → profile "cleve-docker". Load together with the shared docker-host skills.
license: MIT
---

# Docker Host Location – Kleve

This workspace manages the **Kleve** Docker host. Use these values in the
shared docker-host skills wherever `profile:"<docker-profil>"` appears.

## Mapping

| Location | Profile | Host | Notes |
|----------|---------|------|-------|
| **Kleve** (this workspace) | `cleve-docker` | 192.168.178.52 (via Tailscale) | `/opt/docker-compose` |
| Krefeld | `docker-host` | 10.0.10.10 (CT 100) | only in the **server-management** workspace |

## Rule

- Default profile here: **`cleve-docker`**
- If the user asks for Krefeld explicitly, say so and use `docker-host` — but
  note that the Krefeld-specific skills (and its location mapping) live in the
  server-management workspace, not here.
- Always state the profile you are about to use before running commands.

## Host details

- Reaches the Kleve LAN (192.168.178.0/24) over the Tailscale subnet route
  (`docker-kleve`, see the cleve AGENTS.md)
- Runs: immich, jellyfin, homeassistant, portainer, mqtt5, homer, tailscale
