#!/usr/bin/env python3
"""Regressionstests für hestia/backend/wardrobe_billing.py — offline, kein Netz.

Abgedeckt: disabled-Payload ohne Env (Feature off), Monats-Tag-SKU-Aggregation
(rein, offline), BigQuery-Fake-Kette (Tabellen-Erkennung + Nutz-Query →
Aggregates), 30-Minuten-Cache (zweiter Call fragt NICHT erneut) und API-
Route-Smoke: echter ThreadingHTTPServer auf Port 0, GET /api/wardrobe/billing
mit/ohne Basic-Auth (Muster ci/test_wardrobe_costs.py / ohne echte Abhängiges).

Bewusst UNABHÄNGIG von test_wardrobe_panel.py: kein geteiltes State-Mutieren,
WARDROBE_*-Env wird je Test gesichert/wiederhergestellt. Optional-Import von
pytest — läufbar wie die übrigen ci-Suiten mit der check()/fails-Konvention.
"""
import base64
import datetime
import os
import sys
import threading
import urllib.request

BACKEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
sys.path.insert(0, BACKEND)

import wardrobe_billing as wb  # noqa: E402

fails = []


def check(label, cond, extra=""):
    mark = "ok  " if cond else "FAIL"
    print(f"  {mark} {label}" + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(label)


PAYLOAD_KEYS = {"enabled", "ok", "error", "table", "last_date", "total_month",
                "by_day", "by_sku", "retrieved_at"}


# --- Aggregate (rein, offline) ----------------------------------------------


def test_aggregate_mixed_rows():
    today = datetime.date(2026, 11, 9)
    prev = datetime.date(2026, 10, 31)
    rows = [
        {"day": "2026-11-09", "sku": "Image Output", "cost": 1.5},
        {"day": datetime.date(2026, 11, 8), "sku": "Image Output", "cost": 2.0},
        {"day": "2026-11-08", "sku": "Text Input", "cost": 0.25},
        {"day": "2026-11-01", "sku": "Image Output", "cost": 0.5},
        {"day": prev, "sku": "Image Output", "cost": 99.0},   # Vorat-Monat
        {"day": None, "sku": "Broken", "cost": 123.0},        # kippt nichts
    ]
    a = wb.aggregate(rows, today=today)
    check("aggregate: total_month nur laufender Monat", abs(a["total_month"] - 4.25) < 1e-9,
          f"{a['total_month']}")
    check("aggregate: last_date neuester Tag", a["last_date"] == "2026-11-09", f"{a}")
    check("aggregate: by_day absteigend",
          [d["day"] for d in a["by_day"]] == ["2026-11-09", "2026-11-08", "2026-11-01", "2026-10-31"],
          f"{a['by_day']}")
    day8 = [d for d in a["by_day"] if d["day"] == "2026-11-08"][0]
    check("aggregate: by_day sums (2 SKUs am 8.)", abs(day8["cost"] - 2.25) < 1e-9, f"{day8}")
    check("aggregate: by_sku teuerste zuerst",
          [s["sku"] for s in a["by_sku"]] == ["Image Output", "Text Input"], f"{a['by_sku']}")
    img = [s for s in a["by_sku"] if s["sku"] == "Image Output"][0]
    check("aggregate: by_sku Monats-Summe (ohne Vorat-Monat)", abs(img["cost_month"] - 4.0) < 1e-9,
          f"{img}")


def test_aggregate_empty_and_garbage():
    a = wb.aggregate([])
    check("aggregate leer: total_month 0", a["total_month"] == 0, f"{a}")
    check("aggregate leer: last_date None", a["last_date"] is None, f"{a}")
    check("aggregate leer: Listen leer", a["by_day"] == [] and a["by_sku"] == [], f"{a}")
    b = wb.aggregate([{"day": "not-a-date", "sku": "x", "cost": "y"}])
    check("aggregate garbage: fällt raus", b["by_day"] == [] and b["by_sku"] == [], f"{b}")


# --- Disabled-Payloads (Env fehlt / Bibliothek fehlt) -----------------------


def test_disabled_without_env():
    old = {k: os.environ.pop(k, None) for k in (wb.DATASET_ENV, wb.PROJECT_ENV)}
    old_cache = dict(wb.CACHE)
    try:
        wb.CACHE.update(ts=0.0, data=None)
        s = wb.billing_state(force=True)
        check("disabled: enabled False", s["enabled"] is False, f"{s}")
        check("disabled: ok False + error gen",
              s["ok"] is False and "WARDROBE_GCP_DATASET" in (s["error"] or ""), f"{s}")
        check("disabled: Kontrakt vollständig", set(s.keys()) == PAYLOAD_KEYS, f"{s.keys()}")
        check("disabled: Listen leer", s["by_day"] == [] and s["by_sku"] == [], f"{s}")
    finally:
        wb.CACHE.update(ts=old_cache["ts"], data=old_cache["data"])
        for k, v in old.items():
            if v is not None:
                os.environ[k] = v


def test_error_never_crashes():
    # _fetch mit gerissenen Client-Ergebnissen — Fehler landen als reason.
    old = {k: os.environ.pop(k, None) for k in (wb.DATASET_ENV, wb.PROJECT_ENV)}
    old_cache, old_client = dict(wb.CACHE), wb._client
    try:
        wb.CACHE.update(ts=0.0, data=None)
        os.environ[wb.DATASET_ENV] = "billing_export"
        os.environ[wb.PROJECT_ENV] = "proj"
        wb._client = lambda project: (None, "boom im Fake")
        s = wb.billing_state(force=True)
        check("client None → disabled", s["ok"] is False and s["error"] == "boom im Fake", f"{s}")
        wb._client = lambda project: (RaisingClient(), None)
        s = wb.billing_state(force=True)
        check("query wirft → disabled mit Grund",
              s["ok"] is False and "Tabellen-Erkennung" in (s["error"] or ""), f"{s}")
    finally:
        wb._client = old_client
        wb.CACHE.update(ts=old_cache["ts"], data=old_cache["data"])
        for k, v in old.items():
            if v is not None:
                os.environ[k] = v


# --- Fake-BigQuery-Kette -----------------------------------------------------


class _FakeTableJob:
    def __iter__(self):
        return iter([{"table_name": "gcp_billing_export_resource_v1_012345"}])


class _FakeRowsJob:
    def __iter__(self):
        return iter([
            {"day": "2026-11-09", "sku": "Image Output", "cost": 1.5},
            {"day": "2026-11-08", "sku": "Image Output", "cost": 2.0},
            {"day": "2026-11-08", "sku": "Text Input", "cost": 0.25},
            {"day": "2026-10-31", "sku": "Image Output", "cost": 9.0},
        ])


class FakeClient:
    """Bluff-BigQuery: INFORMATION_SCHEMA-Query → Tabelle, Nutz-Query → Zeilen."""

    def __init__(self, sqls=None):
        self.queries = []
        self.sqls = sqls

    def query(self, sql):
        self.queries.append(sql)
        self.last_sql = sql
        if "INFORMATION_SCHEMA" in sql:
            return _FakeTableJob()
        return _FakeRowsJob()


class RaisingClient:
    def query(self, sql):
        raise RuntimeError("q-fail")


def test_billing_state_with_fake_client():
    old = {k: os.environ.pop(k, None) for k in (wb.DATASET_ENV, wb.PROJECT_ENV)}
    old_cache, old_client = dict(wb.CACHE), wb._client
    try:
        wb.CACHE.update(ts=0.0, data=None)
        os.environ[wb.DATASET_ENV] = "billing_export"
        os.environ[wb.PROJECT_ENV] = "proj"
        fake = FakeClient()
        wb._client = lambda project: (fake, None)
        s = wb.billing_state(force=True)
        check("fake: enabled+ok", s["enabled"] is True and s["ok"] is True, f"{s}")
        check("fake: table qualified",
              s["table"] == "proj.billing_export.gcp_billing_export_resource_v1_012345", s["table"])
        check("fake: Kontrakt vollständig", set(s.keys()) == PAYLOAD_KEYS, f"{s.keys()}")
        check("fake: total_month nur Monat", abs(s["total_month"] - 3.75) < 1e-9, s["total_month"])
        check("fake: last_date", s["last_date"] == "2026-11-09", f"{s}")
        check("fake: by_sku top", s["by_sku"][0]["sku"] == "Image Output",
              f"{[x['sku'] for x in s['by_sku']]}")
        check("fake: 2 Queries ( Erkennung + Nutz)", len(fake.queries) == 2,
              f"{len(fake.queries)}")
        usage_sql = fake.last_sql
        check("fake: Nutz-Query Gemini-API-Filter", "Gemini API" in usage_sql
              and "gcp_billing_export_resource_v1_012345" in usage_sql
              and "INTERVAL 35 DAY" in usage_sql, usage_sql[:120])
    finally:
        wb._client = old_client
        wb.CACHE.update(ts=old_cache["ts"], data=old_cache["data"])
        for k, v in old.items():
            if v is not None:
                os.environ[k] = v


def test_billing_state_cache_hit():
    old = {k: os.environ.pop(k, None) for k in (wb.DATASET_ENV, wb.PROJECT_ENV)}
    old_cache, old_client = dict(wb.CACHE), wb._client
    calls = {"n": 0}
    try:
        wb.CACHE.update(ts=0.0, data=None)
        os.environ[wb.DATASET_ENV] = "billing_export"
        os.environ[wb.PROJECT_ENV] = "proj"

        def counting_client(project):
            calls["n"] += 1
            return FakeClient(), None

        wb._client = counting_client
        wb.billing_state(force=True)
        check("cache: erste Abfrage läuft", calls["n"] == 1, f"{calls}")
        s2 = wb.billing_state()   # im 30-min-Fenster → KEINE zweite Abfrage
        check("cache: zweiter Call fragt nicht erneut", calls["n"] == 1, f"{calls}")
        check("cache: Rückgabe identisch", s2["table"] == "proj.billing_export.gcp_billing_export_resource_v1_012345", s2.get("table"))
        wb.CACHE.update(ts=0.0, data=None)
        wb.billing_state()
        check("cache: nach Nullstellen erneut", calls["n"] == 2, f"{calls}")
    finally:
        wb._client = old_client
        wb.CACHE.update(ts=old_cache["ts"], data=old_cache["data"])
        for k, v in old.items():
            if v is not None:
                os.environ[k] = v


# --- API-Route-Smoke (echter Handler auf Port 0, ohne Netz) -----------------


def _start_server():
    import server as server_mod
    httpd = server_mod.ThreadingHTTPServer(("127.0.0.1", 0), server_mod.Handler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return server_mod, httpd, port


def test_api_route_smoke():
    try:
        server_mod, httpd, port = _start_server()
    except Exception as e:  # noqa: BLE001 — server.py-Import nicht testreif? Skip.
        print(f"  SKIP API-Smoke (Server-Import): {e}")
        return
    import json as _json
    import base64 as _b64
    import urllib.error  # noqa: F401 (pflicht für die 401-Erwartung)
    old_billing_mod = server_mod.wardrobe_billing_mod
    try:
        fake_state = {
            "enabled": True, "ok": True, "error": None,
            "table": "p.d.gcp_billing_export_resource_v1_1",
            "last_date": "2026-11-09", "total_month": 3.75,
            "by_day": [{"day": "2026-11-09", "cost": 1.5}],
            "by_sku": [{"sku": "Image Output", "cost_month": 3.5}],
            "retrieved_at": "2026-11-09T12:00:00",
        }
        server_mod.wardrobe_billing_mod = type(
            "FakeBilling", (), {"billing_state": staticmethod(lambda force=False: fake_state)})
        # Ohne Auth → 401, mit Basic → 200 + exakt der Fake-Payload
        base = f"http://127.0.0.1:{port}/api/wardrobe/billing"
        try:
            urllib.request.urlopen(base, timeout=5)
            check("smoke: ohne Auth 401", False, "kein 401 bekommen")
        except urllib.error.HTTPError as e:
            check("smoke: ohne Auth 401", e.code == 401, f"{e.code}")
        pw = server_mod.HESTIA_PASSWORD  # generiertes Secret des Testprozesses
        req = urllib.request.Request(base)
        req.add_header("Authorization",
                       "Basic " + _b64.b64encode(f"hestia:{pw}".encode()).decode())
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = _json.loads(resp.read().decode())
            check("smoke: 200 + fake-payload", resp.status == 200 and body == fake_state, f"{body}")
    finally:
        server_mod.wardrobe_billing_mod = old_billing_mod
        httpd.shutdown()


if __name__ == "__main__":
    tests = True
    try:
        import pytest  # noqa: F401
    except ImportError:
        tests = False
    if tests:
        rc = pytest.main(["-q", "--no-header", "-p", "no:cacheprovider", __file__])
        sys.exit(0 if rc == 0 else 1)
    else:
        # pytest nicht installiert (z. B. lokal ohne CI-Dependencies):
        # Tests direkt laufen lassen.
        import inspect
        failed = 0
        for name, fn in sorted(globals().items()):
            if name.startswith("test_") and inspect.isfunction(fn):
                try:
                    fn()
                    print(f"{name} ok")
                except Exception as e:  # noqa: BLE001
                    failed += 1
                    print(f"{name} FAILED: {e}")
        if fails:  # check()-basierte Meldungen mitzählen (Direktlauf)
            print(f"{len(fails)} Prüfung(en) fehlgeschlagen")
            failed += 1
        sys.exit(1 if failed else 0)
