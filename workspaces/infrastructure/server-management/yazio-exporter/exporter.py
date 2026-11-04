#!/usr/bin/env python3
"""YAZIO -> Postgres Exporter.

Meldet sich per OAuth2-Password-Grant an der YAZIO-API an, holt Tagebuch,
Naehrwerte, Ziele, Wasser, Gewicht, Aktivitaeten und Fastenzeiten und schreibt
alles idempotent in eine Postgres-Datenbank.

Aufruf:
    exporter.py --backfill                 # komplette Historie
    exporter.py --days 30                  # letzte 30 Tage
    exporter.py --date 2026-11-04          # ein Tag
    exporter.py --loop --interval 21600    # Dauerbetrieb (Standard im Container)

Konfiguration ueber Umgebungsvariablen (siehe README).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

import psycopg

API_BASE = os.environ.get("YAZIO_API", "https://yzapi.yazio.com")
CLIENT_ID = os.environ.get("YAZIO_CLIENT_ID", "").strip()
CLIENT_SECRET = os.environ.get("YAZIO_CLIENT_SECRET", "").strip()
USERNAME = os.environ.get("YAZIO_USERNAME", "").strip()
PASSWORD = os.environ.get("YAZIO_PASSWORD", "").strip()
DATABASE_URL = os.environ.get("DATABASE_URL", "")
APP_VERSION = os.environ.get("YAZIO_APP_VERSION", "26.36.1")
BUILD_NUMBER = os.environ.get("YAZIO_BUILD_NUMBER", "609040954")
TIMEZONE = os.environ.get("YAZIO_TIMEZONE", "Europe/Berlin")

BASE_HEADERS = {
    "accept": "application/json",
    "user-agent": "YAZIO/%s (Android)" % APP_VERSION,
    "x-yazio-app-version": APP_VERSION,
    "x-yazio-build-number": BUILD_NUMBER,
    "x-yazio-platform": "android",
    "x-yazio-language": "de",
    "x-yazio-country": "DE",
    "x-yazio-timezone": TIMEZONE,
}


# --------------------------------------------------------------------------- #
# Datenbank
# --------------------------------------------------------------------------- #

SCHEMA = """
CREATE TABLE IF NOT EXISTS oauth_token (
    id            boolean PRIMARY KEY DEFAULT true,
    access_token  text,
    refresh_token text,
    expires_at    timestamptz,
    updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS profile (
    id         boolean PRIMARY KEY DEFAULT true,
    raw        jsonb NOT NULL,
    fetched_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS diary_entry (
    id               text PRIMARY KEY,
    day              date NOT NULL,
    kind             text NOT NULL,
    daytime          text,
    product_id       text,
    amount           numeric,
    serving          text,
    serving_quantity numeric,
    raw              jsonb NOT NULL,
    updated_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS diary_entry_day_idx ON diary_entry (day);

CREATE TABLE IF NOT EXISTS product (
    id         text PRIMARY KEY,
    name       text,
    brand      text,
    raw        jsonb NOT NULL,
    fetched_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS nutrients_day (
    day         date PRIMARY KEY,
    energy      numeric,
    carb        numeric,
    protein     numeric,
    fat         numeric,
    energy_goal numeric,
    raw         jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS activity_day (
    day      date PRIMARY KEY,
    steps    numeric,
    duration numeric,
    energy   numeric,
    raw      jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS activity_detail (
    day    date PRIMARY KEY,
    steps  numeric,
    energy numeric,
    raw    jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS water_day (
    day        date PRIMARY KEY,
    water_ml   numeric,
    raw        jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS goal (
    day   date NOT NULL,
    key   text NOT NULL,
    value numeric,
    PRIMARY KEY (day, key)
);

CREATE TABLE IF NOT EXISTS feeling_day (
    day  date PRIMARY KEY,
    note text,
    tags jsonb,
    raw  jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS body_value (
    id         text PRIMARY KEY,
    day        date,
    type       text,
    value      numeric,
    raw        jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS fasting_plan (
    id         text PRIMARY KEY,
    plan_key   text,
    start_at   timestamptz,
    raw        jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sync_log (
    id         bigserial PRIMARY KEY,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    scope      text,
    requests   integer DEFAULT 0,
    ok         boolean
);
"""


def connect() -> psycopg.Connection:
    if not DATABASE_URL:
        sys.exit("DATABASE_URL fehlt")
    conn = psycopg.connect(DATABASE_URL, autocommit=True)
    conn.execute(SCHEMA)
    return conn


# --------------------------------------------------------------------------- #
# HTTP / OAuth
# --------------------------------------------------------------------------- #

class ApiError(RuntimeError):
    def __init__(self, status: int, body: str):
        super().__init__("HTTP %s: %s" % (status, body[:300]))
        self.status = status


class YazioClient:
    def __init__(self, conn: psycopg.Connection):
        self.conn = conn
        self.access_token: str | None = None
        self.refresh_token: str | None = None
        self.expires_at: datetime | None = None
        self.request_count = 0
        self._load_token()

    # -- Tokenverwaltung ---------------------------------------------------- #
    def _load_token(self) -> None:
        row = self.conn.execute(
            "SELECT access_token, refresh_token, expires_at FROM oauth_token WHERE id"
        ).fetchone()
        if row:
            self.access_token, self.refresh_token, self.expires_at = row

    def _store_token(self, payload: dict) -> None:
        self.access_token = payload["access_token"]
        self.refresh_token = payload.get("refresh_token") or self.refresh_token
        expires_in = payload.get("expires_in")
        self.expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=int(expires_in)) if expires_in else None
        )
        self.conn.execute(
            """
            INSERT INTO oauth_token (id, access_token, refresh_token, expires_at, updated_at)
            VALUES (true, %s, %s, %s, now())
            ON CONFLICT (id) DO UPDATE
               SET access_token = EXCLUDED.access_token,
                   refresh_token = EXCLUDED.refresh_token,
                   expires_at = EXCLUDED.expires_at,
                   updated_at = now()
            """,
            (self.access_token, self.refresh_token, self.expires_at),
        )

    def _token_valid(self) -> bool:
        if not self.access_token:
            return False
        if self.expires_at is None:
            return True
        return self.expires_at - datetime.now(timezone.utc) > timedelta(seconds=90)

    def _token_request(self, data: dict) -> dict:
        body = urllib.parse.urlencode(data).encode()
        request = urllib.request.Request(
            API_BASE + "/v22/oauth/token",
            data=body,
            headers=dict(BASE_HEADERS, **{"content-type": "application/x-www-form-urlencoded"}),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode())
        except urllib.error.HTTPError as error:
            raise ApiError(error.code, error.read().decode(errors="replace")) from error

    def ensure_token(self) -> None:
        if self._token_valid():
            return
        if self.refresh_token:
            try:
                self._store_token(
                    self._token_request(
                        {
                            "grant_type": "refresh_token",
                            "refresh_token": self.refresh_token,
                            "client_id": CLIENT_ID,
                            "client_secret": CLIENT_SECRET,
                        }
                    )
                )
                return
            except ApiError as error:
                if error.status not in (400, 401):
                    raise
                print("Refresh-Token abgelehnt, Password-Grant noetig", file=sys.stderr)
        if not (USERNAME and PASSWORD):
            sys.exit("Kein gueltiger Token und keine Zugangsdaten hinterlegt")
        self._store_token(
            self._token_request(
                {
                    "grant_type": "password",
                    "username": USERNAME,
                    "password": PASSWORD,
                    "client_id": CLIENT_ID,
                    "client_secret": CLIENT_SECRET,
                }
            )
        )

    # -- API ---------------------------------------------------------------- #
    def get(self, path: str, **params) -> object:
        self.ensure_token()
        query = ("?" + urllib.parse.urlencode(params)) if params else ""
        request = urllib.request.Request(
            API_BASE + path + query,
            headers=dict(BASE_HEADERS, authorization="Bearer " + (self.access_token or "")),
        )
        for attempt in range(4):
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    self.request_count += 1
                    raw = response.read()
                    return json.loads(raw) if raw else None
            except urllib.error.HTTPError as error:
                if error.code == 401 and attempt == 0:
                    self.access_token = None
                    self.ensure_token()
                    request.add_header("authorization", "Bearer " + (self.access_token or ""))
                    continue
                if error.code == 429:
                    wait = 5 * (attempt + 1)
                    print("429 erhalten, warte %ss" % wait, file=sys.stderr)
                    time.sleep(wait)
                    continue
                if error.code == 404:
                    return None
                raise ApiError(error.code, error.read().decode(errors="replace")) from error
        raise ApiError(429, "Rate-Limit nicht aufgeloest")


# --------------------------------------------------------------------------- #
# Sync
# --------------------------------------------------------------------------- #

def month_ranges(start: date, end: date):
    current = date(start.year, start.month, 1)
    while current <= end:
        if current.month == 12:
            following = date(current.year + 1, 1, 1)
        else:
            following = date(current.year, current.month + 1, 1)
        yield current, min(following - timedelta(days=1), end)
        current = following


def store_nutrients(conn, client, start: date, end: date) -> set[date]:
    payload = client.get(
        "/v22/user/consumed-items/nutrients-daily",
        start=start.isoformat(),
        end=end.isoformat(),
    )
    days = set()
    for row in payload or []:
        day = date.fromisoformat(row["date"])
        days.add(day)
        conn.execute(
            """
            INSERT INTO nutrients_day (day, energy, carb, protein, fat, energy_goal, raw)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (day) DO UPDATE SET energy = EXCLUDED.energy, carb = EXCLUDED.carb,
                protein = EXCLUDED.protein, fat = EXCLUDED.fat,
                energy_goal = EXCLUDED.energy_goal, raw = EXCLUDED.raw
            """,
            (
                day,
                row.get("energy"),
                row.get("carb"),
                row.get("protein"),
                row.get("fat"),
                row.get("energy_goal"),
                json.dumps(row),
            ),
        )
    return days


def store_activity_summary(conn, client, start: date, end: date) -> set[date]:
    payload = client.get(
        "/v22/user/exercises/summary-daily", start=start.isoformat(), end=end.isoformat()
    )
    days = set()
    for row in payload or []:
        day = date.fromisoformat(row["date"])
        days.add(day)
        conn.execute(
            """
            INSERT INTO activity_day (day, steps, duration, energy, raw)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (day) DO UPDATE SET steps = EXCLUDED.steps,
                duration = EXCLUDED.duration, energy = EXCLUDED.energy, raw = EXCLUDED.raw
            """,
            (day, row.get("steps"), row.get("duration"), row.get("energy"), json.dumps(row)),
        )
    return days


def store_diary(conn, client, day: date) -> None:
    payload = client.get("/v22/user/consumed-items", date=day.isoformat()) or {}
    entries = []
    for kind, rows in (
        ("product", payload.get("products") or []),
        ("recipe", payload.get("recipe_portions") or []),
        ("simple", payload.get("simple_products") or []),
    ):
        for row in rows:
            entries.append((kind, row))
    for kind, row in entries:
        entry_id = str(row.get("id") or "")
        if not entry_id:
            continue
        conn.execute(
            """
            INSERT INTO diary_entry
                (id, day, kind, daytime, product_id, amount, serving, serving_quantity, raw)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET day = EXCLUDED.day, kind = EXCLUDED.kind,
                daytime = EXCLUDED.daytime, product_id = EXCLUDED.product_id,
                amount = EXCLUDED.amount, serving = EXCLUDED.serving,
                serving_quantity = EXCLUDED.serving_quantity, raw = EXCLUDED.raw,
                updated_at = now()
            """,
            (
                entry_id,
                day,
                kind,
                row.get("daytime"),
                row.get("product_id"),
                row.get("amount"),
                row.get("serving"),
                row.get("serving_quantity"),
                json.dumps(row),
            ),
        )


def store_water(conn, client, day: date) -> None:
    payload = client.get("/v22/user/water-intake", date=day.isoformat())
    if not payload:
        return
    conn.execute(
        """
        INSERT INTO water_day (day, water_ml, raw) VALUES (%s, %s, %s)
        ON CONFLICT (day) DO UPDATE SET water_ml = EXCLUDED.water_ml, raw = EXCLUDED.raw
        """,
        (day, payload.get("water_intake"), json.dumps(payload)),
    )


def store_goals(conn, client, day: date) -> None:
    payload = client.get("/v22/user/goals", date=day.isoformat())
    if not isinstance(payload, dict):
        return
    for key, value in payload.items():
        if not isinstance(value, (int, float)):
            continue
        conn.execute(
            """
            INSERT INTO goal (day, key, value) VALUES (%s, %s, %s)
            ON CONFLICT (day, key) DO UPDATE SET value = EXCLUDED.value
            """,
            (day, key, float(value)),
        )


def store_feeling(conn, client, day: date) -> None:
    payload = client.get("/v22/user/feeling", date=day.isoformat())
    if not payload:
        return
    conn.execute(
        """
        INSERT INTO feeling_day (day, note, tags, raw) VALUES (%s, %s, %s, %s)
        ON CONFLICT (day) DO UPDATE SET note = EXCLUDED.note, tags = EXCLUDED.tags,
            raw = EXCLUDED.raw
        """,
        (day, payload.get("note"), json.dumps(payload.get("tags") or []), json.dumps(payload)),
    )


def store_activity_detail(conn, client, day: date) -> None:
    payload = client.get("/v22/user/exercises", date=day.isoformat())
    activity = (payload or {}).get("activity") or {}
    if not activity:
        return
    conn.execute(
        """
        INSERT INTO activity_detail (day, steps, energy, raw) VALUES (%s, %s, %s, %s)
        ON CONFLICT (day) DO UPDATE SET steps = EXCLUDED.steps, energy = EXCLUDED.energy,
            raw = EXCLUDED.raw
        """,
        (day, activity.get("steps"), activity.get("energy"), json.dumps(payload)),
    )


def store_bodyvalues(conn, client, day: date) -> None:
    payload = client.get("/v22/user/bodyvalues", date=day.isoformat())
    if not isinstance(payload, dict):
        return
    for kind, value in payload.items():
        # Die API liefert je Koerperwert eine Liste von Messungen.
        items = value if isinstance(value, list) else [value]
        for item in items:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id") or "%s-%s" % (kind, day))
            conn.execute(
                """
                INSERT INTO body_value (id, day, type, value, raw) VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET day = EXCLUDED.day, type = EXCLUDED.type,
                    value = EXCLUDED.value, raw = EXCLUDED.raw, updated_at = now()
                """,
                (item_id, day, kind, item.get("value"), json.dumps(item)),
            )


def store_fasting(conn, client) -> None:
    """Speichert Fastenplaene.

    /fasting-countdowns liefert den laufenden Plan (Fenster als Tag/Zeit-Paare),
    /fasting-countdowns/history die frueheren Plaene. Die Fenster sind eine
    Vorlage, keine gebuchten Fastenzeiten - deshalb eine Zeile pro Plan.
    """
    plans = []
    current = client.get("/v22/user/fasting-countdowns")
    if isinstance(current, dict) and current.get("key"):
        plans.append(current)
    history = client.get("/v22/user/fasting-countdowns/history") or []
    if isinstance(history, list):
        plans.extend(item for item in history if isinstance(item, dict) and item.get("key"))
    for plan in plans:
        plan_key = str(plan.get("key"))
        start = plan.get("start")
        plan_id = "%s|%s" % (plan_key, start)
        conn.execute(
            """
            INSERT INTO fasting_plan (id, plan_key, start_at, raw)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET plan_key = EXCLUDED.plan_key,
                start_at = EXCLUDED.start_at, raw = EXCLUDED.raw, updated_at = now()
            """,
            (plan_id, plan_key, start, json.dumps(plan)),
        )


def store_profile(conn, client) -> dict:
    payload = client.get("/v22/user") or {}
    conn.execute(
        """
        INSERT INTO profile (id, raw, fetched_at) VALUES (true, %s, now())
        ON CONFLICT (id) DO UPDATE SET raw = EXCLUDED.raw, fetched_at = now()
        """,
        (json.dumps(payload),),
    )
    return payload


def sync_products(conn, client, day_ids: set[str]) -> None:
    if not day_ids:
        return
    known = {
        row[0]
        for row in conn.execute(
            "SELECT id FROM product WHERE id = ANY(%s)", (list(day_ids),)
        ).fetchall()
    }
    for product_id in day_ids - known:
        payload = client.get("/v22/products/" + product_id)
        if not isinstance(payload, dict):
            continue
        conn.execute(
            """
            INSERT INTO product (id, name, brand, raw) VALUES (%s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, brand = EXCLUDED.brand,
                raw = EXCLUDED.raw, fetched_at = now()
            """,
            (
                product_id,
                payload.get("name"),
                payload.get("brand"),
                json.dumps(payload),
            ),
        )


def sync(conn, client, start: date, end: date, with_products: bool = True) -> None:
    log_id = conn.execute(
        "INSERT INTO sync_log (scope) VALUES (%s) RETURNING id",
        ("%s..%s" % (start, end),),
    ).fetchone()[0]
    print("Sync %s bis %s" % (start, end))

    profile = store_profile(conn, client)
    today = date.today()
    active_days: set[date] = set()

    for month_start, month_end in month_ranges(start, min(end, today)):
        active_days |= store_nutrients(conn, client, month_start, month_end)
        active_days |= store_activity_summary(conn, client, month_start, month_end)

    days = sorted(d for d in active_days if start <= d <= end)
    print("  %d Tage mit Daten" % len(days))

    product_ids: set[str] = set()
    for day in days:
        store_diary(conn, client, day)
        store_water(conn, client, day)
        store_goals(conn, client, day)
        store_feeling(conn, client, day)
        store_activity_detail(conn, client, day)
        store_bodyvalues(conn, client, day)

    for row in conn.execute("SELECT DISTINCT product_id FROM diary_entry WHERE product_id IS NOT NULL"):
        if row[0]:
            product_ids.add(row[0])
    if with_products:
        sync_products(conn, client, product_ids)

    store_fasting(conn, client)

    conn.execute(
        "UPDATE sync_log SET finished_at = now(), requests = %s, ok = true WHERE id = %s",
        (client.request_count, log_id),
    )
    print("  fertig, %d Anfragen" % client.request_count)


def account_start(client, conn) -> date:
    profile = store_profile(conn, client)
    registration = profile.get("registration_date")
    if isinstance(registration, str):
        try:
            return date.fromisoformat(registration[:10])
        except ValueError:
            pass
    return date.today() - timedelta(days=365)


def main() -> None:
    parser = argparse.ArgumentParser(description="YAZIO -> Postgres")
    parser.add_argument("--backfill", action="store_true", help="komplette Historie")
    parser.add_argument("--days", type=int, help="nur die letzten N Tage")
    parser.add_argument("--date", help="ein einzelner Tag (YYYY-MM-DD)")
    parser.add_argument("--loop", action="store_true", help="Dauerbetrieb")
    parser.add_argument("--interval", type=int, default=21600, help="Sekunden zwischen Laeufen")
    parser.add_argument("--no-products", action="store_true", help="keine Produktdetails holen")
    args = parser.parse_args()

    conn = connect()
    client = YazioClient(conn)
    with_products = not args.no_products

    if args.loop:
        while True:
            try:
                today = date.today()
                sync(conn, client, today - timedelta(days=3), today, with_products)
            except Exception as error:  # Dauerbetrieb darf nicht sterben
                print("Sync fehlgeschlagen: %s" % error, file=sys.stderr)
            time.sleep(args.interval)

    if args.date:
        day = date.fromisoformat(args.date)
        sync(conn, client, day, day, with_products)
    elif args.days:
        today = date.today()
        sync(conn, client, today - timedelta(days=args.days), today, with_products)
    elif args.backfill:
        sync(conn, client, account_start(client, conn), date.today(), with_products)
    else:
        parser.error("eines von --backfill, --days, --date oder --loop angeben")


if __name__ == "__main__":
    main()
