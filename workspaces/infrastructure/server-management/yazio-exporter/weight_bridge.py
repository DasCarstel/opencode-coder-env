#!/usr/bin/env python3
"""openScale (MQTT) -> Postgres -> YAZIO.

Hoert auf dem MQTT-Broker die Messungen der openScale-Sync-App, schreibt sie in
die Tabelle ``body_measurement`` und traegt das Gewicht zusaetzlich in YAZIO ein,
sofern fuer den Tag dort noch kein Gewicht steht.

Aufruf:
    weight_bridge.py            # Dauerbetrieb im Container

Konfiguration ueber Umgebungsvariablen (siehe README).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import paho.mqtt.client as mqtt
import psycopg

import exporter as yz

MQTT_HOST = os.environ.get("MQTT_HOST", "10.0.10.10")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USER = os.environ.get("MQTT_USER", "openscale")
MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD", "")
MQTT_TOPIC = os.environ.get("MQTT_TOPIC", "openScaleSync/#")
PUSH_WEIGHT = os.environ.get("YAZIO_PUSH_WEIGHT", "1") == "1"
LOCAL_TZ = ZoneInfo(os.environ.get("YAZIO_TIMEZONE", "Europe/Berlin"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS body_measurement (
    id            bigserial PRIMARY KEY,
    openscale_id  bigint,
    measured_at   timestamptz NOT NULL,
    weight_kg     numeric(6,2),
    body_fat_pct  numeric(5,2),
    water_pct     numeric(5,2),
    muscle_pct    numeric(5,2),
    lbm_pct       numeric(5,2),
    bone_pct      numeric(5,2),
    source        text NOT NULL DEFAULT 'openscale',
    raw           jsonb,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS body_measurement_openscale_id
    ON body_measurement (openscale_id) WHERE openscale_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS body_measurement_measured_at
    ON body_measurement (measured_at);
"""

IDENTITIES = {
    "builtin.weight": "weight_kg",
    "builtin.body_fat": "body_fat_pct",
    "builtin.water": "water_pct",
    "builtin.muscle": "muscle_pct",
    "builtin.lbm": "lbm_pct",
    "builtin.bone": "bone_pct",
}

CONVENIENCE = {
    "weight": "weight_kg",
    "body_fat": "body_fat_pct",
    "water": "water_pct",
    "muscle": "muscle_pct",
    "lbm": "lbm_pct",
    "bone": "bone_pct",
}


def log(message: str) -> None:
    print("%s %s" % (datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), message), flush=True)


def parse_moment(value: str | None) -> datetime | None:
    """openScale liefert "2025-12-27T10:30+0100"."""
    if not value:
        return None
    for pattern in ("%Y-%m-%dT%H:%M%z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S%z"):
        try:
            return datetime.strptime(value, pattern)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def extract(payload: dict) -> dict:
    values: dict[str, object] = {column: None for column in IDENTITIES.values()}

    for item in payload.get("values") or []:
        column = IDENTITIES.get(str(item.get("identity", "")))
        if column and item.get("value") is not None:
            values[column] = item["value"]

    for key, column in CONVENIENCE.items():
        if values[column] is None and payload.get(key) is not None:
            values[column] = payload[key]

    return values


def store(conn: psycopg.Connection, payload: dict) -> tuple[datetime, float] | None:
    measured_at = parse_moment(payload.get("date"))
    if measured_at is None:
        log("Messung ohne lesbares Datum uebersprungen: %s" % payload)
        return None

    values = extract(payload)
    conn.execute(
        """
        INSERT INTO body_measurement (openscale_id, measured_at, weight_kg, body_fat_pct,
                                      water_pct, muscle_pct, lbm_pct, bone_pct, source, raw)
        VALUES (%(openscale_id)s, %(measured_at)s, %(weight_kg)s, %(body_fat_pct)s,
                %(water_pct)s, %(muscle_pct)s, %(lbm_pct)s, %(bone_pct)s, 'openscale', %(raw)s)
        ON CONFLICT (openscale_id) WHERE openscale_id IS NOT NULL DO UPDATE
           SET measured_at  = EXCLUDED.measured_at,
               weight_kg    = EXCLUDED.weight_kg,
               body_fat_pct = EXCLUDED.body_fat_pct,
               water_pct    = EXCLUDED.water_pct,
               muscle_pct   = EXCLUDED.muscle_pct,
               lbm_pct      = EXCLUDED.lbm_pct,
               bone_pct     = EXCLUDED.bone_pct,
               raw          = EXCLUDED.raw,
               updated_at   = now()
        """,
        {
            "openscale_id": payload.get("id"),
            "measured_at": measured_at,
            "raw": json.dumps(payload, ensure_ascii=False),
            **values,
        },
    )
    log(
        "Messung gespeichert: %s %s kg (Fett %s)"
        % (measured_at.isoformat(), values["weight_kg"], values["body_fat_pct"])
    )

    weight = values["weight_kg"]
    if weight is None:
        return None
    return measured_at, float(weight)


