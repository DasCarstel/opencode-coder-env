# openGym-Session-Start → Zimmerreinigung + Sportmodus

[[2026-11-23]]

Wenn in openGym eine Trainings-Session startet, reinigt der Roborock Carstens
Zimmer und das Handy (S25 Ultra) geht in den Sportmodus (Nicht stören, Musik).

## Warum nicht über die openGym-Webhooks?

openGym 1.4.5 kennt nur drei Webhook-Events (`/app/integrations/events.js`):

```js
export const EVENTS = ['workout.finished', 'workout.pr', 'weighin.logged'];
```

Alle entstehen aus dem Diff zweier `PUT /api/data`-Dokumente, also **erst am
Trainingsende**. Ein `session.started` gibt es nicht, und die Integrations sind
auf der Instanz ohnehin deaktiviert (`INTEGRATIONS` fehlt in der `.env`).

## Lösung: Activity-Shim

Die openGym-Web-App schickt alle 20 s `POST /api/activity` mit `{active:true,…}`
— **nur solange ein Workout auf dem Bildschirm offen ist**. Reines Stöbern in
der App erzeugt keinen Heartbeat. Genau dieses Signal wird abgegriffen.

```
Handy ──POST /api/activity──▶ Traefik ──Path(/api/activity)──▶ activity-shim
                                                                   │
                                                          proxied an api:3000
                                                                   │
                                          active:true (erster Beat)│
                                                                   ▼
                                    POST http://homeassistant:8123/api/webhook/gym_session_start
```

### Dateien

| Datei | Zweck |
|---|---|
| `/opt/docker-compose/opengym/shim/server.js` | Shim (Node, keine Dependencies) |
| `/opt/docker-compose/opengym/docker-compose.yml` | Service `activity-shim` + Traefik-Labels |
| `/etc/docker/homeassistant/packages/gym.yaml` | HA-Automation + Skript |

### Entprellung (im Shim)

- `active:true` → löst höchstens einmal pro `DEBOUNCE_MS` (45 min) aus
- `active:false` mit `reason: closed|navigated` → Sperre aufheben
- `active:false` mit `reason: hidden` (App im Hintergrund) → ignoriert
- Health: `docker exec opengym-activity-shim wget -qO- http://localhost:8080/healthz`

### Workout-Filter (welches Training löst aus?)

Der Heartbeat enthält den Namen des laufenden Workouts (`name`, aus
`state.S.active.name` der Web-App). Der Shim prüft ihn **vor** der
Entprellung gegen `SKIP_WORKOUT_REGEX` (Default
`dehn|stretch|mobility|yoga|foam`, case-insensitive):

- Treffer → Log `SESSION-START ignoriert (Workout gefiltert)`, **kein**
  Webhook und **kein** Lock. Ein Dehn-Tag verbraucht also keinen der
  45 Minuten, ein echtes Training danach startet normal.
- Kein Treffer → wie bisher.

Damit lösen Dehnen/Mobility-Einheiten (z. B. Dienstag/Donnerstag) keine
Reinigung aus. Filter ändern: `SKIP_WORKOUT_REGEX` in
`/opt/docker-compose/opengym/docker-compose.yml` anpassen und
`docker compose -f /opt/docker-compose/opengym/docker-compose.yml up -d activity-shim`.
Leer setzen (`""`) = alle Workouts lösen aus.

## HA-Seite

`packages/gym.yaml`:

