# Coder-Templates

Vier Templates, ein gemeinsames Setup-Skript. Jeder Container startet über
`entrypoint.sh`, das `coder-templates/shared/startup.sh <profil>` im Hintergrund
startet und danach den Coder-Agent-Bootstrap per `exec "$@"` ausführt.

> Der **Hermes-Agent** ist kein Coder-Template, sondern ein Host-Deployment auf
> dem Coder-Host — siehe [`hermes.md`](hermes.md).

## Die vier Templates

| Template | Netz | Image / Container | `startup.sh`-Profil |
|----------|------|-------------------|---------------------|
| `infrastructure` | `coder-infra` | `workspace-infrastructure:v2` | `infrastructure` |
| `general` | `coder-gen` | `workspace-general:v2` | `general` |
| `muellerconnect` | `coder-mc` | `workspace-muellerconnect:v2` | `muellerconnect` |
| `datascience` | `coder-ds` | `workspace-datascience:v2` | `datascience` |

### `infrastructure`
HA-, Server-Management-, Svelte- und Cleve-Workspaces. `main.tf`-Besonderheiten:
zusätzliche Capabilities (`NET_ADMIN`) und `/dev/net/tun` für Tailscale, ein
eigenes persistentes Tailscale-State-Volume, Netzwerk `coder-infra`. Coder-App
„Infrastructure Workspace" → `herdr`.

### `general`
Isolierter Workspace: oCIS-MCP (privater Obsidian-Vault), PDF-MCP und `ssh-mcp` mit
**ausschließlich** dem Carsten-PC (`config-general.toml`). `main.tf`-Besonderheiten:
Netzwerk `coder-gen` (kein internes VLAN), eigener Session-Pfad
`/etc/opencode-data/<user>-general`. Der Container bekommt die Zugangsdaten über eine eigene,
minimal berechtigte AppRole `mcp-general`; Role-ID und Secret-ID kommen aus den Coder-Parametern
`openbao_role_id` und `openbao_approle_secret_id` (beide standardmäßig leer). Coder-App „General Workspace".

### `muellerconnect`
Wie `general`, aber Arbeits-Vault: Netzwerk `coder-mc`, eigener Session-Pfad
`/etc/opencode-data/<user>-mc`. Coder-App „MuellerConnect Workspace".

### `datascience` (FernUni Data Engineering)

Workspace für das Modul „Data Engineering für Data Science" mit **Docker-in-Docker**:
die Studierenden starten im Workspace `./Deploy-DeDs-Rocket.sh`/`-Firework.sh`/`-Candle.sh`
aus dem FernUni-Repo, was per `docker compose` einen Stack (Jupyter/PySpark-Client +
Hadoop-Edge + 2 Worker) hochzieht, und dazu alpine-/mongodb-Container aus Notebooks.