def delete_moment(conn: psycopg.Connection, payload: dict) -> None:
    moment = parse_moment(payload.get("date") or payload.get("dateTime"))
    if moment is None:
        return
    cur = conn.execute(
        "DELETE FROM body_measurement WHERE measured_at = %s AND source = 'openscale'", (moment,)
    )
    log("Messung geloescht: %s (%s Zeilen)" % (moment.isoformat(), cur.rowcount))


def clear_all(conn: psycopg.Connection) -> None:
    cur = conn.execute("DELETE FROM body_measurement WHERE source = 'openscale'")
    log("Alle openScale-Messungen geloescht (%s Zeilen)" % cur.rowcount)


def post_json(client: yz.YazioClient, path: str, payload: dict) -> int:
    client.ensure_token()
    request = urllib.request.Request(
        yz.API_BASE + path,
        data=json.dumps(payload).encode(),
        headers=dict(
            yz.BASE_HEADERS,
            authorization="Bearer " + (client.access_token or ""),
            **{"content-type": "application/json"},
        ),
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status
    except urllib.error.HTTPError as error:
        if error.code == 401:
            client.access_token = None
            client.ensure_token()
            return post_json(client, path, payload)
        raise


def push_weight(conn: psycopg.Connection, client: yz.YazioClient, moment: datetime, weight: float) -> None:
    if not PUSH_WEIGHT:
        return
    day = moment.astimezone(LOCAL_TZ).date()
    existing = client.get("/v22/user/bodyvalues", date=day.isoformat()) or {}
    if existing.get("weight"):
        log("YAZIO hat fuer %s schon ein Gewicht, kein Eintrag" % day)
        return
    status = post_json(client, "/v22/user/bodyvalues", {"date": day.isoformat(), "value": weight, "type": "weight"})
    log("YAZIO-Gewicht fuer %s eingetragen: %s kg (HTTP %s)" % (day, weight, status))


def handle(conn: psycopg.Connection, client: yz.YazioClient, event: str, payload: object) -> None:
    if event == "clear":
        clear_all(conn)
        return
    if event == "delete":
        delete_moment(conn, payload if isinstance(payload, dict) else json.loads(payload))
        return

    items = payload if isinstance(payload, list) else [payload]
    if isinstance(payload, dict) and isinstance(payload.get("measurements"), list):
        items = payload["measurements"]

    newest: tuple[datetime, float] | None = None
    for item in items:
        if not isinstance(item, dict):
            continue
        stored = store(conn, item)
        if stored and (newest is None or stored[0] > newest[0]):
            newest = stored

    if newest:
        try:
            push_weight(conn, client, newest[0], newest[1])
        except Exception as error:  # YAZIO-Ausfall darf die Aufzeichnung nicht stoppen
            log("YAZIO-Eintrag fehlgeschlagen: %s" % error)


def on_connect(client: mqtt.Client, userdata, flags, reason_code, properties=None) -> None:
    log("MQTT verbunden (%s), abonniere %s" % (reason_code, MQTT_TOPIC))
    client.subscribe(MQTT_TOPIC, qos=1)


def on_message(client: mqtt.Client, userdata, message) -> None:
    db, yazio = userdata
    topic = message.topic
    event = topic.rsplit("/", 1)[-1]
    try:
        payload = json.loads(message.payload.decode() or "null")
    except json.JSONDecodeError:
        log("Ungueltiges JSON auf %s" % topic)
        return
    try:
        handle(db, yazio, event, payload)
    except psycopg.Error as error:
        log("Datenbankfehler bei %s: %s" % (topic, error))
    except Exception as error:
        log("Fehler bei %s: %s" % (topic, error))


def main() -> None:
    conn = yz.connect()
    conn.execute(SCHEMA)
    yazio = yz.YazioClient(conn)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="yazio-weight-bridge")
    if MQTT_USER:
        client.username_pw_set(MQTT_USER, MQTT_PASSWORD)
    client.user_data_set((conn, yazio))
    client.on_connect = on_connect
    client.on_message = on_message

    while True:
        try:
            client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
            break
        except OSError as error:
            log("MQTT nicht erreichbar (%s), neuer Versuch in 15s" % error)
            import time

            time.sleep(15)

    log("Weight-Bridge laeuft")
    client.loop_forever(retry_first_connection=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
