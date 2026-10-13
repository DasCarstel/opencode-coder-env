# Hestia — einmaliges Setup (CI/CD-Infrastruktur)

Dieses Setup ist **ein Mal** auszuführen. Danach deploys und rollt alles über
GitHub Actions ohne manuelle Schritte.

## 1. Self-hosted GitHub-Runner auf dem Docker-Host (CT 100)

```bash
# Auf dem Docker-Host ausführen (via ssh-mcp privileged-command):
HESTIA_GITHUB_TOKEN='<Runner-Registration-Token aus GitHub: Repo → Settings →
Actions → Runners → New self-hosted runner → im Token-Block kopiert>'
bash /opt/docker-compose/github-runner/setup-runner.sh   # Skript unten einmalig deployen
```

Vorbereitetes Skript: `hestia/ci/setup/setup-runner.sh`
- lädt actions-runner-linux-x64 nach `/opt/docker-compose/github-runner`
- registriert mit Label `docker-host` (`--labels docker-host`)
- installiert als systemd-service `github-runner-hestia`

## 2. SSH-Deploy-Key Coder-Host (CT 120)

```bash
# Auf dem Docker-Host:
ssh-keygen -t ed25519 -f /opt/docker-compose/github-runner/coderdeploy_key -N ""
# Public-Key auf dem Coder-Host freischalten:
ssh root@10.0.10.17 'mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys' \
  < /opt/docker-compose/github-runner/coderdeploy_key.pub
# ⚠ nur den PRIVATE-Key als GitHub-Secret speichern:
#   Repo → Settings → Secrets → CODER_DEPLOY_KEY (Inhalt der Coderdeploy_key-Datei)
```

## 3. GitHub-Secrets (Repo → Settings → Secrets and variables → Actions)

| Secret | Inhalt |
|---|---|
| `CODER_DEPLOY_KEY` | Inhalt von `coderdeploy_key` (privat, ssh-ed25519) — **einzige notwendige Secret** |

Damit laufen **beide** Workflows: `deploy-hestia` (Panel ausrollen) und
`rollout` (Coder-Container neu starten). Es wird **kein Coder-API-Token** benötigt:
der Rollout läuft per SSH auf den Coder-Host und startet die OpenCode-Container
neu — deren Entrypoint zieht beim Start das frische Repo.

## 4. OpenBao für den Hestia-Container

Einmalig auf dem Coder-Host in `/opt/docker-compose/hestia/.env` (irgendwo neben
docker-compose.yml) eintragen:

```
HESTIA_OPENBAO_ROLE_ID=<AppRole-Role-ID 'hestia'>
HESTIA_OPENBAO_SECRET_ID=<AppRole-Secret-ID (nicht planbar; regeneriert TTLs)>
```

OpenBao-Policy (auf dem OpenBao-Host anlegen, z.B. via `bao` CLI — CI-seitig
nicht automatisiert):

```hcl
# policy: hestia
path "secret/data/mcp/github"                  { capabilities = ["read"] }
path "secret/data/mcp/hermes-opencode-service" { capabilities = ["read"] }
path "secret/data/mcp/hestia"                  { capabilities = ["read"] }
```

AppRole `hestia` mit überig Zeitlimit scripten (role TTL 24h,
secret_id_num_uses=0). Und `secret/data/mcp/hestia` einmal anlegen:

```
bao kv put secret/mcp/hestia password=<GLEICHES Passwort wie GitHub-Secret HESTIA_PASSWORD>
```

## 5. Manuelle Deploy-Erstrung (falls der Runner fehlt)

```bash
rsync -a hestia/ root@10.0.10.17:/opt/docker-compose/hestia/
ssh root@10.0.10.17 "cd /opt/docker-compose/hestia && docker compose up -d --build"
```

## Runner-Watchdog (Broker-Ausfall)

Der Self-hosted Runner verliert gelegentlich die Broker-Verbindung zu GitHub
(`ERR BrokerServer` / `Back off 8,636 seconds`); Jobs bleiben dann stundenlang
`queued`, obwohl der Runner `online`/`busy` meldet. `runner-watchdog.sh`
erkennt das im neuesten `_diag/Runner_*.log` und startet den Service neu
(Cooldown 30 min). Installation (systemd-Unit + Timer, alle 5 min) steht als
Kommentar im Skriptkopf; auf dem Docker-Host liegt es unter
`/opt/docker-compose/github-runner/watchdog.sh`.