| Punkt | Wert |
|-------|------|
| RAM | **Workspace: 8 GiB** (`memory = 8192`), `cpu_shares = 4096`, `shm_size = 2048` (MongoDB/Spark). Davon lauffähig: `deds candle` (~2 GiB) und `deds firework` (~4 GiB); `deds rocket` (~12 GiB) läuft **nicht** (Details unten). **Proxmox CT 120 braucht ≥ 16 GiB** (`pct set 120 -memory 16384`), weil Coder + Workspaces denselben Host teilen. |
| DinD | `privileged = true`, `cgroupns_mode = "host"`, `security_opts = ["apparmor=unconfined","seccomp=unconfined"]`. **Kein Host-Socket.** |
| Storage-Treiber | Treiber-Probe in `uni-bootstrap.sh`: `overlay2` → `fuse-overlayfs` → `vfs`; Ergebnis + Compose-Version + cgroup-Check in `~/uni/logs/dockerd.log` |
| Compose | `docker-compose-plugin` aus dem Docker-Repo (≥ 2.32) |
| Ports | 8890 unser JupyterLab (Kernel „Python (ml)" / „Python (dl)"), 8888 Jupyter des FernUni-Stacks, 4040–4050 Spark-UI, 9000/9870/8088 Hadoop (Namenode/WebUI/ResourceManager – erst nach „Firework"/„Rocket") |
| Volumes | `coder-<owner>-<ws>-data` → `~/opencode-coder-env` (reproduzierbar: Repo-Klon), **Host-Bind-Mount `/etc/coder/uni-data` → `~/uni`** (kein Volume, keine Terraform-Ressource – überlebt das Löschen/Neuanlegen des Workspace, siehe unten), `coder-<owner>-<ws>-docker` → `/var/lib/docker` (Images/Container überleben „Stop"/„Restart", **bewusst pro Workspace**), Sessions: `/etc/opencode-data/<user>-datascience` |
| AppRole | `mcp-uni` (Least Privilege, fail-closed in `startup.sh`) — Secrets `secret/data/uni/fernuni-ocis` (user, token, password, space_id, url), `secret/data/uni/moodle` (username, password, base_url) und `secret/data/mcp/github` (`api_key`, nur für die privaten Modul-Repos; die Policy `mcp-server-uni` erlaubt genau diesen einen mcp-Pfad) |
| Modul-Repos | vier private GitHub-Repos `fernuni-<modulnummer>-<slug>`, lokal in `~/uni/module/<modulnummer>-<slug>`; Abgleich per `uni-git-setup.sh` (Log `~/uni/logs/git-setup.log`) — Details unten |
| MCP | nur FernUni-oCIS + pdf (`workspaces/uni/workspace-global.json`); das private/muellerconnect-oCIS-Token wird im Service aktiv `unset` |
| Sync | `fernuni-sync/` läuft per `sync-loop.sh` beim Start + alle 6 h (Log `~/uni/logs/sync.log`); Zustand/config/env in `~/uni/state` → Neustart ist idempotent („0 Downloads") |

**Bekannte Einschränkungen (Stand 2026-10-15, im Workspace `ds-test` verifiziert):**

- `deds candle|firework|rocket` startet den Stack **im Vordergrund** (die
  FernUni-Skripte nutzen `docker compose up` ohne `-d`) – das Terminal bleibt
  belegt. Stop mit `deds down`; Strg-C beendet nur die Ausgabe, der Container
  hat `restart: unless-stopped`.
- Für „Firework"/„Rocket" werden zusätzlich die `hadoop-node:0.3.6-rootless`-Images
  gezogen (zusammen ~2,8 GiB, anonym); `deds candle` zieht nur den
  `pyspark-client-node` (~3,7 GiB entpackt/1,3 GiB Download).
- **Der Material-Baum gehört dem Sync** (Regel seit Commit `0e91a25`,
  2026-10-15): kennt `~/uni/state/state.json` eine Datei nicht, die im
  oCIS-Space schon liegt (z. B. Host-Ordner neu, Vault voll), wird sie
  **übernommen** (gleiche Größe = inhaltsgleich, kein Download) oder sonst
  **überschrieben** — nie mehr dupliziert. Vorher wich der Sync auf
  `…-generated-2.<ext>` aus; bei frischem State traf das auf **jede**
  Material-Datei zu (207 Duplikate, in jedem Lauf erneut geladen). Die
  `-generated-2`-Ausweichung gilt nur noch für **Notizen** ohne
  `generated: true` (Nutzerdateien werden nie überschrieben). Ist der State
  synchron, meldet der Lauf `0 Download(s)`.
- **Duplikate aufgeräumt (geprüft 2026-10-15):** der FernUni-oCIS-Space
  enthält **261 Dateien, 0 Treffer** auf `…-generated-2.<ext>` und
  `0 *.generated-2.md` — der frühere Desync-Rest (207 Kopien) ist weg, der
  Material-Baum ist eindeutig; der manuelle Aufräumschritt entfällt.
- **Musterlösungs-Ordner sind gesperrt, nicht leer:** alle sechs Ordner
  „Musterlösung - Lektion 1…6" in Kurs 6556 hängen an einer
  Moodle-Verfügbarkeitsbedingung (Freigaben 16.10.2026, 31.10.2026,
  16.11.2026, 01.12.2026, 16.12.2026, 08.01.2027). `/mod/folder/view.php`
  leitet dafür auf die Kursseite um („availabilityinfo-error: Verfügbar ab …").
  Früher meldete der Sync „Ordner … ohne Dateiliste"; seit Commit `46a1188`
  (2026-10-15) nennt er die Sperre (`🔒 Verfügbar ab …`) und ruft die Seite
  gar nicht mehr ab. Die Dateien landen nach der jeweiligen Freigabe
  automatisch beim nächsten Sync-Lauf.
- **`deds rocket` ist im 8-GiB-Workspace nicht lauffähig:** das Rocket-Compose
  (`docker-compose.yml`) setzt `memory` 2048M + 4096M + 4096M + 2048M
  (Jupyter-PySpark-Client, zwei Worker, Edge) ≈ **12 GiB**; der Workspace hat
  `memory = 8192`. Nutzbar sind `deds candle` (nur Client, 2 GiB) und
  `deds firework` (mit `docker-compose-small.yml`: 768M + 1408M + 1408M + 512M
  ≈ 4 GiB). Verifiziert 2026-10-15 im Workspace `ds-test`.
- Ein Coder-`restart` baut das Workspace-Image neu (`CACHE_DATE` im `Dockerfile`)
  — der Neustart dauert dadurch ~4–6 min. Der Host-Ordner `~/uni` und die
  beiden workspace-eigenen Volumes bleiben dabei erhalten.
- **`~/uni` ist ein Host-Bind-Mount** auf `/etc/coder/uni-data` (seit 2026-10-16;
  vorher ein festes Docker-Volume `coder-uni-<owner>-data` mit
  `lifecycle { prevent_destroy = true }`): Löschen und Neuanlegen des Workspace
  verlieren den Studienstand **nicht** mehr, ohne den Workspace unzerstörbar zu
  machen (siehe „Was überlebt was" unten).
- **`/var/lib/docker` bleibt bewusst pro Workspace** (`coder-<owner>-<ws>-docker`):
  zwei gleichzeitig laufende Workspaces mit demselben Docker-`data-root` würden
  sich gegenseitig zerstören (gemeinsamer Image-Store, gleiche Container-Namen,
  gleiche Ports). Preis: ein **neuer** Workspace zieht die ~2,8 GiB
  FernUni-Images einmal neu — reproduzierbar, kein Datenverlust.
- `herdr ls` gibt es nicht (Herdr hat kein `ls`-Subkommando); Status über
  `herdr status server`, Workspaces über `herdr workspace <subcommand>`.

Setup-Reihenfolge im Container: `entrypoint.sh` (Tools + Repo) →
`uni-bootstrap.sh` im Hintergrund (dockerd → `~/uni`-Struktur →
FernUni-Repo klonen → `workspace/`-Symlink im FernUni-Klon →
uv-Umgebungen ml/dl → JupyterLab 8890 → `startup.sh datascience` →
Modul-Repos).

### Modul-Repos (ein privates Git-Repo pro Studienmodul)

Jedes Pflichtmodul hat ein **eigener privates GitHub-Repo**. Zweck: Versionierung
der eigenen Arbeiten, Zugriff vom PC — und vor allem **Wiederherstellung, falls
das `~/uni`-Verzeichnis wirklich mal neu/leer ist** (fremder Owner, Host-Ordner
von Hand geleert): dann klont `uni-git-setup.sh` alle vier Repos aus GitHub zurück.

| Moodle-Kurs | Modulnummer | Modul | Lokaler Pfad (`~/uni/module/…`) | GitHub-Repo (privat) |
|---|---|---|---|---|
| 6556 | 63123 | Data Engineering für Data Science | `63123-data-engineering-fuer-data-science` | `DasCarstel/fernuni-63123-data-engineering-fuer-data-science` |
| 6591 | 64401 | Maschinelles Lernen | `64401-maschinelles-lernen` | `DasCarstel/fernuni-64401-maschinelles-lernen` |
| — | 64511 | Einführung in Data Science | `64511-einfuehrung-in-data-science` | `DasCarstel/fernuni-64511-einfuehrung-in-data-science` |
| — | 61811 | Mathematische Grundlagen von Data Science | `61811-mathematische-grundlagen-von-data-science` | `DasCarstel/fernuni-61811-mathematische-grundlagen-von-data-science` |

Namensschema: `fernuni-<modulnummer>-<slug>`. Jedes Repo enthält eine
`README.md` (Modulnummer, Modulname, Moodle-Kurs-ID, Verweis auf den Vault-Pfad
`10-Semester/1-Semester/<…>`) und eine `.gitignore` (Python-Artefakte, Daten
`*.csv`/`*.parquet`/…, `.env`; **Notebooks `.ipynb` werden versioniert**).
Die **Moodle-Materialien liegen im Vault/oCIS und werden nicht hier versioniert.**

**Sonderfall 63123 (Data Engineering):** der Ordner `workspace/` dieses Repos ist
das **Bind-Mount-Ziel des FernUni-Compose-Stacks** (Details unten) — dort liegen
die Notebooks, die im Stack entstehen. Er ist deshalb eingecheckt
(`workspace/README.md`, `workspace/.gitkeep`) und **darf nicht gelöscht** werden.

`coder-templates/datascience/uni-git-setup.sh` (idempotent, läuft im Hintergrund
aus `uni-bootstrap.sh`, Log `~/uni/logs/git-setup.log`) stellt den Zustand her:

| Fall | Verhalten |
|---|---|
| `~/uni/module/<slug>/.git` vorhanden | nur `git remote get-url origin` prüfen — **nichts** ändern; `git pull --ff-only` nur bei sauberem Arbeitsbaum (sonst überspringen + melden) |
| `.git` fehlt, Remote erreichbar | `git clone <url> ~/uni/module/<slug>` |
| `.git` fehlt, Remote fehlt/gesperrt | `git init -b main` + Remote + `README.md` + `.gitignore` + Erstcommit + `push -u origin main` |

**Token:** gelesen wird `secret/data/mcp/github` → `api_key` (Classic-PAT mit
Scope `repo`, Login `DasCarstel`), im Profil `datascience` per
`read_secret … GITHUB_TOKEN` in `shared/startup.sh` → landet in
`/etc/opencode.env` (0600, außerhalb des Repos). Der Token steht **nie** in
einem Repo, in einer Remote-URL oder in einem Log: die Remotes sind
`https://github.com/<owner>/<repo>.git` (tokenfrei), authentifiziert wird über
`credential.helper store` mit `/root/.git-credentials` (0600) — dieselbe
Konvention wie `scripts/setup-git-credentials.sh`. `url.insteadOf` wurde
bewusst **nicht** gewählt, weil das den Token dauerhaft in `~/.gitconfig`
schreiben würde.

Neuer Workspace von Hand (statt auf `uni-bootstrap.sh` zu warten):

```bash
bash ~/opencode-coder-env/coder-templates/datascience/uni-git-setup.sh
```

Voraussetzung: `GITHUB_TOKEN` in der Umgebung oder in `/etc/opencode.env`
(dort 0600, außerhalb von Repo und `~/uni`). Fehlt der Token, werden die Repos
nur lokal angelegt (kein Push) — das Skript meldet das.

**Grenze des Tokens (belegt 2026-10-15):** Scopes sind `repo` + `workflow`. Anlegen
(`POST /user/repos`), Klonen und Pushen funktionieren; **Löschen geht nicht** —
`DELETE /repos/<owner>/<name>` antwortet mit HTTP 403 „Must have admin rights to
Repository." (Scope `delete_repo` fehlt). Repos müssen im Zweifel über die
GitHub-UI gelöscht werden.

### Was liegt wo – und was überlebt ein Löschen des Workspace?

`~/uni` ist ein **Host-Bind-Mount** auf den Ordner `/etc/coder/uni-data` des
Coder-Hosts. Der Ordner ist **keine Terraform-Ressource**: Terraform erzeugt,
verändert und löscht ihn nie. Ein `coder delete` (destroy) räumt nur Container
und die workspace-eigenen Volumes ab und fasst ihn **nicht an**; ein neu
angelegter Workspace mountet **denselben Studienstand** wieder ein (auch zwei
gleichzeitig laufende Workspaces teilen sich dieses `~/uni` – eine bewusste
Kopplung, weil nur ein Workspace gleichzeitig betrieben wird).

Einmalig auf dem Coder-Host anzulegen:

```bash
install -d -m 0755 /etc/coder/uni-data   # Container läuft als uid 0 → schreibt direkt
```

Vorher lag `~/uni` in einem festen Docker-Volume `coder-uni-<owner>-data` mit
`lifecycle { prevent_destroy = true }`. Das erreichte zwar „Daten überleben",
brach aber jedes `coder delete` hart mit `Instance cannot be destroyed` ab. Der
Host-Ordner leistet dasselbe, ohne den Workspace unzerstörbar zu machen.

| Daten | Ort | git-versioniert? | überlebt Workspace-Löschen |
|---|---|---|---|
| Moodle-Material, Notizen | **oCIS-Vault** (FernUni-Space, `10-Semester/…`); lokal nur der Sync-**State** | nein (Vault statt Git) | **ja** — liegt nicht im Container-Volume |
| Eigene Arbeiten je Modul | `~/uni/module/<modulnummer>-<slug>/` → private GitHub-Repos | **ja** (committen + pushen) | **ja** (GitHub) |
| DE-Notebooks aus dem Stack | `~/uni/module/63123-…/workspace/` (Bind-Mount-Ziel) | **ja** (63123-Repo) | **ja**, sofern committet + gepusht |
| uv-Umgebungen `~/uni/envs/{ml,dl}` (1,5 GiB) | Host-Ordner `/etc/coder/uni-data`, Marker `.ready` | nein | **ja** (Host-Ordner) – sonst reproduzierbar neu gebaut |
| FernUni-Klon `~/uni/data-engineering-infrastructure` | Host-Ordner `/etc/coder/uni-data` | nein (öffentliches GitLab) | **ja** (Host-Ordner) – sonst neu geklont |
| Sync-Zustand `~/uni/state/` (`state.json`, `fernuni-sync.config.json`, `fernuni-sync.env`) | Host-Ordner `/etc/coder/uni-data` | **nein** (enthält Zugangsdaten → gehört nie ins Repo) | **ja** (Host-Ordner) – sonst: erster Sync-Lauf baut ihn neu |
| Docker-Images des inneren Daemons (~2,8 GiB) | `/var/lib/docker` = `coder-<owner>-<ws>-docker` | nein | **nein** – bewusst pro Workspace, ein neuer Workspace zieht sie neu |
| OpenCode-Sessions/Chats | `/etc/opencode-data/<user>-datascience` (Host, kein Volume) | nein | ja (host-seitig) |
| `~/uni/tools/` (`prune_state.py`, `sync-check.sh`) | Host-Ordner `/etc/coder/uni-data` | **ja** – seit 2026-10-16 versioniert in `fernuni-sync/tools/` (siehe unten) | **ja** (Host-Ordner) |

**End-to-End belegt am 2026-10-16 (echtes `coder delete` + Neuanlage als Workspace
`carstenmueller2002/datascience` — der Workspace hieß davor
`carstenmueller2002/fernuni` und wurde an diesem Tag endgültig umbenannt):**
`coder delete -y` zerstörte 9 Terraform-Ressourcen (Container, beide
workspace-eigenen Volumes, das Image `workspace-datascience:v2`, Apps, Agent);
danach war der alte Name aus `coder list` verschwunden, ebenso der Container und
die workspace-eigenen (Fernuni-)Volumes. Die Neuanlage als `datascience` (Template
`datascience`, AppRole `mcp-uni` über die Parameter
`openbao_role_id`/`openbao_approle_secret_id`) startete um 06:39 UTC,
war um 06:42 `Started`/`healthy`. Werte vorher → nachher: `~/uni` **1,5 GiB /
63 680 Einträge → 1,5 GiB / 63 680 Einträge**; die Marker `envs/{ml,dl}/.ready`
trugen danach dieselben alten Zeitstempel (05:57:22 / 05:58:55 UTC) – es wurde
nichts neu gebaut; `uni-git-setup` meldete für alle vier Modul-Repos
„Repo vorhanden" (**kein** „klone"); der erste Sync-Lauf im neuen Container
(06:43–06:45) endete mit „0 Download(s), 209 unveraendert, 0 Fehler/Warnung(en),
489 DAV-Aufrufe · Ergebnis: ok". `state.json` wurde von diesem Lauf neu geschrieben
(md5 wechselte, Größe 82 633 B unverändert; Index: 188 Material- und 46 Notiz-
Einträge) – das ist normal, der Vergleichsbeleg ist die unveränderte Gesamtzahl.
dockerd (29.9.1, overlay2) und Jupyter (`:8890` → HTTP 200) liefen im neuen
Container. Einzige Neubeschaffung: der Image-Store des inneren Docker ist leer
(`docker images` = 0 Treffer auf `fernuni-hagen`) – die ~2,8 GiB FernUni-Images
(Hadoop-Edge/Worker bzw. `pyspark-client-node`) zieht der nächste `deds …`-Lauf
neu (einige Minuten, reproduzierbar; kein Datenverlust).

Wenn der Host-Ordner doch einmal leer/neu ist, baut sich der Workspace selbst
auf: Modul-Repos klonen (`uni-git-setup.sh`), Material mit dem oCIS-Space
abgleichen (Sync), uv-Umgebungen und Images ziehen. Verloren ist nur, was
**nirgends** versioniert/gesichert ist — deshalb Notebooks in `~/uni/module/…`
committen und pushen.

**Der Ordner liegt bewusst außerhalb von Terraform.** Er ist keine
`docker_volume`-Ressource und trägt kein `prevent_destroy`; ein `coder delete`
räumt den Container und die workspace-eigenen Volumes ab und blendet
`/etc/coder/uni-data` dabei überhaupt nicht ein. Das ist genau der
gewünschte Kompromiss: Der Studienstand überlebt ein Löschen des Workspace,
aber der Workspace bleibt löschbar (der frühere `prevent_destroy`-Ansatz
brach `coder delete` mit `Instance cannot be destroyed` ab). Zum endgültigen
Entfernen der Uni-Daten bleibt nur der Griff auf dem Coder-Host:

```bash
rm -rf /etc/coder/uni-data   # Vorsicht: ohne Rückfrage, kein Papierkorb
```

Deshalb gilt weiter: alles, was nicht reproduzierbar ist, gehört in die
privaten Modul-Repos (commit + push) oder in den oCIS-Vault.

### Wartungs-Tools: `fernuni-sync/tools/`

Zwei Helfer, die bisher **nur** im Ordner `~/uni/tools/` lagen (also im
damaligen Docker-Volume) und damit nirgends versioniert waren — bei Verlust
des Speichers wären sie weg gewesen. Sie liegen jetzt im Repo unter
`fernuni-sync/tools/`, weil beide direkt auf der Sync-Subsystem-Logik
(`fernuni-sync/sync.py`, `dav.py`) bzw. ihrem Zustand arbeiten; `scripts/` ist
für Host-/Provisionierungs-Skripte reserviert. Beide sind keine
Einmal-Wegwerf-Helfer: das Prune-Skript ist ein wiederkehrendes
Reparatur-Werkzeug, das Check-Skript erzeugt den Idempotenz-Beleg.

| Skript | Zweck | Aufruf im Workspace |
|---|---|---|
| `prune_state.py` | entfernt tote `-generated-2`-Einträge aus `~/uni/state/state.json` (nur die, deren Vault-Ziel per DAV-HEAD HTTP 404 liefert); Backup vor jeder Änderung | `python3 ~/opencode-coder-env/fernuni-sync/tools/prune_state.py` |
| `sync-check.sh` | State aufräumen + zwei Sync-Läufe hintereinander; der zweite muss „0 Download(s) … Ergebnis: ok" melden (Idempotenz-Beleg) | `bash ~/opencode-coder-env/fernuni-sync/tools/sync-check.sh` |

Pfade: `$UNI_DIR` schlägt alles andere, sonst `/home/$WORKSPACE_OWNER/uni`
(`WORKSPACE_OWNER` setzt das Template als Container-Env). Beide Skripte
schreiben nur nach `~/uni/logs/` (`state-prune.log`, `sync-check.log`) und
löschen **nichts** außer State-Einträgen, die im Vault nachweislich fehlen.

### FernUni-Stack starten (abgenommen 2026-10-15)

| Schritt | Befehl |
|---------|--------|
| kleinster Test (nur Jupyter-/PySpark-Client) | `deds candle` |
| Firework (kleine Maschinen) | `deds firework` |
| Rocket (voller Stack) | `deds rocket` |
| stoppen | `deds down` |

**Laufzeit-Verhalten (2026-10-16 erstmals in diesem Workspace gestartet):**

- **Start:** `deds candle` (nur Jupyter-/PySpark-Client-Node, Port 8888) oder
  `deds firework` (kleiner Cluster). `deds` läuft **im Vordergrund** – der
  Befehl kehrt nicht zurück, er hält Compose am Leben. Aus einem Skript/Agenten
  heraus deshalb abgesetzt starten, z. B.:
  `cd ~/uni/data-engineering-infrastructure && nohup env UID=1000 GID=1000 docker compose -f docker-compose.yml up client-node > ~/uni/logs/deds-candle.log 2>&1 &`
  (im Workspace-Container `coder-carstenmueller2002-datascience`).
- **Beenden:** `deds down` fährt Client-Node und ggf. Cluster herunter. Strg-C
  beendet nur die Ausgabe, **nicht** den Container.
- **Ein Workspace-Neustart überlebt der Stack nicht.** `docker compose up` ist
  ein Vordergrundprozess im Container; Coder startet nur den Container neu, nicht
  den Stack – nach Stop/Start oder Neustart des Workspace muss `deds candle`
  erneut laufen. Erhalten bleiben nur die **Daten** (Notebooks unter
  `~/uni/module/63123-data-engineering-fuer-data-science/workspace`, Host-Ordner
  `/etc/coder/uni-data`) und das Docker-Volume `…-datascience-docker`.
- **Kein Auto-Start:** Der Stack kommt beim Workspace-Start **nicht** von selbst
  hoch. Ob er das soll, ist eine Abwägung: dauerhaft ~2 GiB (`candle`) bzw.
  ~4 GiB (`firework`) RAM-Last im 8-GiB-Workspace gegen den Komfort, Jupyter auf
  8888 sofort ohne manuellen Start zu haben. **Nicht** implementiert – Entscheidung
  des Nutzers.
- **`hadoop-rm--…` (Port 8088) bleibt bei `deds candle` korrekt 502:** `candle`
  startet nur den Client-Node, es läuft kein Hadoop-ResourceManager. Erst
  `deds firework` (oder `deds rocket`) bringt 9000/9870/8088 hoch.
- Hinweis: Der Jupyter im Client-Node läuft **ohne eigene Authentifizierung**
  („All authentication is disabled", so konfiguriert das FernUni-Image) –
  erreichbar ist er über die Coder-App `fernuni-jupyter`, deren Zugriff Coder
  selbst (Session-Token/SSO) prüft.

**Warum der Wrapper `deds` (in `uni-bootstrap.sh` installiert)?** Die
FernUni-Skripte `Deploy-DeDs-*.sh` setzen im Compose `user: ${UID}:${GID}` aus
`id -u`. Im Workspace läuft alles als **root**, und Jupyter im
`pyspark-client-node` lehnt das ab:

```
[C …] Running as root is not recommended. Use --allow-root to bypass.
dbis-pyspark-client-node exited with code 1 (restarting)
```

`deds` erzwingt deshalb UID/GID **1000** (User `pyspark-client` im Image).
`uni-bootstrap.sh` setzt zusätzlich `chmod 666 /var/run/docker.sock` (nur der
Sockel des **inneren** Daemons – der Host-Socket wird bewusst nicht gemountet)
und Schreibrechte auf das Bind-Mount-Ziel, sonst kann UID 1000 nicht in den
Bind-Mount schreiben.

**Ablage der DE-Notebooks (Bind-Mount-Ziel):**
`~/uni/data-engineering-infrastructure/workspace` ist ein **Symlink** auf
`~/uni/module/63123-data-engineering-fuer-data-science/workspace` – den
`workspace/`-Ordner des **privaten 63123-Git-Repos**. Grund: ein Notebook, das
im Stack entsteht, liegt damit in einem Git-Repo (committen + pushen) und hängt
nicht mehr allein am `~/uni`-Host-Ordner; vorher zeigte der Symlink auf
`~/uni/workspace` – einen Ordner in **keinem** Repo. `uni-bootstrap.sh` setzt
und repariert den Symlink idempotent (`ln -sfn`), `uni-git-setup.sh` zieht ihn
nach dem Klon nach (in einem frischen Host-Ordner existiert das Ziel erst mit dem
Klon) und warnt, wenn dort uncommittete Dateien liegen. Den nicht mehr
verwendeten Ordner `~/uni/workspace` (enthielt nur `probe.txt`) entfernt
`uni-bootstrap.sh`; liegt dort echter Inhalt, wird **nicht** gelöscht, sondern
gewarnt.

Beim **ersten** Start – und nach jedem Verlust des Volumes (s. u.) – zieht der
Stack anonym von `registry.dbis-pro1.fernuni-hagen.de` (dauert einige Minuten;
2026-10-16 erneut gezogen: `pyspark-client-node:0.7.6-rootless`, **3,66 GB**
laut `docker images`). Die Images liegen in `/var/lib/docker` = Volume
`coder-carstenmueller2002-datascience-docker` und überstehen einen **Stop/Start
desselben** Workspace – **nicht** aber ein Umbenennen oder Neuaufbauen des
Workspace: dann greift Coder auf ein neues, leeres Volume zu. Genau das war am
2026-10-16 der Fall (`docker images` leer, deshalb der erneute Pull).

Voraussetzungen auf dem Coder-Host (CT 120):

```bash
docker network create coder-ds        # wie coder-gen/coder-mc: bridge, kein internes VLAN
pct set 120 -memory 16384             # auf dem Proxmox-Host; 8 GiB reichen fuer Coder + Datascience-Workspace nicht
```

Die beiden Coder-Template-Parameter `openbao_role_id` und `openbao_approle_secret_id`
müssen die AppRole **`mcp-uni`** enthalten (Role-ID/-Secret-ID aus `scripts/bootstrap-hosts.sh`),
sonst bricht `startup.sh` fail-closed ab (Docker/Jupyter/`~/uni` laufen trotzdem,
OpenCode/Secrets/Sync nicht).

Python-Umgebungen (idempotent, Marker `.ready`, Hintergrund, Log `~/uni/logs/env-setup.log`):

| env | Python | Pakete |
|-----|--------|--------|
| `~/uni/envs/ml` | 3.14.4 | numpy 2.4.4, pandas 3.0.2, matplotlib 3.10.9, scikit-learn 1.8.0 |
| `~/uni/envs/dl` | 3.12.0 | numpy 1.26.4, pandas 3.0.2, matplotlib 3.10.9, scikit-learn 1.8.0, tensorflow 2.17.0 |

## Aufrufkette

```
Docker-Start
  └─ ENTRYPOINT /usr/local/bin/entrypoint.sh
       1. Grund-Tools + Node 22 (nur wenn fehlend)
       2. Repo klonen bzw. git fetch + reset --hard FETCH_HEAD
       3. nohup: coder-templates/shared/startup.sh <profil>
       4. exec "$@"  → Coder-Agent-Bootstrap aus main.tf
```

`general` und `muellerconnect` nutzen denselben Ablauf mit eigenem `PROFILE`.

## `shared/startup.sh` — Ablauf

Aufruf: `startup.sh <infrastructure|general|muellerconnect|datascience>` — ein anderes oder
fehlendes Profil bricht mit Fehler ab.

1. **Versions-Pins laden** — `source ../versions.env`; fehlt die Datei, werden alle
   Pins leer gesetzt (Blöcke warnen, laufende Version bleibt).
2. **OpenBao AppRole-Login** — `POST /v1/auth/approle/login` mit
   `OPENBAO_ROLE_ID` (Default `mcp-server`) + `OPENBAO_APPROLE_SECRET_ID`; Token
   landet in `/etc/opencode.env`.
3. **Secrets laden** — profilabhängig via `read_secret <pfad> <key> <VAR>`:
   - `infrastructure`: HA (Krefeld + Kleve), authentik, grafana, opencloud,
     opencloud-ocis (privat), tailscale
   - `general`: opencloud-ocis (privat)
   - `muellerconnect`: opencloud-ocis (Arbeits-Vault)
   - **alle Profile**: `opencode-go` `default1..3`, `hermes-opencode-service`
     → Passwort für den OpenCode-Server
   Der OpenBao-Token selbst wird nicht weitergereicht.
4. **Git-Credentials** — `scripts/setup-git-credentials.sh` (GitHub-PAT aus OpenBao).
5. **SSH + ssh-mcp** *(`infrastructure`; `general` nur ssh-mcp mit PC-Config)* — ed25519-Key erzeugen, per
   `ssh/sign/host-access` (24 h) signieren, `~/.ssh/config` schreiben; statischen
   ssh-mcp-Key aus OpenBao installieren; `uvx` (für authentik) und **`ssh-mcp` per
   npm** installieren (Stempel-Datei, idempotent); `config.toml`,
   `config-full.toml` (Krefeld+Kleve+Carsten-PC)
   und `config-cleve.toml` (nur Kleve) unter `/root/.config/ssh-mcp/` erzeugen.
   Für `general` läuft ein eigener Abschnitt: `ssh-mcp` installieren, den exklusiven
   PC-Key `secret/data/mcp/ssh-mcp-pc` nach `/root/.ssh/id_ed25519_mcp_pc` schreiben und
   ausschließlich `config-general.toml` (Profil `carsten-pc`) erzeugen.
6. **Tailscale** *(nur `infrastructure`)* — `tailscaled` starten, `tailscale up`
   mit Auth-Key, `--accept-routes`, `--ssh=false`.
7. **OpenCode-CLI** — Abgleich gegen npm-latest, ggf. Neuinstallation + Service-Neustart
   (bewusst ungepinnt).
8. **Globale MCP-Config** — profilabhängig installiert:
   `infrastructure` → `coder-templates/infrastructure/config/global-opencode.json`
   (bewusst **leer**, MCP-Trennung läuft über die Projekt-Configs),
   `general`/`muellerconnect` → `workspaces/{general,minijob}/workspace-global.json`.
   Zusätzlich werden die Model-Router-Overrides global abgelegt.
9. **Env-Injektion** *(nur `infrastructure`)* — `HA_LLA_TOKEN`,
   `HA_KLEVE_LLA_TOKEN`, `AUTHENTIK_API_KEY` per `opencode service set env`.
10. **`oc-go`-Helper** — wird nach `/usr/local/bin/oc-go` geschrieben. Der aktive
    Key kommt aus der SOT `.opencode/hestia-accounts.jsonc` (Feld `active`);
    `OPENCODE_API_KEY` wird dedupliziert in `.bashrc` und ins (ephemere)
    Service-Env gesetzt. Siehe [`secrets.md`](secrets.md).
11. **Primary-Agents** — `derive-agents.py` (Details unten).
12. **OpenCode-Server** — `hostname 0.0.0.0`, Port **49374**, Passwort aus
    `hermes-opencode-service` (Fleet-Zugriff für Hestia/Hermes), Service-Restart.
13. **`ocis-mcp-server`** — gepinntes Go-Binary aus GitHub Releases nach
    `/root/.local/bin/ocis-mcp-server` (Stempel-Datei).
14. **oCIS-Guardrail** — das eigene Profil-Token wird ins Service-Env gesetzt, das
    jeweils andere aktiv **entfernt** (`opencode service unset env`). Ein
    Infrastructure-/General-Workspace kann den Arbeits-Vault physisch nicht sehen.
15. **Herdr** — Install auf Pin: Host-Bind-Mount `/opt/opencode-bin/herdr` bzw.
    Download von `herdr.dev`, Version wird verifiziert.
16. **Herdr-Workspaces** — Server starten, Workspaces idempotent anlegen und je
    einen OpenCode-Agenten starten:

    | Profil | Workspaces (Label → Verzeichnis) |
    |--------|----------------------------------|
    | `infrastructure` | Home Assistant, Server Management, Svelte, Cleve |
    | `general` | General |
    | `muellerconnect` | MuellerConnect |

17. **`ha-forward.py`** — startet bei Bedarf das HA-Kleve-TCP-Relay
    (`127.0.0.1:18123` → HA Kleve), damit Container ohne Tailscale den
    HA-MCP-Endpunkt erreichen. Läuft das Relay schon, wird es nicht neu gestartet.

## Die drei Profile im Vergleich

| Aspekt | `infrastructure` | `general` | `muellerconnect` |
|--------|------------------|-----------|------------------|
| Netz | `coder-infra` | `coder-gen` | `coder-mc` |
| SSH/ssh-mcp | ✅ | ✅ (nur Carsten-PC) | ❌ |
| Tailscale | ✅ | ❌ | ❌ |
| oCIS-Vault | privat | privat | Arbeit (MuellerConnect) |
| Extra-Secrets | HA, authentik, grafana, opencloud, tailscale | `ssh-mcp-pc` | — |
| Herdr-Workspaces | HA, Server Mgmt, Svelte, Cleve | General | MuellerConnect |

`muellerconnect` ist vollständig abgeschnitten: kein `ssh-mcp`, keine SSH-Keys, kein
internes VLAN. `general` ist ebenfalls abgeschnitten, mit einer Ausnahme: Es hat `ssh-mcp`
mit **nur** dem Carsten-PC (`config-general.toml`, exklusiver Key). Kein internes VLAN.

## `derive-agents.py`

Quelle der Wahrheit ist `.opencode/hestia-primary-agents.json` (SOT, vom
Hestia-Panel gepflegt, ein Commit):

```json
{ "build": { "model": "…", "variant": "" },
  "plan":  { "model": "…", "variant": "max" } }
```

Das Skript merged die **effektiven** Modelle nach `agents` in
`/root/.config/opencode/opencode.json` — nur das Feld `model`, die System-Prompts
der Built-ins bleiben unangetastet. Fehlt die Datei, wird aus `@medium`
abgeleitet (Standardvariante für `build`, höchste Variante für `plan`), wobei die
Varianten-Liste vom laufenden OpenCode-Server (`/api/model`) kommt.

## `ha-forward.py`

Ein asyncio-TCP-Relay: `[LISTEN_PORT] [TARGET_HOST] [TARGET_PORT]`, Standard
`18123` → HA Kleve. Zweck: der Infrastructure-Container darf HA Kleve (Tailscale)
nur über das Tailnet erreichen; Container ohne Tailscale — vor allem `hermes` —
brauchen diesen Relay-Weg.

## Versions-Pins (`versions.env`)

```ini
SSH_MCP_VERSION=2.18.0
OCIS_MCP_VERSION=1.1.0
HERDR_VERSION=0.9.3
```

Die **OpenCode-CLI ist bewusst nicht gepinnt** (Hybrid, siehe
[`architecture.md`](architecture.md)). Update-Ablauf: eine Version in
`versions.env` anpassen, committen, pushen — beim nächsten Workspace-Start ziehen
alle Container automatisch und identisch nach. Veraltete Pins zeigt
`scripts/check-updates.sh` (read-only).

## Template veröffentlichen

```bash
coder login --url https://coder.mueller-nas.de --token <token>
coder templates push <name> --directory coder-templates/<name> --yes --org=coder
```

`<name>` = `infrastructure` | `general` | `muellerconnect` | `datascience`.
Das Image wird lokal aus dem jeweiligen `Dockerfile` gebaut; der `CACHE_DATE`
Build-Arg erzwingt den Rebuild beim Push.