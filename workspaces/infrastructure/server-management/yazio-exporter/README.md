# YAZIO → Postgres Exporter

Holt die eigenen YAZIO-Daten über die offizielle App-API und schreibt sie in
eine Postgres-Datenbank. Läuft als Container auf dem Docker-Host, ohne
Eingriff auf dem Handy.

## Wie es funktioniert

Die App authentifiziert sich per OAuth2-Password-Grant:

```
POST https://yzapi.yazio.com/v22/oauth/token
     grant_type=password
     username, password, client_id, client_secret
→ access_token (48 h), refresh_token
```

`client_id` und `client_secret` sind fest in der App hinterlegt
(`yazio.network.api.Environment`) und gelten für alle Nutzer. Der Exporter
braucht sie nur als Konfiguration, nicht als Geheimnis.

Nach dem ersten Login läuft alles über den `refresh_token`, der in der
Datenbank liegt. Das Passwort wird nur für den allerersten Login gebraucht und
kann danach aus `yazio.env` entfernt werden.

## Endpunkte, die genutzt werden

| Zweck | Endpunkt | Parameter |
|---|---|---|
| Profil, Registrierungsdatum | `GET /v22/user` | — |
| Tagebuch eines Tages | `GET /v22/user/consumed-items` | `date` |
| Nährwerte (Monat) | `GET /v22/user/consumed-items/nutrients-daily` | `start`, `end` |
| Ziele eines Tages | `GET /v22/user/goals` | `date` |
| Wasser | `GET /v22/user/water-intake` | `date` |
| Körperwerte | `GET /v22/user/bodyvalues` | `date` |
| Aktivität (Monat) | `GET /v22/user/exercises/summary-daily` | `start`, `end` |
| Aktivität (Tag) | `GET /v22/user/exercises` | `date` |
| Stimmung | `GET /v22/user/feeling` | `date` |
| Fastenplan + Historie | `GET /v22/user/fasting-countdowns[/history]` | — |
| Produktdetails | `GET /v22/products/<id>` | — |

`consumed-items` liefert drei getrennte Listen — `products`,
`recipe_portions` und `simple_products`. Alle drei landen in `diary_entry`
mit unterschiedlichem `kind`.

## Aufruf

```sh
exporter.py --backfill              # komplette Historie ab Registrierung
exporter.py --days 30               # letzte 30 Tage
exporter.py --date 2026-11-04       # ein Tag
exporter.py --loop --interval 21600 # Dauerbetrieb, alle sechs Stunden
```

Im Container läuft `--loop`; jeder Lauf holt die letzten drei Tage erneut
(idempotent über Upserts), damit nachträgliche Änderungen ankommen.

## Konfiguration

`/opt/docker-compose/yazio-capture/yazio.env` (Modus 600, nicht im Git):

```
YAZIO_USERNAME=…
YAZIO_PASSWORD=…            # nur für den ersten Login nötig
YAZIO_CLIENT_ID=…
YAZIO_CLIENT_SECRET=…
```

Dazu im Compose-File:

```
DATABASE_URL=postgresql://yazio:<POSTGRES_PASSWORD>@postgres:5432/yazio
```

> **Fallstrick:** Compose interpoliert `$` auch in `env_file`-Dateien. Enthält
> das Passwort ein `$`, muss es als `$$` geschrieben werden — sonst kommt ein
> verstümmeltes Passwort im Container an (Symptom: `HTTP 400` mit leerem Body).

## Tabellen

| Tabelle | Inhalt |
|---|---|
| `oauth_token` | Access-/Refresh-Token, Ablaufzeitpunkt |
| `profile` | Profil-JSON inkl. `registration_date` |
| `diary_entry` | Tagebucheinträge: `kind` = product/recipe/simple, `day`, `daytime`, `amount`, `raw` |
| `product` | Produktdetails (Name, Marke) mit Cache |
| `nutrients_day` | Kalorien/Makros pro Tag inkl. Tagesziel |
| `activity_day`, `activity_detail` | Schritte, Dauer, Aktivitätskalorien |
| `water_day` | Wasseraufnahme pro Tag |
| `goal` | Tagesziele als Key-Value |
| `feeling_day` | Notiz und Tags |
| `body_value` | Gewicht und andere Körperwerte (API liefert Listen) |
| `fasting_plan` | Fastenplan-Vorlagen (keine gebuchten Zeiten) |
| `sync_log` | Lauf-Protokoll mit Anzahl der API-Aufrufe |

## Deployment

```sh
cd /opt/docker-compose/yazio-capture
docker compose up -d --build exporter
docker compose run --rm --no-deps exporter --backfill
```

Die Quelldateien liegen im Infra-Repo unter
`workspaces/infrastructure/server-management/yazio-exporter/`.
