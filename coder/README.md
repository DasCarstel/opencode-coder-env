# Coder-Server (Host-Compose)

Der **Coder-Server** — also die Coder-Instanz, die alle Workspace-Container
provisioniert — läuft selbst als dauerhafter Compose-Stack auf dem
**Coder-Host** (`10.0.10.17`) unter `/opt/docker-compose/coder/`.

Er ist **kein** Coder-Template und keine Workspace: Er ist die Steuerzentrale,
die die Workspaces erst startet.

> ⚠️ **Steuerzentrale — besonders vorsichtig deployen**
> Dieser Stack ist die Grundlage der gesamten Fleet. Ein fehlerhafter
> Compose-Stand, ein überschriebener `.env` oder ein `docker compose up -d`
> **ohne Service-Angabe** nimmt die Coder-Instanz samt Datenbank und damit allen
> Workspaces den Boden unter den Füßen weg. Deshalb:
> - Der Deploy läuft **ausschließlich manuell** (`workflow_dispatch`), nie bei
>   einem Push.
> - Immer **erst validieren** (`docker compose config -q`), dann **nur** den
>   Service `coder` starten — **niemals** `database`, **niemals** ohne
>   Service-Angabe.
> - `.env` wird beim rsync ausgeschlossen und nie ins Repo gegeben.

## Komponenten

| Datei | Inhalt |
|-------|--------|
| [`docker-compose.yml`](docker-compose.yml) | Services `coder` (Container `coder`, Port **7080**) und `database` (Container `coder-postgres`) |
| [`.env.example`](.env.example) | Vollständige Vorlage der `.env` — **nur Platzhalter, keine echten Werte** |

| Service | Image | Aufgabe |
|---------|-------|---------|
| `coder` | `ghcr.io/coder/coder:latest` | Der Coder-Server, Provisionierung der Workspaces per Docker-Provider |
| `database` | `postgres:17` | Postgres-Datenbank; `pg_isready`-Healthcheck, `coder` startet erst nach `service_healthy` |

Netzwerke: `coder-internal` (bridge, nur der Stack) sowie die **externen**
Netze `coder-gen`, `coder-mc`, `coder-infra` — der Server muss die
Workspace-Container in allen drei Netzen erreichen.

## Bind-Mounts (Laufzeit-/Host-Zustand — nicht im Repo)

```yaml
- /var/run/docker.sock:/var/run/docker.sock   # Docker-Provider
- /etc/docker/coder/home:/home/coder          # Daten des Coder-Servers
- /etc/docker/coder/postgres:/var/lib/postgresql/data   # Postgres-Daten
- .env                                        # env_file: Zugangsdaten, OIDC, Keys
```

Alles außer `docker-compose.yml` ist **Host-Zustand und liegt außerhalb dieses
Repos**:

- `.env` existiert **nur** unter `/opt/docker-compose/coder/.env` auf dem Host.
  Sie enthält das Postgres-Passwort, das OIDC-Client-Secret und die
  Token-Encryption-Keys. `.env.example` im Repo ist die Pflege-Vorlage — nach
  einer Änderung an der echten `.env` die Vorlage aktualisieren, aber **niemals
  die Werte übernehmen**.
- `/etc/docker/coder/postgres` enthält die **Datenbank**. Sie ist der einzige
  Ort mit dem Produktiv-Datenbestand (Templates, Workspaces, Audit-Log) und
  darf durch keinen Deploy angefasst werden.
- `/etc/docker/coder/home` ist das Home-Verzeichnis des Servers im Container.

## Deploy

Bewusst **manuell**, nicht per Push:

```text
GitHub → Actions → deploy-coder.yml (workflow_dispatch, von Hand gestartet)
  1. Deploy-Key ~/.ssh/id_ed25519_coderdeploy hinterlegen, ssh-keyscan 10.0.10.17
  2. rsync coder/ → root@10.0.10.17:/opt/docker-compose/coder/   (.env + .env.bak* ausgeschlossen)
  3. docker compose config -q        → Validierung; bricht bei Fehler ab, es wird nichts gestartet
  4. docker compose up -d coder      → NUR der Server; database wird nie angefasst (bewusst ohne --pull)
  5. Healthcheck: docker inspect -f '{{.State.Running}}' coder == true (Retry-Schleife)
```

Das Image ist ein floating `:latest`-Tag. Ein `--pull` würde bei jedem Deploy
ungekündigt eine neue Version einspielen — bei der Steuerzentrale ein
unkontrollierbarer Versionssprung. Bewusste Updates erfolgen durch
`docker compose pull coder` von Hand.

## Wenn ein Deploy fehlschlägt

1. **Job-Look ansehen** — der Validierungs-Schritt (`docker compose config -q`)
   nennt die Ursache (typisch: YAML-Fehler oder fehlende `.env` auf dem Host).
2. **Nichts weiter deployen**, solange die Validierung schlägt: die laufende
   Instanz ist danach noch intakt, nur der neue Stand liegt auf der Platte.
3. **Vorgänger-Stand zurückholen** und nur den Server neu starten:

   ```bash
   cd /opt/docker-compose/coder
   # im geklonten Repo, Pfad ggf. anpassen:
   git -C <pfad-zum-repo> show HEAD~1:coder/docker-compose.yml > docker-compose.yml
   docker compose config -q && docker compose up -d coder
   ```

4. **Prüfen**: `docker ps --filter name=coder` zeigt `coder` und
   `coder-postgres`, `docker logs --tail=50 coder` ist fehlerfrei, die
   Coder-UI auf `https://coder.mueller-nas.de` lädt und die Templates sind da.
5. **`coder-postgres` nicht neu erstellen** — das Neuerzeugen eines
   Postgres-Containers ohne `--volumes` ist harmlos (die Daten liegen im
   Bind-Mount `/etc/docker/coder/postgres`), aber ein `docker compose down`
   **mit** `-v` oder ein `docker volume prune` wäre Datenverlust. Im Zweifel:
   nur `coder` anfassen.

## Verwandte Doku

- [`../docs/architecture.md`](../docs/architecture.md) — Systemüberblick
- [`../docs/coder-templates.md`](../docs/coder-templates.md) — die Templates, die
  dieser Server provisioniert
- [`../docs/secrets.md`](../docs/secrets.md) — Secret-Flow OpenBao → Container