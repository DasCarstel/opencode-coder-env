---
name: authentik
description: Authentik SSO configuration via community MCP server. Load for app integration, policy management, or troubleshooting SSO issues.
license: MIT
---

# Authentik SSO Configuration

## Community MCP Server

The `authentik-mcp` server (nikitatsym/authentik-mcp) provides comprehensive Authentik API integration.

### Configuration

```json
{
  "mcp": {
    "servers": {
      "authentik": {
        "type": "local",
        "command": ["/root/.local/bin/uvx", "--extra-index-url", "https://nikitatsym.github.io/authentik-mcp/simple", "authentik-mcp"],
        "environment": {
          "AUTHENTIK_URL": "https://auth.mueller-nas.de",
          "AUTHENTIK_TOKEN": "{env:AUTHENTIK_API_KEY}"
        }
      }
    }
  }
}
```

The token is retrieved from OpenBao at `secret/data/mcp/authentik` (key `api_key`) and injected into the OpenCode service environment as `AUTHENTIK_API_KEY`.

## Available Tool Groups

| Group | Operations |
|-------|-----------|
| `authentik_read` | Users, groups, apps, tokens, providers, outposts, crypto, RBAC (read-only) |
| `authentik_write` | Create/update core resources (non-destructive) |
| `authentik_delete` | Delete operations (destructive) |
| `authentik_flows_read` | Flows, stages, policies, sources, events (read-only) |
| `authentik_flows_write` | Create/update auth pipeline config |
| `authentik_admin` | Admin settings, system info, lifecycle |

Call any group with `operation="help"` to list available operations.

## Integration Strategy

### Decision Flow

1. Check `https://integrations.goauthentik.io/<app-name>/` for dedicated integration guide
2. **If OAuth2/OIDC or SAML** → use native SSO (OAuth2 Provider)
3. **If Proxy Provider** → use ForwardAuth
4. **If no integration** → ForwardAuth as fallback

### Native SSO vs Proxy

| Aspect | Native SSO (OIDC/SAML) | Proxy/ForwardAuth |
|--------|------------------------|-------------------|
| Session management | App-managed | Outpost checks every request |
| Logout propagation | Automatic | No automatic forwarding |
| API access | App tokens work | Requires `skip_path_regex` |
| Configuration | App-specific | Universal |

**Always prefer native SSO when available.**

## Standard Policy Stack

Every application follows this Order-based policy chain:

```
Order 0: Default Deny      → blocks everyone by default
Order 1: <group> binding   → allows specific group members
Order 2: Allow Superuser   → allows superusers (admin)
```

### Recommended Groups by App Category

| App Category | Example Apps | Group |
|-------------|-------------|-------|
| Admin-only | Proxmox, Portainer, Traefik | `admin` |
| Media | Sonarr, Radarr, Jellyfin | `medien-tools` |
| Productivity | Stirling PDF, Excalidraw | `produktivitaet` |
| Monitoring | Uptime Kuma, Homarr | `monitoring` |
| Public | Homer dashboard | No Deny, just Group |

**CRITICAL: Always ask the user which group to apply.**

## Adding a New App

### Workflow

1. Check https://integrations.goauthentik.io/<app>/ for native SSO
2. If native SSO → create OAuth2 Provider
3. If Proxy → create Proxy Provider + Application
4. Apply standard policy stack (Default Deny → Group → Superuser)
5. Assign to Outpost
6. Add Traefik labels: `authentik@docker`
7. Verify login flow

### Traefik Labels

```yaml
labels:
  - "traefik.http.routers.<app>-secure.middlewares=authentik@docker"
  - "traefik.docker.network=proxy"
```

## Troubleshooting

### "no app for hostname"
- Provider has `assigned_application_slug` set to null
- Fix: Create application with provider PK already set

### Access Denied
- Check provider `external_host` matches browser URL
- Check provider is assigned to outpost
- Check policy order (Deny=0, Group=1, Superuser=2)
- Check user is in the group
- Check Traefik middleware is in labels

## Related Skills

- **Docker Host:** `skill({name:"docker-host"})` for managing Authentik containers
- **Home Assistant:** `skill({name:"home-assistant"})` for HA integration
