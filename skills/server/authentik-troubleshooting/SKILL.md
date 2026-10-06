---
name: authentik-troubleshooting
description: Fix common Authentik SSO problems (no app for hostname, access denied, missing Traefik middleware).
license: MIT
---

# Authentik – Troubleshooting

## "no app for hostname"

- The provider's `assigned_application_slug` is null.
- Fix: create the application with the provider PK already set.

## Access denied

Check, in order:

- Provider `external_host` matches the browser URL.
- Provider is assigned to the outpost.
- Policy order is correct (Deny=0, Group=1, Superuser=2).
- The user is in the bound group.
- The Traefik middleware is present in the container labels.

## Related

- `authentik` – MCP setup and tool groups
- `authentik-integration` – policy stack and app setup