- **Automation** `GYM | Session-Start = Zimmer reinigen + Sportmodus`
  - Trigger: Webhook `gym_session_start` (POST)
  - Conditions: `person.carsten` = `home`, Zeitfenster 06:00–22:00,
    `vacuum.roborock_qrevo_edge_series` ≠ `cleaning`
  - Actions: `script.roborock_carsten_zimmer_wischen` (drückt die
    Roborock-Szene „Carsten Z. wischen"), `script.gym_sportmodus_push`,
    ntfy-Info auf Topic `home`
- **Skript** `GYM | Sportmodus-Push` — schickt `Routine: Sportmodus`
  (`title: openGym | Session gestartet`) an `device_id: dddeb792…`
- Registry: Kategorie `Gym` (`mdi:dumbbell`, Scopes automation + script),
  Automation-Labels `roborok`, `message`, `zone`, `ai_made`,
  Skript-Labels `message`, `ai_made`, Skript-Icon `mdi:dumbbell`

Zeitfenster ändern: Template-Condition „Zeitfenster 06:00-22:00" in
`packages/gym.yaml` anpassen. Abschalten: Automation in der UI deaktivieren.

### Handy-Seite

Die Handy-Routine matcht auf den **exakten** Text:

```
message: 'Routine: Sportmodus'
title:   'openGym | Session gestartet'
```

Sie darf den Webhook **nicht** aufrufen (sonst Schleife). Die bestehende
Bixby-Webhook-Automation `bixby_sportmodus_carsten_reinigung` bleibt als
manueller Weg erhalten (z. B. Training per Uhr).

## Test

```bash
# Session-Start simulieren (löst Reinigung + Push aus)
curl -X POST https://gym.mueller-nas.de/api/activity \
  -H 'content-type: application/json' \
  -d '{"active":true,"name":"Test","exTotal":6}'

# Session beenden / Sperre aufheben
curl -X POST https://gym.mueller-nas.de/api/activity \
  -H 'content-type: application/json' \
  -d '{"active":false,"reason":"closed"}'
```

Logs: `docker logs opengym-activity-shim` und
`docker logs --since 5m homeassistant | grep -i gym`.

## Rollback

```bash
cd /opt/docker-compose/opengym && docker compose stop activity-shim \
  && docker compose rm -f activity-shim
rm /etc/docker/homeassistant/packages/gym.yaml
docker restart homeassistant
```

Die Traefik-Route verschwindet mit dem Container; `/api/activity` läuft dann
wieder unverändert über `opengym-web` → `api`.

## Roborock-Szenen (Stand 2026-11-23)

Die Integration legt für jede Roborock-**Szene** einen Button an
(`RoborockRoutineButtonEntity`, `key=routine.id`). Die Cloud liefert aktuell
genau diese sechs Szenen:

| Szene | Entity |
|---|---|
| Badezimmer wischen | `button.wohnkuche_roborock_qrevo_edge_series_badezimmer_wischen` |
| Carsten Z. wischen | `button.wohnkuche_roborock_qrevo_edge_series_carsten_z_wischen` |
| Görkems Z. wischen | `button.wohnkuche_roborock_qrevo_edge_series_gorkems_zimmer` |
| Küche wischen | `button.wohnkuche_roborock_qrevo_edge_series_kuche_bad_saugen` |
| Wohnung wischen | `button.wohnkuche_roborock_qrevo_edge_series_wohnung_wischen` |
| Wohnzimmer wischen | `button.wohnkuche_roborock_qrevo_edge_series_wohnzimmer` |

Eine Szene „Carsten Z. saugen dann wischen" ist **nicht** dabei (auch nach
Neuanmeldung der Integration, 2026-11-23). Grundlage ist deshalb
`script.roborock_carsten_zimmer_wischen`.

Verlauf: Vor der Neuanmeldung waren fünf Szenen-Buttons `unavailable`
(`alles_saugen`, `carsten_z_saugen`, `saugen_dann_wischen2`, `intensiv`,
`nach_mahlzeiten`) – ein Zeichen für eine hängende Cloud-Session. Nach dem
Reauth sind sie verschwunden, die sechs oben sind live.

Hinweis: `script.roborock_carsten_zimmer_saugen_dann_wischen` (saugen → wischen)
nutzt den nicht mehr existierenden Button `…_carsten_z_saugen` und läuft
deshalb ins Leere. Es wird von der Gym-Automation nicht mehr verwendet.
