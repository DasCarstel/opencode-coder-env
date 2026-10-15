# OpenBao – Secrets & Access Management

OpenBao (Vault-Fork) verwaltet alle Secrets für die Coder-Infrastruktur.

**Adresse:** https://openbao.mueller-nas.de

## Policies

| Policy | Zweck | Berechtigungen |
|--------|-------|----------------|
| `mcp-server` | Coder-Workspaces | secret/data/mcp/* (read, list), secret/data/ssh/unifi (read), secret/metadata/mcp/* (list, read), secret/metadata/ssh/unifi (read), auth/token/renew-self (update), auth/token/revoke-self (update), ssh/sign/host-access (update) |
| `host-access` | Host-Zugriff | ssh/sign/host-access (update) |
| `mcp-server-general` | General-Workspace (least privilege) | secret/data/mcp/{github,opencloud-ocis,opencode-go,hermes-opencode-service,ssh-mcp-pc} (read), secret/metadata/mcp/{github,opencloud-ocis,opencode-go,hermes-opencode-service,ssh-mcp-pc} (list, read), auth/token/renew-self (update), auth/token/revoke-self (update) |

## AppRoles

| AppRole | Policy | secret_id_ttl | secret_id_num_uses | token_max_ttl |
|---------|--------|---------------|-------------------|---------------|
| `mcp-server` | mcp-server | 0 (nie) | 0 (unbegrenzt) | 24h |
| `host-access` | host-access | 0 (nie) | 0 (unbegrenzt) | 24h |
| `mcp-general` | mcp-server-general | 0 (nie) | 0 (unbegrenzt) | 24h |

## Secrets

| Pfad | Inhalt |
|------|--------|
| secret/data/mcp/homeassistant | api_key – Home Assistant Long-Lived Token |
| secret/data/mcp/authentik | api_key – Authentik API-Key |
| secret/data/mcp/grafana | api_key – Grafana API-Key |
| secret/data/mcp/opencloud | api_key, username – OpenCloud Zugang |
| secret/data/mcp/opencloud-ocis | obsidian_private_user, obsidian_private_token, obsidian_muellerconnect_user, obsidian_muellerconnect_token – oCIS App-Tokens pro Obsidian-Vault (Hard-Guardrail) |
| secret/data/mcp/opencode-go | default1, default2, default3 – OpenCode Go API-Keys (der **aktive** Account steht in der Hestia-SOT `.opencode/hestia-accounts.jsonc`) |
| secret/data/mcp/ssh-mcp | private_key, public_key – statischer SSH-Key für ssh-mcp |
| secret/data/mcp/ssh-mcp-pc | private_key, public_key – exklusiver SSH-Key für den Carsten-PC (Windows); wird **nicht** mit `ssh-mcp` geteilt |
| secret/data/mcp/tailscale | auth_key – Tailscale Auth-Key (eigenes Coder↔Kleve-Tailnet) |
| secret/data/mcp/github | api_key – GitHub PAT (git clone/push in allen Workspaces) |
| secret/data/mcp/artificialanalysis | api_key – Artificial Analysis API-Key (Modell-Benchmarks der Hestia, optional; Policy `hestia` hat Leserecht) |
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

Erstellt idempotent: Policies (inkl. `mcp-server-general`), AppRoles (inkl.
`mcp-general`), verteilt CA-Key. Zusätzlich wird der Key `secret/data/mcp/ssh-mcp-pc`
erzeugt und in OpenBao gespeichert; am Ende gibt das Skript den
Public-Key des Carsten-PC und die Role-ID von `mcp-general` aus (für die
Coder-Parameter `openbao_role_id` / `openbao_approle_secret_id` des `general`-Templates).

Neues Secret-ID ausstellen:

    bash scripts/bootstrap-hosts.sh --issue-secret-id

## Coder-Integration

Das Secret-ID wird als Default im Template main.tf gesetzt und bei
coder update infrastructure automatisch injiziert.
