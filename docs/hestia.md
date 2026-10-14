# Hestia — OpenCode Model Panel

> Vertiefung, Hintergründe und die Projektakte liegen im Obsidian-Vault unter dem
> Projekt **PRO-302** („AUTO AI"). Dieses Dokument beschreibt nur den Ist-Zustand.

## Zweck und Historie

Hestia ist das Kontrollpanel für die Modell-Auswahl der OpenCode-Fleet. Es ging aus
**PRO-302 („AUTO AI")** hervor, in dem die Agenten-Tiers (`@fast` / `@medium` /
`@heavy`) überhaupt erst automatisch auf Modelle abbildbar wurden. Die ursprüngliche
Frage lautete: welches Modell soll welcher Agent sein? Daraus entstanden die
Modell-Katalog-Anzeige, der Apply-Durchstich in CI und später das Budget-/Konto-
System über die OpenCode-Go-Abos.

Heute macht Hestia vier Dinge:

1. **Tier-Zuordnung** verwalten (Modell, Variant/Effort, Kostenratio, Stufenlimits je Tier)
2. **Budget überwachen** — Restbudget je OpenCode-Go-Abo, mit automatischen Kontowechsel
3. **Ausfallsicherheit** — bezahltes Gegenmodell hinter jedem Gratis-Tier, Auto-Failover
4. **Hermes steuern** — Modell, Fallback-Kette und Abo-Pool für den Hermes-Agenten

## Erreichbarkeit

| Weg | Adresse | Auth |
|-----|---------|------|
| Primär | `https://hestia.mueller-nas.de` | Authentik-SSO (Traefik) |
| Fallback | `10.0.10.17:49400` | HTTP-Basic |

Der primäre Weg läuft über Traefik mit Authentik-SSO. Der Fallback ist der direkt
veröffentlichte Port aus der Compose-Datei (`49400:8000`), geschützt durch
HTTP-Basic-Authentifizierung im Backend.

Zusätzlich akzeptiert das Backend einen Proxy-Secret-Header `X-Hestia-Proxy-Secret`
(`HESTIA_PROXY_SECRET`). Damit kann ein vorgeschalteter Reverse Proxy
Panel-Anfragen durchreichen, ohne das Basic-Passwort zu kennen — nützlich, wenn der
Zugriff nicht über die normale SSO-Kette läuft.

Die Compose-Datei ist bewusst **ohne Volumes** aufgebaut: Der Container ist
zustandslos. Alles, was er speichern muss (Panel-Config, Kontostand, Fallbacks),
liegt in OpenBao, GitHub oder den Coder-Workpaces. Neustart, Update oder
Wiederherstellung bedeuten deshalb nur „Container neu starten".

## Technik

| Aspekt | Umsetzung |
|--------|-----------|
| Backend | Python-3.12-**Stdlib**-HTTP-Server (`http.server`), keine externen Frameworks |
| Quelltext | `hestia/backend/` — `server.py`, `accounts.py`, `catalog.py`, `failover.py`, `github_api.py`, `hermes.py`, `suggest.py`, `bootstrap.py`, `hjsonc.py` |
| Secret-Bootstrap | `bootstrap.py` — startet als `command`, holt alle Secrets aus OpenBao |
| Frontend | Svelte; `frontend/dist/` ist **committet** |
| Image | `python:3.12-slim`, baut Backend + fertiges Frontend ein |
| Netz | externes Docker-Netz `coder-infra`; Container `hestia` |

Zwei Konsequenzen aus dem Setup:

- **Im Container wird nichts gebaut.** Das Frontend-Dist liegt im Repo, das Image
  kopiert es nur. Ein CI-Build braucht keine Node-Toolchain.
- **Keine Abhängigkeiten, kein Lockfile.** Das Backend kommt mit der Python-Standard-
  Bibliothek aus. Ein Sicherheitsupdate des Panels ist ein `git pull` und ein
  Container-Neustart.

`bootstrap.py` liest, wenn nicht überschrieben:

| OpenBao-Pfad | Feld | Env im Container |
|-------------|------|------------------|
| `secret/data/mcp/github` | `api_key` | `GITHUB_TOKEN` |
| `secret/data/mcp/hermes-opencode-service` | `password` | `OC_SERVER_PASSWORD` |
| `secret/data/mcp/hestia` | `password` | `HESTIA_PASSWORD` (optional) |
| `secret/data/mcp/opencode-go` | `default1`, `default2`, `default3` | `OC_GO_DEFAULT1..3` |

Der Rest der Env (`OPENBAO_ADDR`, `GITHUB_REPO`, `OC_SERVER_URL`, Timeout- und
Schwellenwerte) steht als Klartext in `hestia/docker-compose.yml`.

## Single Sources of Truth

Acht Dateien unter `.opencode/` im Repo-Root, alle von Hestia verwaltet:

| Datei | Inhalt | Wer liest sie |
|-------|--------|---------------|
| `opencode-model-router.overrides.jsonc` | **Tiers**: `fast`/`medium`/`heavy` je Modell, `variant`, `costRatio`, `steps`, `whenToUse`; dazu `activePreset` und weitere Presets | Plugin `opencode-model-router` |
| `hestia-accounts.jsonc` | **Konten und Policy**: `active`, `accounts[]` mit `keyRef`, `policy` (Modus, Balance, expiring), `display`, `providers` | Hestia (`accounts.py`), CI-Workflows, `startup.sh` |
| `hestia-fallbacks.json` | Ausfallmodell je Tier (Model + Variant) | Hestia (`failover.py`), CI |
| `hestia-hermes.jsonc` | Hermes-Modell, Fallback-Kette, Pool-Regeln, Profil-Overrides | Hestia (`hermes.py`), Workflow `apply-hermes` |
| `hestia-primary-agents.json` | Agent→Modell-Zuordnung für `build` und `plan` | Hestia (`/api/config`) |
| `hestia-failover.jsonc` | **Ausfallschutz-Einstellungen**: `enabled`, `threshold`, `intervalSeconds`, `timeoutSeconds` | Hestia (`failover.py`) |
| `hestia-suggest.jsonc` | **KI-Vorschlag-Automatik**: `enabled`, `checkIntervalSeconds`, `cooldownSeconds`, `analysisModel` | Hestia (`suggest.py`) |
| `hestia-benchmarks.jsonc` | **Modell-Benchmarks**: `enabled`, `refreshDays`, `fields`, `aliases` (Quelle OpenRouter) | Hestia (`benchmarks.py`) |

Das Overrides-File ist JSONC (Kommentare erlaubt) und wird vom Router-Plugin per
**cwd-Suche nach oben** bis zur Repo-Root gefunden. Deshalb braucht kein Workspace
einen Symlink und keinen Cache-Eingriff — jedes Container-Arbeitsverzeichnis im Repo
zieht automatisch dieselbe Datei.

### Der aktive Account: genau eine Quelle

**Der aktive OpenCode-Go-Account steht in `.opencode/hestia-accounts.jsonc`**, im Feld
`active`. Das ist die einzige Quelle.

OpenBao `secret/data/mcp/opencode-go` enthält **ausschließlich die Keys**
(`default1`, `default2`, `default3`). Ein dort historisch ebenfalls vorhandenes Feld
`active` wird **nicht mehr gelesen und nicht mehr geschrieben** — es bleibt nur als
Altlast in OpenBao bestehen und ist ohne Wirkung.

Warum die Verlagerung? Ein Attribut, das zwei Systeme pflegen, driftet. Der alte
Ablauf hatte Hestia und OpenBao beide als Schreiber; wenn eins der beiden ausfiel,
konnten beide „der aktive Account sein". Jetzt schreibt nur noch Hestia, und der
CI-Workflow verteilt daraus den Key.

`hestia-accounts.jsonc` im Repo enthält außerdem die Konto-IDs `default1`/`default2`/
`default3` mit `keyRef` auf das jeweilige OpenBao-Feld — aber keine Schlüsselwerte.

## REST-Endpunkte

Aus `hestia/backend/server.py`. Alle Endpunkte außer `/api/health` verlangen
Autorisierung; ein GET auf einen POST-only-Endpunkt liefert 405 mit Klartext-Hinweis.

### GET

| Pfad | Antwort |
|------|---------|
| `/api/health` | `status` (`ok`/`degraded`), Erreichbarkeit von OpenCode-Server und GitHub, Repo, Overrides-Pfad. **Bewusst unauthentifiziert** — monitoring-freundlich, keine sensiblen Inhalte |
| `/api/catalog` | Live-Modellkatalog + erlaubte Provider + aktiver Preset-Provider; zusätzlich `benchmarks` je Modell und `benchmarksStatus` |
| `/api/benchmarks/status` | Benchmark-Cache: Quelle, Datenstand/Alter, Anzahl, fehlende Modelle |
| `/api/config` | aktiver Preset, Tiers, effektive Primary-Agents, Fallbacks, verfügbare Modes, Git-SHA und HTML-URL der Overrides |
| `/api/rollout/status` | letzter Lauf des Rollout-Workflows |
| `/api/failover/status` | Auto-Failover-Zustand |
| `/api/suggest/status` | KI-Vorschlag-Zustand (läuft gerade? welches Modell, welcher Versuch, Laufzeit) + `auto` (Automatik-Einstellungen, Fingerprint, nächste Prüfung) + `proposal` (letzter automatischer Vorschlag) |
| `/api/accounts`, `/api/accounts/status` | Kontostand, Restbudget je Fenster, `isActive`, `expiring`, Modus |
| `/api/hermes`, `/api/hermes/status` | aktuell an Hermes verteiltes Modell, Fallback-Kette, Pool |

### POST

| Pfad | Wirkung |
|------|---------|
| `/api/apply` | Tier-Konfiguration validieren + committen + Rollout auslösen |
| `/api/suggest` | KI-Modellvorschlag starten (asynchron) |
| `/api/suggest/settings` | KI-Vorschlag-Automatik speichern (SOT-Commit, wirkt sofort) |
| `/api/failover/check` | Failover-Probe sofort ausführen |
| `/api/failover/settings` | Ausfallschutz-Einstellungen speichern (SOT-Commit, wirkt sofort) |
| `/api/benchmarks/refresh` | Benchmarks sofort bei OpenRouter neu holen (read-only, gecacht) |
| `/api/accounts/check` | Budget aller Konten sofort neu abfragen |
| `/api/accounts/switch` | manueller Kontowechsel |
| `/api/hermes/apply` | Hermes-Konfiguration verteilen |
| `/api/rollout` | Rollout erneut auslösen |

## Ablauf „Anwenden"

Der Weg von einer Panel-Änderung bis zu einem laufenden Modell:

```
Panel-Änderung
   → POST /api/apply
   → Validierung gegen den Live-Katalog (Modell existiert noch? Provider erlaubt?)
   → Commit via GitHub Contents API auf master
   → CI: validate-hestia-config.yml   (Schema-/Werteprüfung der SOT-Dateien)
   → CI: rollout.yml                   (SSH vom Self-hosted Runner auf den Coder-Host)
   → Container infrastructure / general / muellerconnect neu starten
   → entrypoint zieht frisches Repo
   → Overrides aktiv
```

Der Container-Neustart ist deshalb nötig: `startup.sh` setzt das Service-Env bei
jedem Start neu, und das Plugin liest die Overrides beim Prozessstart.

## Budget, Konten und Policy

### Quoten

Je Konto liefert die OpenCode-Go-Usage-API (`https://opencode.ai/zen/go/v1/usage`)
den Verbrauch für drei Fenster: **rolling**, **weekly**, **monthly**.

`checkIntervalSeconds` (Standard 300 s) legt das Abfrageintervall fest. Die
Anzeige-Schwellen stehen unter `display`:

- `barMetric` — `used` (Balken zeigt Verbrauch) oder `free` (Restbudget)
- `warnBelowPercentFree` — gelbe Warnstufe
- `criticalBelowPercentFree` — rote/kritische Stufe (`0` = erschöpft)

### Ablauf-Bevorzugung („use it or lose it")

Ein Wochen- oder Monatsfenster, das sich bald zurücksetzt, ist wertlos, wenn es
unerreicht bleibt. Hestia bevorzugt deshalb ein solches Konto, solange es noch
Restbudget hat. Konfiguriert in `policy.expiring`:

| Feld | Wirkung |
|------|---------|
| `enabled` | Bevorzugung an/aus |
| `withinHours` | „bald" = Reset in ≤ X Stunden |
| `windows` | welche Fenster zählen — bewusst **nicht** `rolling`/5 h |
| `minRemainingPercent` | nur bevorzugen, wenn noch ≥ X % frei sind |

Die Bevorzugung gilt, **bevor** die Ausgleichs- und Erschöpfungsregeln greifen. Eine
abgelaufene Bevorzugung im aktiven Konto wird also nicht zurückgenommen.

### Auto-Kontowechsel

`policy.mode` steuert das Verhalten:

- `auto` — Wechsel ohne Rückfrage
- `notify` — Vorschlag mit Bestätigung
- `off` — nur manuell über das Panel

Auslöser in dieser Reihenfolge:

1. **Bevorzugung** (`policy.expiring`, siehe oben)
2. **Erschöpfung** — `switchWhenExhausted` bei 0 % frei bzw. Status `rate-limited`
   in irgendeinem Fenster → Zielkonto mit dem meisten Restbudget
3. **Ausgleich** (`policy.balance`) — aktiv, wenn der Verbrauch des aktiven Kontos in
   Woche/Monat um `maxSpreadPercentPoints` über dem Durchschnitt der anderen liegt
   und das Zielkonto mindestens `minHeadroomPercent` frei hat

`cooldownSeconds` (Standard 1800) setzt den Mindestabstand zwischen zwei Wechseln —
das Panel selbst kann den Wert als Env `HESTIA_SWITCH_COOLDOWN` überschreiben.

Mechanik eines Wechsels:

```
accounts.py → switch_account()
  → schreibt "active" in .opencode/hestia-accounts.jsonc  (SOT, atomar via GitHub Contents API)
  → dispatcht .github/workflows/switch-account.yml
  → hestia/ci/switch-account.sh
      liest den Key des neuen aktiven Kontos aus OpenBao
      opencode service set env OPENCODE_API_KEY   je betroffenem Container
      KEIN Container-Neustart
```

## Selbstheilung

### Credential-Kette — `verify-oc-credentials.yml`

Der Workflow `hestia/ci/check-oc-credentials.sh --fix` prüft und heilt die
Kette, die nach einem Kontowechsel am ehesten bricht. Er ist auf **alle 30 Minuten**
getaktet — **in der Praxis aber deutlich seltener**: GitHubs Zeitplan ist „best
effort" (gemessen am 2026-10-14: nur **6 Läufe in 24 h**, Lücken bis ~5,5 h). Die
**verlässliche** Absicherung ist der Key, den `coder-templates/shared/startup.sh`
bei **jedem** Container-Start setzt; der Workflow ist das zusätzliche Sicherheitsnetz
für den Fall, dass sich *während* des Betriebs ein gespeicherter Account
dazwischenschiebt. Anlass war ein Fehler vom
2026-10-13, bei dem OpenCode v2 Credentials in `opencode.db` speichern konnte
(`opencode auth login`) — ein gespeicherter Account **überschriebt dann die
ENV-Variable** `OPENCODE_API_KEY`, und der Wechsel wird wirkungslos, ohne dass irgendwo
ein Fehler sichtbar wird. Zusätzlich ist `/root/.config/opencode/service.json`
**ephemer**: nach einem Container-Neustart fehlt der Key im Service-Env.

Geprüft wird (Exit 1 bei Verstoß), je Container:

1. Für die Integration „OpenCode Go" existiert **kein** gespeichertes (`stored`)
   Credential, nur `environment` — sonst greift das Shadowing.
2. `OPENCODE_API_KEY` in der `.bashrc` entspricht dem Key des in der SOT als `active`
   markierten Kontos — Vergleich über **sha256**, der Key wird nie ausgegeben.
3. Der Key steht im Service-Env (`service.json`).
4. Ein Mini-Modellaufruf mit dem `active`-Key antwortet mit HTTP 200 (Konto nutzbar,
   nicht gesperrt und nicht ohne Guthaben).

`--fix` zieht einen fehlenden oder abweichenden Key im Service-Env und in
`/root/.bashrc` (dedupliziert) nach.

### Service-Env bei jedem Container-Start

Weil `service.json` ephemer ist, setzt `coder-templates/shared/startup.sh` den Key des
aktuellen Kontos bei **jedem** Container-Start neu
(`opencode service set env OPENCODE_API_KEY`). Das ist die Gegenmaßnahme, die einen
Neustart von einem Selbstheilungs-Fall befreit.

### `oc-go` — lokaler Kontowechsel

Im Container gibt es ein Shell-Kommando zum manuellen Umschalten, ohne Panel:

```bash
oc-go list            # zeigt den lokal gesetzten Key/Account (verwaltet von Hestia)
oc-go use default1    # Key lokal wechseln
oc-go use default2
oc-go use default3
```

Der Wechsel schreibt `.bashrc` und das Service-Env — **kein** OpenBao-Schreibzugriff,
kein Container-Neustart.

Ein `oc-go default` gibt es **nicht mehr**; der Aufruf wird mit einer Fehlermeldung
abgewiesen. Welcher Account aktiv ist, steht im SOT-Feld `active` — nicht in einer
Konfiguration, die ein einzelnes `oc-go` nachträglich divergieren könnte.

## Failover

Wird ein Tier auf ein **Gratis-Modell** gesetzt (Kosten 0/0), hinterlegt Hestia
automatisch das **bezahlte Gegenstück** als Ausfallmodell in
`.opencode/hestia-fallbacks.json` (Model + Variant je Tier). Dieses Ausfallmodell
lässt sich im **Tier-Editor** je Tier auch **manuell** wählen — oder bewusst auf
„kein Ausfall-Modell" setzen; die Auswahl wird mit dem Tier-Update committet.

| Tier | Modell | Ausfallmodell |
|------|--------|---------------|
| `fast` | `opencode/mimo-v2.6-flash-free` | `opencode-go/mimo-v2.6-flash` |
| `medium` | `opencode/space-bunny-free#high` | `opencode-go/claude-haiku-5-5#medium` |
| `heavy` | `opencode-go/gpt-6-luna#max` | — (schon bezahlt) |

Bei wiederholtem Fehlschlag schaltet der Auto-Failover auf das bezahlte Modell:

Die Schalter und Schwellen stehen in `.opencode/hestia-failover.jsonc` und sind im
Tier-Editor einstellbar (`POST /api/failover/settings`); die Env-Variablen dienen
nur noch als Startwerte, wenn die SOT fehlt:

| Einstellung | Feld in der SOT | Startwert (Env) | Wirkung |
|-------------|-----------------|-----------------|---------|
| Auto-Failover | `enabled` | `HESTIA_AUTO_FAILOVER` (`0`) | `false` = nur manuell (Panel-Button), `true` = automatisch beim Probe-Lauf |
| Prüfintervall | `intervalSeconds` | `HESTIA_PROBE_INTERVAL` (`900`) | Intervall der Erreichbarkeitsproben (60..86400 s) |
| Fehlerschwelle | `threshold` | `HESTIA_FAIL_THRESHOLD` (`2`) | Fehlversuche bis zum Umschalten (1..10) |
| Probe-Timeout | `timeoutSeconds` | `HESTIA_PROBE_TIMEOUT` (`35`) | Zeitfenster je Probe (5..300 s) |

Änderungen wirken **sofort** (der Hintergrund-Thread liest Intervall und Schwelle
je Iteration neu) und brauchen keinen Container-Neustart.

Status über `/api/failover/status`, Sofort-Prüfung per `POST /api/failover/check`.

## KI-Vorschlag

`POST /api/suggest` dispatcht eine OpenCode-Session und lässt sie die Modellwahl
analysieren. Kein Request-Block: Der Panel-Request kehrt sofort zurück, der Zustand
wird über `/api/suggest/status` verfolgt (Modell, Versuch, Laufzeit).

### Automatik (ereignisgesteuert, 2026-10-13)

Der Vorschlag kann **automatisch** laufen — aber **nicht** nach Kalender, sondern nur
bei einer **materiellen Katalogänderung**. Konfiguration in `.opencode/hestia-suggest.jsonc`
(im Panel einstellbar, `POST /api/suggest/settings`):

- **Prüfen (billig, lokal):** alle `checkIntervalSeconds` (Default 6 h) **ein** Abruf des
  eigenen OpenCode-Server-Katalogs → Fingerprint über die **materiellen** Felder der
  relevanten Provider: Modell-ID, Status, Varianten, Preise. Reine Namens-/Beschreibungs-
  änderungen ändern ihn **nicht**.
- **Analysieren (teuer, selten):** ein KI-Lauf startet nur, wenn sich der Fingerprint
  geändert hat **und** der `cooldownSeconds` (Default 24 h) seit dem letzten Auto-Lauf
  abgelaufen ist. Änderungen innerhalb des Cooldowns bleiben „anstehend" und laufen danach
  nach — Obergrenze also **max. 1 Lauf je Cooldown**.
- **Baseline-Regel:** ohne gespeicherten Fingerprint wird nur der Ist-Stand gemerkt —
  **kein** KI-Lauf (sonst würde jeder Deploy/Neustart einen Lauf auslösen).
- **Modell:** der Auto-Lauf nutzt `analysisModel` (Default ein **Gratis-Modell**), damit er
  nie am erschöpften Abo-Budget scheitert; der manuelle Lauf bleibt `@heavy`.
- **Zustand lokal:** Fingerprint + letzter Vorschlag liegen unter `/etc/hestia`
  (Bind-Mount → `/app/data`, **nicht** im Repo committet) und überleben Deploys.
- **Sichtbarkeit:** Das Panel zeigt den letzten automatischen Vorschlag, markiert
  Abweichungen zur aktiven Konfiguration und kann ihn in den Tier-Editor übernehmen.

- **Default:** `@heavy` aus den committeten Overrides, inklusive Effort-Variante.
  Im Panel frei wählbar — Editor-Auswahl oder jedes Katalog-Modell.
- **Fallback-Kette:** `heavy → medium → Session-Default`. Fällt ein Modell aus, wird
  das nächste probiert, statt den Vorschlag abzubrechen.
- **Timeouts** (bewusst unterschiedlich): `OC_SUGGEST_STALL_TIMEOUT` = 45 s Stille,
  `OC_SUGGEST_TIMEOUT` = 300 s gesamt. Der Unterschied ist wichtig: die vollen
  Katalog-Modelle mit Effort-Variante brauchen deutlich länger als 150 s, ein zu
  kurzes Gesamtlimit hat den Vorschlag regelmäßig in den `@medium`-Fallback getrieben.
  Ein Hänger wird über *Stille* erkannt, nicht über das Gesamtlimit.
- **Aufräumen:** die Analyse-Session wird danach automatisch gelöscht;
  `OC_SUGGEST_KEEP_SESSIONS=1` behält sie zum Debuggen.

## Benchmarks (Artificial Analysis + OpenRouter, 2026-10-14)

Preisübersicht und KI-Vorschlag nutzen **Modell-Benchmarks** aus zwei Quellen, pro
Feld zusammengeführt — **der erste Treffer gewinnt**:

1. **Artificial Analysis** (primär): `GET https://artificialanalysis.ai/api/v2/data/llms/models`
   (Header `x-api-key`; gratis, 1.000 Requests/Tag, **Attribution Pflicht**). Der Key liegt
   in OpenBao unter `secret/data/mcp/artificialanalysis` (`api_key`) → Env `AA_API_KEY`;
   fehlt er, fällt die Quelle automatisch weg (dann alles über OpenRouter).
2. **OpenRouter** (Fallback/Ergänzung): `GET https://openrouter.ai/api/v1/models` —
   öffentlich, **kein API-Key**.

- **Felder** (konfigurierbar in `.opencode/hestia-benchmarks.jsonc`; Labels aus
  `benchmarks.FIELD_LABELS`): `intelligence_index`, `coding_index` (AA/OR),
  `terminalbench` (AA, Terminal-Bench v4.0 ×100), `speed` (AA, Tok/s),
  `agentic_index` (nur OR), `code_elo`/`code_rank` (nur OR). AA liefert zusätzlich
  SciCode und τ²-bench; `livecodebench` ist für unsere Modelle leer und bewusst ungenutzt.
- **Zuordnung** Katalog-Ref → Quelle: expliziter Alias → `modelID` ohne `-free` →
  Modellname → **`canonical_slug` datumsbereinigt** (nur bei Eindeutigkeit, sonst „—");
  bei AA zusätzlich über `slug`/`model_creator.slug`. Verifizierter Stand 2026-10-14:
  **61 von 78** mit Zahlen (AA 700 Modelle, OR 462).
- **Verifizierte Lücken (0 Zuordnungsfehler):** Die Modelle ohne Zahlen sind geprüft —
  überwiegend **Zen-exklusiv ohne Zwilling** bei beiden Quellen (`space-bunny`,
  `cat-jam`, `big-pickle`, `exo`, `ling-3.0-flash-fin`, `longcat-2.5-preview`,
  `gpt-5.3-codex-spark` …); der Rest hat bei den Quellen (noch) keine Werte. Neue
  Modelle werden beim nächsten Abgleich mitgezogen.
- **Regressionstest:** `hestia/ci/test_benchmarks.py` (36 Tests, **kein Netz**) prüft
  Zuordnung inkl. `canonical_slug`/Eindeutigkeit, Werte-Extraktion, Cache,
  Fault-Tolerance, Drossel und TTL — läuft in `validate-hestia-config.yml` mit.
- **Lokaler Cache:** `/etc/hestia/model-benchmarks.json` (Bind-Mount, **nicht** im
  Repo). Neu geholt wird nur bei Bedarf — Cache fehlt, älter als `refreshDays`
  (Default 30), ein Modell fehlt — oder manuell über `POST /api/benchmarks/refresh`;
  dazu eine 6-h-Drossel. Bei Netzfehler bleibt der alte Cache stehen (`lastError`).
- **Für die AI (entscheidend):** Die Zahlen werden dem KI-Vorschlag **mitgeliefert**
  (Spalte `Bench (…)` mit den in der SOT gewählten Feldern und ihren Labels) samt
  verbindlicher Regel, **keine eigenen Benchmarks abzurufen oder zu schätzen**;
  `—` heißt „unbekannt".
- **Anzeige (UI-Redesign 2026-10-14):** Die Preisübersicht zeigt in der **Hauptzeile**
  nur `Modell · Kontext · in/out` plus **2–3 per Chip wählbare Kennzahlen** als
  Mini-Balken + Zahl (Auswahl in `localStorage`, max. 3; Balken relativ zum Maximum
  über die sichtbaren Zeilen). Ein **Klick auf die Zeile** klappt eine **Detailzeile**
  auf: alle vorhandenen Benchmarks (Label · Balken · Wert), Kontext/Max-Output,
  Cache-Preise, Varianten, Status sowie die Quell-IDs (`aa_id`/`or_id`) mit
  Attribution. Sortierung, Kennzahlen-Toggle, Provider-Filter, „Top-40/alle" und
  Refresh bleiben; `table-layout: fixed` + Ellipsis verhindern horizontales Scrollen.

## Hermes-Steuerung

SOT ist `.opencode/hestia-hermes.jsonc`:

- `primary` — Provider und Modell
- `fallback[]` — geordnete Ausfallkette
- `pool` — `followActive` zieht den Abo-Pool nach jedem Kontowechsel automatisch nach;
  `onlyActiveWhenExpiring` schränkt den Pool auf das aktive Konto ein, solange keine
  Bevorzugung greift
- `restart` — Container nach dem Anwenden neu starten
- `profiles` — **pro Profil** abweichende Regeln, aktuell `kleve` (mit eigenem Primary
  und kürzerer Fallback-Kette, abweichend vom Standard)

Verteilung:

```
Panel-Karte „Hermes — Modell/Provider"
  → POST /api/hermes/apply
  → schreibt .opencode/hestia-hermes.jsonc (SOT)
  → .github/workflows/apply-hermes.yml
  → hestia/ci/apply-hermes.sh
      config set
      fallback_providers
      Pool aus OpenBao
      Restart
```

Nach einem Kontowechsel wird der Pool automatisch nachgezogen, solange
`pool.followActive` gesetzt ist.

## `model-curator`-Skill

Der Skill `model-curator` (`hestia/skills/model-curator/`) fragt Hestia im
**Lesemodus** ab — er liest Konfiguration, schreibt nichts. Er läuft im
**Standardprofil**; das **kleve-Profil ist bewusst ausgeschlossen**, weil der
Curator die Standard-Konfiguration beschreibt und eine site-spezifische Ausnahme
nicht sinnvoll allgemeingültig beantworten kann.

## One-Time-Setup

Einmalig auszuführen, danach läuft alles über GitHub Actions:

**`hestia/ci/setup/README.md`**

1. Self-hosted GitHub-Runner auf dem Docker-Host (CT 100) — `setup-runner.sh`,
   Label `docker-host`, systemd-Service `github-runner-hestia`
2. SSH-Deploy-Key für den Coder-Host (CT 120) — `setup-coderhost-deploy-key.sh`
3. GitHub-Secrets und OpenBao-AppRole
4. Runner-Watchdog — `runner-watchdog.sh`

## Status Phase 5 / O6

**Umgesetzt:**

- Budget-System über die OpenCode-Go-Usage-API, mit Anzeige, Schwellen und
  Selbstheilung der Credential-Kette
- Hestia → Hermes-Steuerung (Modell, Fallback-Kette, Pool-Nachzug)
- Runner-Watchdog für den self-hosted Runner

**Bewusst offen:**

- **Gemini- und Codex-Adapter.** In `hestia-accounts.jsonc` stehen beide Provider als
  `"type": "placeholder"` — das Gerüst ist da, die Nutzungs-API noch nicht angebunden.
- **Echter Cross-Provider-Fallback.** Die Fallback-Kette funktioniert derzeit nur
  innerhalb eines Providers. Ein Wechsel *über* Provider-Grenzen (z. B. von
  `opencode-go` auf `opencode`) braucht zwei belastbare Provider-Keys und ist bis
  dahin nicht belastbar zu bauen.

## Weiterführend

- [workspaces.md](workspaces.md) — Workspaces und ihre Trennung
- [mcp-skills.md](mcp-skills.md) — MCP-Server, `ssh-mcp`-Profile, Skills
- [architecture.md](architecture.md) — Gesamtarchitektur
- [secrets.md](secrets.md) — OpenBao-Struktur