# OpenBao – Secrets & Access Management

OpenBao (Vault-Fork) verwaltet alle Secrets für die Coder-Infrastruktur.

**Adresse:** https://openbao.mueller-nas.de

## Policies

| Policy | Zweck | Berechtigungen |
|--------|-------|----------------|
| `mcp-server` | Coder-Workspaces | secret/data/mcp/* (read, list), secret/data/ssh/unifi (read), secret/metadata/mcp/* (list, read), secret/metadata/ssh/unifi (read), auth/token/renew-self (update), auth/token/revoke-self (update), ssh/sign/host-access (update) |
| `host-access` | Host-Zugriff | ssh/sign/host-access (update) |

## AppRoles

| AppRole | Policy | secret_id_ttl | secret_id_num_uses | token_max_ttl |
|---------|--------|---------------|-------------------|---------------|
| `mcp-server` | mcp-server | 0 (nie) | 0 (unbegrenzt) | 24h |
| `host-access` | host-access | 0 (nie) | 0 (unbegrenzt) | 24h |

## Secrets

| Pfad | Inhalt |
|------|--------|
| secret/data/mcp/homeassistant | api_key – Home Assistant Long-Lived Token |
| secret/data/mcp/authentik | api_key – Authentik API-Key |
| secret/data/mcp/grafana | api_key – Grafana API-Key |
| secret/data/mcp/opencloud | api_key, username – OpenCloud Zugang |
| secret/data/mcp/opencloud-ocis | obsidian_private_user, obsidian_private_token, obsidian_muellerconnect_user, obsidian_muellerconnect_token – oCIS App-Tokens pro Obsidian-Vault (Hard-Guardrail) |
| secret/data/mcp/opencode-go | default1, default2, default3, active – OpenCode Go API-Keys |
| secret/data/mcp/ssh-mcp | private_key, public_key – statischer SSH-Key für ssh-mcp |
| secret/data/mcp/tailscale | auth_key – Tailscale Auth-Key (eigenes Coder↔Kleve-Tailnet) |
| secret/data/mcp/github | api_key – GitHub PAT (git clone/push in allen Workspaces) |
| secret/data/ssh/unifi | UniFi SSH-Zugang |

## SSH-CA

OpenBao fungiert als SSH-Certificate Authority. Der CA-Key wird auf
docker, proxmox und truenas unter /etc/ssh/openbao-ca.pub abgelegt.

Signierung: ssh/sign/host-access (valid_principals: root, TTL 24h)

> **Hinweis:** Die CA-Zertifikate werden nur vom interaktiven `ssh`
> (`~/.ssh/config`) genutzt. **ssh-mcp** verwendet stattdessen den statischen
> Key aus `secret/data/mcp/ssh-mcp`, weil die von ssh-mcp genutzte `ssh2`-Library
> keine OpenSSH-CA-Zertifikate unterstützt. Dessen Public-Key liegt in
> `authorized_keys` der Zielhosts.

## Bootstrap

    export OPENBAO_ROOT_TOKEN=<root-token>
    bash scripts/bootstrap-hosts.sh

Erstellt idempotent: Policies, AppRoles, verteilt CA-Key.

Neues Secret-ID ausstellen:

    bash scripts/bootstrap-hosts.sh --issue-secret-id

## Coder-Integration

Das Secret-ID wird als Default im Template main.tf gesetzt und bei
coder update infrastructure automatisch injiziert.
