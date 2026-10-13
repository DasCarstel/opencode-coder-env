# Hermes (Host-Compose)

Der **Hermes-Agent** (Nous Research) läuft als dauerhafter Dienst auf dem
**Coder-Host** (`10.0.10.17`) — nicht als Coder-Template. Er bekommt keine
Workspace-Session, sondern Modell-, Provider- und Secret-Konfiguration von
**Hestia** verteilt.

Details und Betrieb: [`../docs/hermes.md`](../docs/hermes.md).

## Komponenten

| Datei | Inhalt |
|-------|--------|
| [`Dockerfile`](Dockerfile) | Basis-Image `nousresearch/hermes-agent:latest` + `opencode-mcp@3.0.0` + `ocis-mcp-server` v1.1.0, danach wieder `USER hermes` |
| [`docker-compose.yml`](docker-compose.yml) | Service `hermes`, Netz `coder-infra` (extern), Port **9119**, Neustart `unless-stopped` |

Der Container nutzt das **Entrypoint des Basis-Images**
(`/opt/hermes/docker/entrypoint-dispatch.sh`) — es gibt hier bewusst **kein**
eigenes `entrypoint.sh`. Die früher im Repo liegende `entrypoint.sh`
(Host-Variante des alten Coder-Templates) ist tote Historie und wurde entfernt.

## Bind-Mounts (Laufzeit-Zustand)

```yaml
- /etc/docker/hermes/data:/opt/data                  # Volume: config.yaml, profiles/
- /opt/docker-compose/hermes/.env:/opt/data/.env    # einzelne Datei: Secrets/Pool
```

Beides liegt **außerhalb** dieses Repos:

- `/etc/docker/hermes/data` ist ein Docker-Volume und enthält `config.yaml` sowie
  `profiles/<name>/{config.yaml,.env}`.
- `.env` ist eine einzelne, von Hestia geschriebene Datei direkt im
  Deploy-Verzeichnis auf dem Coder-Host.

## Keine Secrets im Repo

Weder `Dockerfile` noch `docker-compose.yml` enthalten Zugangsdaten. Die früheren
Umgebungsvariablen `OPENBAO_ROLE_ID` und `OPENBAO_SECRET_ID` sind **entfernt** —
sie gehörten zur gelöschten `entrypoint.sh` und werden vom aktuellen Deployment
nicht ausgewertet.

Der Laufzeit-Zustand (`.env`, `config.yaml`) wird gepflegt von
[`hestia/ci/apply-hermes.sh`](../hestia/ci/apply-hermes.sh) aus der SOT
[`.opencode/hestia-hermes.jsonc`](../.opencode/hestia-hermes.jsonc); der
rsync im Deploy-Workflow schließt `.env` deshalb explizit aus.

## Deploy

Nicht manuell, sondern per Workflow:

```text
Push auf master (Pfade hermes/** oder .github/workflows/deploy-hermes.yml)
  → .github/workflows/deploy-hermes.yml
      rsync hermes/ → root@10.0.10.17:/opt/docker-compose/hermes/   (.env ausgeschlossen)
      docker compose build && docker compose up -d                  (bewusst ohne --pull)
```

Details: [`../docs/hermes.md`](../docs/hermes.md#deploy).