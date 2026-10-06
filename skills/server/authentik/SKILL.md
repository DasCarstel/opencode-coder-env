---
name: authentik
description: Authentik SSO via the community authentik-mcp server. Load for app integration, policy management or SSO troubleshooting.
license: MIT
---

# Authentik SSO Configuration

## Community MCP server

`authentik-mcp` (nikitatsym/authentik-mcp) exposes the Authentik API as MCP
tools. It is configured in
`workspaces/infrastructure/server-management/opencode.json`:

```json
{
  "mcp": { "servers": { "authentik": {
    "type": "local",
    "command": ["/root/.local/bin/uvx", "--extra-index-url", "https://nikitatsym.github.io/authentik-mcp/simple", "authentik-mcp"],
    "environment": {
      "AUTHENTIK_URL": "https://auth.mueller-nas.de",
      "AUTHENTIK_TOKEN": "{env:AUTHENTIK_API_KEY}"
    }
  } } }
}
```

The token comes from OpenBao `secret/data/mcp/authentik` (key `api_key`) and is
injected into the OpenCode service environment as `AUTHENTIK_API_KEY`.

## Tool groups

| Group | Operations |
|-------|-----------|
| `authentik_read` | Users, groups, apps, tokens, providers, outposts, crypto, RBAC (read-only) |
| `authentik_write` | Create/update core resources (non-destructive) |
| `authentik_delete` | Delete operations (destructive) |
| `authentik_flows_read` | Flows, stages, policies, sources, events (read-only) |
| `authentik_flows_write` | Create/update auth pipeline config |
| `authentik_admin` | Admin settings, system info, lifecycle |

Call any group with `operation="help"` to list its operations.

## Integration decision flow

1. Check `https://integrations.goauthentik.io/<app-name>/` for a dedicated guide.
2. **OAuth2/OIDC or SAML** → native SSO (OAuth2 Provider).
3. **Proxy Provider** → ForwardAuth.
4. **No integration** → ForwardAuth as fallback.

**Always prefer native SSO when available.**

## Sub-skills

- `authentik-integration` – policy stack, adding a new app, Traefik labels
- `authentik-troubleshooting` – common errors and fixes

## Related

- `docker-host` – managing the Authentik containers on the Docker host
