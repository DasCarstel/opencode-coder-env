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

## HA-Seite

`packages/gym.yaml`:

- **Automation** `GYM | Session-Start = Zimmer reinigen + Sportmodus`
  - Trigger: Webhook `gym_session_start` (POST)
  - Conditions: `person.carsten` = `home`, Zeitfenster 06:00–22:00,
    `vacuum.roborock_qrevo_edge_series` ≠ `cleaning`
  - Actions: `script.roborock_carsten_zimmer_saugen_dann_wischen`,
    `script.gym_sportmodus_push`, ntfy-Info auf Topic `home`
- **Skript** `GYM | Sportmodus-Push` — schickt `Routine: Sportmodus`
  (`title: openGym | Session gestartet`) an `device_id: dddeb792…`

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

## Offener Punkt (2026-11-23)

Beim Test zeigte sich: die Roborock-Szenen-Buttons für **saugen** sind
`unavailable`/`restored` —
`button.wohnkuche_roborock_qrevo_edge_series_carsten_z_saugen`,
`…_alles_saugen`, `button.roborock_qrevo_edge_series_saugen_dann_wischen2`,
`…_intensiv`, `…_nach_mahlzeiten`.
Die Wisch-Buttons (`…_carsten_z_wischen`, `…_badezimmer_wischen`,
`…_kuche_bad_saugen`) sind verfügbar.

`script.roborock_carsten_zimmer_saugen` drückt den nicht verfügbaren Button →
HA meldet `Referenced entities … are missing or not currently available`, der
Roboter startet nicht. Der Rest der Kette (Heartbeat → Shim → Webhook →
Automation → Push) ist verifiziert.
