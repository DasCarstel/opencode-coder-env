---
name: authentik-integration
description: Add an application to Authentik and apply the standard policy stack (deny -> group -> superuser) with Traefik labels.
license: MIT
---

# Authentik – Adding an Application

## Standard policy stack

Every application follows this order-based chain:

```
Order 0: Default Deny      -> blocks everyone by default
Order 1: <group> binding   -> allows specific group members
Order 2: Allow Superuser   -> allows superusers (admin)
```

### Recommended groups

| App category | Examples | Group |
|--------------|----------|-------|
| Admin-only | Proxmox, Portainer, Traefik | `admin` |
| Media | Sonarr, Radarr, Jellyfin | `medien-tools` |
| Productivity | Stirling PDF, Excalidraw | `produktivitaet` |
| Monitoring | Uptime Kuma, Homarr | `monitoring` |
| Public | Homer dashboard | No deny, group only |

**CRITICAL: always ask the user which group to apply.**

## Workflow

1. Check `https://integrations.goauthentik.io/<app>/` for native SSO.
2. Native SSO -> create an OAuth2 Provider; Proxy -> create a Proxy Provider + Application.
3. Apply the standard policy stack (Default Deny -> Group -> Superuser).
4. Assign to the Outpost.
5. Add Traefik labels.
6. Verify the login flow.

## Traefik labels

```yaml
labels:
  - "traefik.http.routers.<app>-secure.middlewares=authentik@docker"
  - "traefik.docker.network=proxy"
```

## Related

- `authentik` – MCP setup and tool groups
- `authentik-troubleshooting` – error fixes
