---
name: docker-host-location
description: Resolves which ssh-mcp profile to use for Docker ops in this workspace. Krefeld Docker Host (CT 100) → profile "docker-host". Load together with the shared docker-host skills.
license: MIT
---

# Docker Host Location – Krefeld

This workspace manages the **Krefeld** Docker host. Use these values in the
shared docker-host skills wherever `profile:"<docker-profil>"` appears.

## Mapping

| Location | Profile | Host | Notes |
|----------|---------|------|-------|
| **Krefeld** (this workspace) | `docker-host` | 10.0.10.10 (CT 100) | `/opt/docker-compose`, `/etc/docker` (Git repo) |
| Kleve | `cleve-docker` | 192.168.178.52 (via Tailscale) | only in the **cleve** workspace |

## Rule

- Default profile here: **`docker-host`**
- If the user asks for Kleve explicitly, say so and use `cleve-docker` — but
  note that the Kleve-specific skills (and its location mapping) live in the
  cleve workspace, not here.
- Always state the profile you are about to use before running commands.
