#!/usr/bin/env python3
"""Regressionstests für hestia/backend/wardrobe_panel.py — offline, ohne Netz UND ohne DB.

Abgedeckt: Kostenformel je Bild-Modell (Token-Split × Preistabelle), unbekannte
Modelle (cost=None), per_image-Schätztabelle, Ampel-Schwellen (ok <60 %, warn
60–90 %, err >90 % / negatives Restbudget), SUMME-Aggregation (Monat/lifetime/
by_model), Einstellungs-Defaults + Validierung (apply_settings-Grundlage) und
der volständige Payload mit Fake-Verbindung (settings/Aggregates/Recent-Rows)
sowie DSN-fehlt (db.ok=false statt Crash).

Läuft mit pytest UND direkt:
    pytest -q hestia/ci/test_wardrobe_panel.py
    python3 hestia/ci/test_wardrobe_panel.py
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

import wardrobe_panel  # noqa: E402

fails = []


def check(label, cond, extra=""):
    mark = "ok  " if cond else "FAIL"
    print(f"  {mark} {label}" + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(label)


def expect_error(label, fn, *args, **kwargs):
    """Assert-raising in einer direkt-laufbaren Form."""
    try:
        fn(*args, **kwargs)
    except wardrobe_panel.SettingsError as e:
        check(label, True)
        return str(e)
    except Exception as e:  # noqa: BLE001
        check(label, False, f"anderer Fehler: {e}")
        return ""
    check(label, False, "kein Fehler geworfen")
    return ""


# --- row_cost: Kostenformel je Modell ---------------------------------------


def test_row_cost_nano_banana_text_and_image():
    # 400 prompt ($0.0006) + 40 text-out ($0.0003) + 1120 image ($0.0336)
    c = wardrobe_panel.row_cost("gemini-nano-banana-2.1", 400, 40, 1120)
    exp = (400 * 1.5 + 40 * 7.5 + 1120 * 30) / 1e6
    check("row_cost nano-banana (text+image)", abs(c - exp) < 1e-12, f"{c} != {exp}")
    check("row_cost nano-banana ≈ 0.0345", round(c, 4) == 0.0345, f"{c}")


def test_row_cost_image_only():
    c = wardrobe_panel.row_cost("gemini-nano-banana-2.1", 0, 0, 3780)
    exp = 3780 * 30 / 1e6
    check("row_cost nur image_tokens (4K)", abs(c - exp) < 1e-12, f"{c}")


def test_row_cost_model_without_text_out_price():
    # gemini-3-pro-image: text_out=None — Text-Anteil zählt NICHT (nur text_in+bild)
    c = wardrobe_panel.row_cost("gemini-3-pro-image", 100, 999999, 1120)
    exp = (100 * 2.0 + 1120 * 120) / 1e6
    check("row_cost 3-pro-image ohne text_out-Preis", abs(c - exp) < 1e-12, f"{c}")


def test_row_cost_unknown_model_is_none():
    check("row_cost unbekanntes Modell → None",
          wardrobe_panel.row_cost("gpt-99-turbo", 10, 10, 10) is None)


def test_row_cost_tolerates_none_garbage_tokens():
    # None und Nicht-Zahlen zählen als 0 — Zeilen dürfen nie den Auftrag reißen.
    c = wardrobe_panel.row_cost("gemini-2.5-flash-image", None, None, 1290)
    exp = 1290 * 30 / 1e6
    check("row_cost None-Token-Split", abs(c - exp) < 1e-12, f"{c}")


def test_row_cost_empty():
    check("row_cost alles 0 → 0",
          wardrobe_panel.row_cost("gemini-nano-banana-2.1", 0, 0, 0) == 0.0)


# --- per_image: Schätztabelle je Bildgröße -----------------------------------


def test_per_image_estimates_match_spec():
    # Zahlen aus der Vorgabe (ai.google.dev, Stand Nov 2026)
    spec_21 = wardrobe_panel.image_model_spec("gemini-nano-banana-2.1")
    p = wardrobe_panel.per_image(spec_21)
    check("per_image nano-banana 1K ≈ 0.0336", abs(p["1K"] - 0.0336) < 1e-9, f"{p}")
    check("per_image nano-banana 2K ≈ 0.0504", abs(p["2K"] - 0.0504) < 1e-9, f"{p}")
    check("per_image nano-banana 4K ≈ 0.113", abs(p["4K"] - 0.113) < 1e-9, f"{p}")

    flash31 = wardrobe_panel.per_image(wardrobe_panel.image_model_spec("gemini-3.1-flash-image"))
    check("per_image 3.1-flash 0.5K ≈ 0.045", abs(flash31["0.5K"] - 0.0448) < 1e-9, f"{flash31}")
    check("per_image 3.1-flash 1K ≈ 0.067", abs(flash31["1K"] - 0.0672) < 1e-9, f"{flash31}")
    check("per_image 3.1-flash 2K ≈ 0.101", abs(flash31["2K"] - 0.1008) < 1e-9, f"{flash31}")

    lite = wardrobe_panel.per_image(wardrobe_panel.image_model_spec("gemini-3.1-flash-lite-image"))
    check("per_image 3.1-lite 1K ≈ 0.0336", abs(lite["1K"] - 0.0336) < 1e-9, f"{lite}")
    check("per_image 3.1-lite hat nur 1K", set(lite) == {"1K"}, f"{lite}")

    pro = wardrobe_panel.per_image(wardrobe_panel.image_model_spec("gemini-3-pro-image"))
    check("per_image 3-pro 2K ≈ 0.134", abs(pro["2K"] - 0.1344) < 1e-9, f"{pro}")
    check("per_image 3-pro 4K ≈ 0.24", abs(pro["4K"] - 0.24) < 1e-9, f"{pro}")

    f25 = wardrobe_panel.per_image(wardrobe_panel.image_model_spec("gemini-2.5-flash-image"))
    check("per_image 2.5-flash 1K ≈ 0.039", abs(f25["1K"] - 0.0387) < 1e-9, f"{f25}")


def test_image_models_payload_active_flag():
    rows = wardrobe_panel.image_models_payload("gemini-3.1-flash-image")
    active = [m for m in rows if m["active"]]
    check("image_models_payload: genau einer aktiv", len(active) == 1, f"{rows}")
    check("image_models_payload: aktives = gewähltes", active[0]["id"] == "gemini-3.1-flash-image")
    check("image_models_payload: per_image je Zeile",
          all(isinstance(m.get("per_image"), dict) for m in rows))


def test_text_models_payload_active_flag():
    rows = wardrobe_panel.text_models_payload("gpt-6-luna")
    active = [m for m in rows if m["active"]]
    check("text_models_payload: 4 Modelle", len(rows) == 4, f"{len(rows)}")
    check("text_models_payload: gpt-6-luna aktiv", active[0]["id"] == "gpt-6-luna")
    billing = {m["id"]: m["billing"] for m in rows}
    check("text_models billing go", billing["glm-5.3-flash"] == "go" and billing["gpt-6-luna"] == "go")
    check("text_models billing gemini",
          billing["gemini-flash-latest"] == "gemini" and billing["gemini-4-argon"] == "gemini")


# --- ampel: Schwellen ---------------------------------------------------------


def test_ampel_thresholds():
    ok = wardrobe_panel.ampel(5.0, 20.0)
    check("ampel <60% ok", ok["state"] == "ok" and ok["pct"] == 25.0, f"{ok}")
    warn = wardrobe_panel.ampel(12.0, 20.0)
    check("ampel 60% = genau warn", warn["state"] == "warn", f"{warn}")
    warn_hi = wardrobe_panel.ampel(18.0, 20.0)
    check("ampel 90% = genau warn", warn_hi["state"] == "warn", f"{warn_hi}")
    err = wardrobe_panel.ampel(19.0, 20.0)
    check("ampel >90% err", err["state"] == "err", f"{err}")
    neg = wardrobe_panel.ampel(25.0, 20.0)
    check("ampel negatives Restbudget err", neg["state"] == "err", f"{neg}")
    zero = wardrobe_panel.ampel(0.0, 20.0)
    check("ampel 0 → ok", zero["state"] == "ok", f"{zero}")
    check("ampel remaining", wardrobe_panel.ampel(7.0, 20.0)["remaining"] == 13.0)
    b0 = wardrobe_panel.ampel(0.0, 0.0)
    check("ampel budget 0 ohne spend ok", b0["state"] == "ok", f"{b0}")


# --- summarize: Monat/lifetime/by_model ---------------------------------------


def test_summarize_aggregates_known_cost():
    month = [{"model": "gemini-nano-banana-2.1", "count": 2, "prompt_tokens": 0,
              "text_output_tokens": 0, "image_tokens": 2240,
              }]
    life = [dict(month[0]),
            {"model": "gemini-3-pro-image", "count": 1, "prompt_tokens": 0,
             "text_output_tokens": 0, "image_tokens": 2000}]
    u = wardrobe_panel.summarize(month, life, budget=20.0)
    month_spent = 2240 * 30 / 1e6   # Token-SUMME je Modell → genau ein Total
    check("summarize month renders", u["month"]["renders"] == 2)
    check("summarize month spent", abs(u["month"]["spent"] - month_spent) < 1e-9,
          f"{u['month']['spent']} != {month_spent}")
    check("summarize lifetime renders", u["lifetime"]["renders"] == 3)
    check("summarize lifetime = month + pro-image", u["lifetime"]["renders"] == u["month"]["renders"] + 1)
    check("summarize lifetime spent > month", u["lifetime"]["spent"] > u["month"]["spent"])
    check("summarize avg vorhanden", u["lifetime"]["avg"] is not None)


def test_summarize_unknown_model_counts_but_skips_cost():
    month = [{"model": "unbekanntes-image", "count": 3, "prompt_tokens": 5,
              "text_output_tokens": 5, "image_tokens": 5}]
    life = [dict(month[0])]
    u = wardrobe_panel.summarize(month, life, budget=20.0)
    check("summarize unbekannt: renders gezählt", u["month"]["renders"] == 3)
    check("summarize unbekannt: spent 0", u["month"]["spent"] == 0.0)
    bm = u["month"]["by_model"][0]
    check("summarize unbekannt: spent null", bm["spent"] is None and bm["avg"] is None, f"{bm}")


def test_summarize_by_model_sorted_nulls_last():
    rows = [
        {"model": "gemini-nano-banana-2.1", "count": 1, "prompt_tokens": 0,
         "text_output_tokens": 0, "image_tokens": 1120},
        {"model": "unbekannt", "count": 9, "prompt_tokens": 0, "text_output_tokens": 0,
         "image_tokens": 0},
        {"model": "gemini-3-pro-image", "count": 1, "prompt_tokens": 0,
         "text_output_tokens": 0, "image_tokens": 2000},
    ]
    u = wardrobe_panel.summarize(rows, [], budget=20.0)
    ids = [m["id"] for m in u["month"]["by_model"]]
    check("by_model: teuerstes zuerst, null zuletzt", ids[-1] == "unbekannt" and ids[0] in
          ("gemini-3-pro-image", "gemini-nano-banana-2.1"), f"{ids}")


def test_summarize_empty():
    u = wardrobe_panel.summarize([], [], budget=20.0)
    check("summarize leer: month ok", u["month"]["spent"] == 0.0 and u["month"]["renders"] == 0)
    check("summarize leer: ampel ok", u["month"]["state"] == "ok", f"{u}")


# --- recent_view --------------------------------------------------------------


def test_recent_view_maps_rows():
    dt = datetime.datetime(2026, 11, 9, 12, 30, 0)
    rows = [
        {"created_at": dt, "outfit_id": "abcdef123456", "model": "gemini-nano-banana-2.1",
         "image_size": "2K", "total_tokens": 1680, "status": "ok", "error": None},
        {"created_at": None, "outfit_id": "", "model": "ein-unbekanntes-modell",
         "image_size": "2K", "total_tokens": 999, "status": "failed", "error": "kaputt"},
    ]
    out = wardrobe_panel.recent_view(rows)
    check("recent: iso-Zeit", out[0]["created_at"] == "2026-11-09T12:30:00", f"{out[0]}")
    check("recent: outfit gekürzt (8)", out[0]["outfit"] == "abcdef12", f"{out[0]}")
    check("recent: kosten bekannt", abs(out[0]["cost"] - 0.0504) < 1e-9, f"{out[0]}")
    check("recent: unbekanntes Modell → cost null", out[1]["cost"] is None)
    check("recent: error durchgereicht", out[1]["error"] == "kaputt")
    check("recent: leerer outfit_id → null", out[1]["outfit"] is None)


# --- defaults_merged + validate_settings --------------------------------------


def test_defaults_merged():
    s = wardrobe_panel.defaults_merged({})
    check("defaults: image_model", s["image_model"] == "gemini-nano-banana-2.1")
    check("defaults: image_size", s["image_size"] == "2K")
    check("defaults: text_model", s["text_model"] == "glm-5.3-flash")
    check("defaults: budget", s["budget_monthly"] == 20.0)


def test_defaults_merged_bad_budget_falls_back():
    for bad in ("brot", "", None):
        s = wardrobe_panel.defaults_merged({"budget_monthly": bad})
        check(f"defaults: budget '{bad}' → default", s["budget_monthly"] == 20.0, f"{s}")


def test_defaults_merged_partial():
    s = wardrobe_panel.defaults_merged({"image_model": "gemini-3-pro-image",
                                        "budget_monthly": "45.5"})
    check("defaults: partial übernommen", s["image_model"] == "gemini-3-pro-image"
          and s["budget_monthly"] == 45.5, f"{s}")
    check("defaults: fiel text_model default",
          s["text_model"] == "glm-5.3-flash")


def test_validate_settings_accepts_all_fields():
    out = wardrobe_panel.validate_settings({
        "image_model": "gemini-3.1-flash-image",
        "image_size": "1k",            # lowercase ok, normalize
        "budget_monthly": "37.25",     # string ok
        "text_model": "gemini-4-argon",
        "unbekanntes_feld": 1,
    })
    check("validate: alle Felder angenommen", out == {
        "image_model": "gemini-3.1-flash-image",
        "image_size": "1K",
        "budget_monthly": 37.25,
        "text_model": "gemini-4-argon",
    }, f"{out}")


def test_validate_settings_empty_and_none():
    check("validate: leeres payload", wardrobe_panel.validate_settings({}) == {})
    check("validate: None-Werte ignoriert",
          wardrobe_panel.validate_settings({"image_model": None, "text_model": None}) == {})


def test_validate_settings_errors():
    expect_error("image_model unbekannt",
                 wardrobe_panel.validate_settings, {"image_model": "megabob"})
    expect_error("text_model unbekannt",
                 wardrobe_panel.validate_settings, {"text_model": "kryptonite"})
    expect_error("image_size ungültig",
                 wardrobe_panel.validate_settings, {"image_size": "8K"})
    expect_error("budget keine Zahl",
                 wardrobe_panel.validate_settings, {"budget_monthly": "teuer"})
    expect_error("budget über 500",
                 wardrobe_panel.validate_settings, {"budget_monthly": 501})
    expect_error("budget negativ",
                 wardrobe_panel.validate_settings, {"budget_monthly": -1})


def test_validate_settings_budget_bounds_exactly():
    check("budget 0 erlaubt", wardrobe_panel.validate_settings({"budget_monthly": 0})
          == {"budget_monthly": 0.0})
    check("budget 500 erlaubt", wardrobe_panel.validate_settings({"budget_monthly": 500})
          == {"budget_monthly": 500.0})


# --- payload: Fake-DB-Pfad + DSN-fehlt-Fehlerpfad (beides offline) ------------


class FakeCursor:
    """Cursor mit Contents aus dem openBao-Look — SQL antwortet je Prefix."""

    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        if "FROM panel_settings" in sql:
            self._rows = [("image_model", "gemini-3-pro-image"),
                          ("budget_monthly", "50"),
                          ("text_model", "gpt-6-luna")]
        elif "GROUP BY model" in sql:
            if params:  # params vorhanden = WHERE → Monat
                self._rows = [("gemini-nano-banana-2.1", 2, 400, 80, 2240)]
            else:       # lifetime
                self._rows = [("gemini-nano-banana-2.1", 5, 400, 80, 5600)]
        elif "ORDER BY created_at" in sql:
            self._rows = [(datetime.datetime(2026, 11, 9, 10, 0, 0), "abcdef123456",
                           "gemini-nano-banana-2.1", "1K", 1120, "ok", None)]
        else:
            raise AssertionError(f"ungültiger Test-SQL: {sql[:60]}")

    def fetchall(self):
        return self._rows


class FakeConn:
    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        self.closed = True


def test_payload_with_fake_db():
    import wardrobe_panel as wp
    old_connect, old_cache = wp._connect, dict(wp.CACHE)
    try:
        wp._connect = lambda: (FakeConn(), None)
        wp.CACHE.update(ts=0.0, data=None)
        p = wp.payload(force=True)
        check("payload fake-db: ok true", p["db"] == {"ok": True}, f"{p['db']}")
        check("payload fake-db: settings gelesen",
              p["settings"]["image_model"] == "gemini-3-pro-image"
              and p["settings"]["budget_monthly"] == 50.0)
        check("payload fake-db: text_model aktive",
              [m for m in p["text_models"] if m["active"]][0]["id"] == "gpt-6-luna")
        check("payload fake-db: month 2 renders", p["usage"]["month"]["renders"] == 2)
        check("payload fake-db: lifetime 5 renders", p["usage"]["lifetime"]["renders"] == 5)
        mcost = (400 * 1.5 + 80 * 7.5 + 2240 * 30) / 1e6   # Token-SUMME = Modell-Total
        check("payload fake-db: month spent", abs(p["usage"]["month"]["spent"] - mcost) < 1e-9,
              f"{p['usage']['month']['spent']} != {mcost}")
        check("payload fake-db: by_model enthalten",
              any(m["id"] == "gemini-nano-banana-2.1" for m in p["usage"]["month"]["by_model"]))
        check("payload fake-db: recent 1 Zeile mit iso", len(p["recent"]) == 1
              and p["recent"][0]["created_at"] == "2026-11-09T10:00:00")
        check("payload fake-db: image_models aktiv",
              [m for m in p["image_models"] if m["active"]][0]["id"] == "gemini-3-pro-image")
    finally:
        wp._connect = old_connect
        wp.CACHE.update(ts=old_cache["ts"], data=old_cache["data"])


def test_payload_without_dsn_degrades_cleanly():
    import wardrobe_panel as wp
    old_connect, old_cache = wp._connect, dict(wp.CACHE)
    old_env = os.environ.pop("WARDROBE_DSN", None)
    try:
        wp._connect = wp.__dict__["_connect"]  # echte Funktion
        wp.CACHE.update(ts=0.0, data=None)
        p = wp.payload(force=True)
        check("payload ohne DSN: ok false", p["db"]["ok"] is False)
        check("payload ohne DSN: reason", "DSN" in p["db"]["reason"], f"{p['db']}")
        check("payload ohne DSN: settings defaults",
              p["settings"]["text_model"] == "glm-5.3-flash")
        check("payload ohne DSN: usage leer aber vollst. Form",
              p["usage"]["month"]["renders"] == 0 and "render" not in p["usage"])
    finally:
        wp._connect = old_connect
        wp.CACHE.update(ts=old_cache["ts"], data=old_cache["data"])
        if old_env is not None:
            os.environ["WARDROBE_DSN"] = old_env


def test_panel_state_cache_hit():
    import wardrobe_panel as wp
    old_cache = dict(wp.CACHE)
    calls = {"n": 0}
    try:
        def counting_connect():
            calls["n"] += 1
            return None, "test-cache"
        old_connect = wp._connect
        wp._connect = counting_connect
        wp.CACHE.update(ts=datetime.datetime.now().timestamp(), data={"settings": {}})
        wp.panel_state()  # Cache-Fenster → kein zweiter DB-Call
        check("panel_state: Cache getroffen", calls["n"] == 0, f"{calls}")
        wp.panel_state(force=True)
        check("panel_state: force → DB-Call", calls["n"] == 1, f"{calls}")
    finally:
        wp._connect = old_connect
        wp.CACHE.update(ts=old_cache["ts"], data=old_cache["data"])


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
        # Tests direkt laufen lassen — der (fixtur-)monkeypatch-Parameter
        # wird als No-Op-Fake bedient, alle Tests ohne monkeypatch bleiben
        # lauffähig.
        import inspect

        failed = 0
        for name, fn in sorted(globals().items()):
            if name.startswith("test_") and inspect.isfunction(fn):
                sig = inspect.signature(fn)
                try:
                    if any(p.name == "monkeypatch" for p in sig.parameters.values()):
                        print(f"{name} SKIPPED (pytest fehlt: monkeypatch)")
                        continue
                    fn()
                    print(f"{name} ok")
                except Exception as e:  # noqa: BLE001
                    failed += 1
                    print(f"{name} FAILED: {e}")
        if fails:  # check()-basierte Meldungen mitzählen (Direktlauf)
            print(f"{len(fails)} Prüfung(en) fehlgeschlagen")
            failed += 1
        sys.exit(1 if failed else 0)
