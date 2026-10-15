# OpenBao – Secrets & Access Management

OpenBao (Vault-Fork) verwaltet alle Secrets für die Coder-Infrastruktur.

**Adresse:** https://openbao.mueller-nas.de

## Policies

| Policy | Zweck | Berechtigungen |
|--------|-------|----------------|
| `mcp-server` | Coder-Workspaces | secret/data/mcp/* (read, list), secret/data/uni/* (read, list), secret/data/ssh/unifi (read), secret/metadata/mcp/* (list, read), secret/metadata/uni/* (read, list), secret/metadata/ssh/unifi (read), auth/token/renew-self (update), auth/token/revoke-self (update), ssh/sign/host-access (update) |
| `host-access` | Host-Zugriff | ssh/sign/host-access (update) |
| `mcp-server-general` | General-Workspace (least privilege) | secret/data/mcp/{github,opencloud-ocis,opencode-go,hermes-opencode-service,ssh-mcp-pc} (read), secret/metadata/mcp/{github,opencloud-ocis,opencode-go,hermes-opencode-service,ssh-mcp-pc} (list, read), auth/token/renew-self (update), auth/token/revoke-self (update) |
| `mcp-server-uni` | uni-Workspace (least privilege) | secret/data/uni/* (read, list), secret/metadata/uni/* (list, read), secret/data/mcp/{github,opencloud-ocis,opencode-go,hermes-opencode-service,ssh-mcp-pc} (read), secret/metadata/mcp/{github,opencloud-ocis,opencode-go,hermes-opencode-service,ssh-mcp-pc} (list, read), auth/token/renew-self (update), auth/token/revoke-self (update) |

## AppRoles

| AppRole | Policy | secret_id_ttl | secret_id_num_uses | token_max_ttl |
|---------|--------|---------------|-------------------|---------------|
| `mcp-server` | mcp-server | 0 (nie) | 0 (unbegrenzt) | 24h |
| `host-access` | host-access | 0 (nie) | 0 (unbegrenzt) | 24h |
| `mcp-general` | mcp-server-general | 0 (nie) | 0 (unbegrenzt) | 24h |
| `mcp-uni` | mcp-server-uni | 0 (nie) | 0 (unbegrenzt) | 24h |

Role-ID `mcp-uni` (live): `0727dc69-cf01-014c-d3c5-a907499d0e43` — das Skript liest sie
zur Laufzeit aus und gibt sie am Ende aus; sie ist **nicht** hart im Skript hinterlegt.

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
| secret/data/uni/fernuni-ocis | user, token, password, space_id, url – oCIS App-Token des Service-Accounts `fernuni-obsidian` für den Space „FernUni" (FernUni/Obsidian-Second-Brain) |
| secret/data/uni/moodle | username, password, base_url – FernUni-Moodle-Zugang für die Sync-Pipeline (lokaler Login, kein SSO/2FA) |

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

Erstellt idempotent: Policies (inkl. `mcp-server-general` und `mcp-server-uni`),
AppRoles (inkl. `mcp-general` und `mcp-uni`), verteilt CA-Key. Die Policy
`mcp-server` wird dabei **additiv** um `secret/data/uni/*` und
`secret/metadata/uni/*` erweitert (bestehende Regeln bleiben erhalten).
Zusätzlich wird der Key `secret/data/mcp/ssh-mcp-pc`
erzeugt und in OpenBao gespeichert; am Ende gibt das Skript den
Public-Key des Carsten-PC sowie die Role-IDs von `mcp-general` und `mcp-uni` aus (für die
Coder-Parameter `openbao_role_id` / `openbao_approle_secret_id` des `general`-Templates
bzw. des uni-Workspace).

Die Policies werden über `/v1/sys/policies/acl/<name>` gelesen und geschrieben
(Feld `data.policy`) — **nicht** über `/v1/sys/policy/acl/<name>`, das eine Policy
mit Slash im Namen adressiert.

Neue Secret-ID ausstellen (Modus ohne Bootstrap — Policies, Rollen und
CA-Verteilung werden nicht angefasst):

    export OPENBAO_ROOT_TOKEN=<root-token>
    bash scripts/bootstrap-hosts.sh --issue-secret-id [ROLLE]     # Default: mcp-uni

Die Secret-ID wird **nie** auf stdout ausgegeben; sie landet 0600 in
`~/.openbao/<rolle>.secret-id` (Pfad per `OPENBAO_SECRET_ID_FILE` übersteuerbar).
stdout nennt nur Länge, Gültigkeit (`secret_id_ttl`) und Nutzungen
(`secret_id_num_uses`). Auf dem Docker-Host liegt der Root-Token in
`/opt/docker-compose/openbao/bootstrap.env` (0600), Aufruf dort z. B.:

    set -a; . /opt/docker-compose/openbao/bootstrap.env; set +a
    bash scripts/bootstrap-hosts.sh --issue-secret-id mcp-uni

Vorhandene Secret-IDs listet `bao list auth/approle/role/<rolle>/secret-id`
(Accessor-IDs); jederzeit gültig ist die Secret-ID nur bei `secret_id_ttl 0`
und `secret_id_num_uses 0` — beides ist für `mcp-uni` gesetzt.

## Coder-Integration

Der datascience-Workspace loggt sich mit der Least-Privilege-AppRole `mcp-uni`
ein. Role-ID und Secret-ID sind **Coder-Parameter** des Templates
(`openbao_role_id`, `openbao_approle_secret_id`; beide Default leer, `mutable`)
— im Repo liegt kein Secret.

* **Role-ID** von `mcp-uni`: `0727dc69-cf01-014c-d3c5-a907499d0e43` (steht auch
  in der Ausgabe von `bootstrap-hosts.sh`).
* **Secret-ID**: wie oben ausstellen und aus der Datei lesen
  (`cat ~/.openbao/mcp-uni.secret-id`) — nie in Logs oder Ausgaben kopieren.
* Für einen neuen Workspace beide Werte setzen, z. B.
  `coder update <workspace> --parameter openbao_role_id=… --parameter openbao_approle_secret_id=…`
  oder im Coder-Panel unter „Parameters". Fehlen sie, bricht `startup.sh`
  fail-closed ab (Docker/Jupyter/`~/uni` laufen, OpenCode/Secrets/Sync nicht).
