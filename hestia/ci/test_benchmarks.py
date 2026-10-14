#!/usr/bin/env python3
"""Regressionstests für hestia/backend/benchmarks.py — komplett ohne Netz.

Abgedeckt: Normalisierung (_norm/_name_norm), Zuordnung (Alias → modelID →
Name → datumsbereinigter canonical_slug, Eindeutigkeit, `:`-Varianten),
Werte-Extraktion (artificial_analysis / design_arena codecategories),
Cache-Roundtrip, Fault-Tolerance, Drossel und TTL.

Läuft mit pytest UND direkt:
    pytest -q hestia/ci/test_benchmarks.py
    python3 hestia/ci/test_benchmarks.py
"""
import contextlib
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

import benchmarks  # noqa: E402

# --- Testdaten -------------------------------------------------------------

AA = {"intelligence_index": 45.4, "coding_index": 76.2, "agentic_index": 56}
AA_PRIME = {"intelligence_index": 51.0, "coding_index": 80.5, "agentic_index": 60}

ARENA_OK = [
    {"arena": "arena", "category": "overall", "elo": 1500, "rank": 1},
    {"arena": "arena", "category": "codecategories", "elo": 1720, "rank": 7},
    {"arena": "arena", "category": "codecategories", "elo": 1690, "rank": 9},
]
ARENA_WRONG_CATEGORY = [
    {"arena": "arena", "category": "mathcategories", "elo": 1800, "rank": 2},
]


def _row(rid, name, slug=None, aa=None, arena=None, with_bench=True):
    row = {"id": rid, "name": name}
    if slug:
        row["canonical_slug"] = slug
    if with_bench:
        row["benchmarks"] = {
            "artificial_analysis": aa or {},
            "design_arena": arena if arena is not None else [],
        }
    return row


ROWS = [
    # Gepinnter Snapshot — genau das Problem aus dem Dispatch.
    _row("qwen/qwen3.8-max-0902", "Qwen3.8 Max (0902)",
         slug="qwen/qwen3.8-max-20260902", aa=AA, arena=ARENA_OK),
    # Anderes Modell, ähnlicher Name — darf NICHT fälschlich treffen.
    _row("qwen/qwen3.8-max-prime", "Qwen3.8 Max Prime",
         slug="qwen/qwen3.8-max-prime-20260923", aa=AA_PRIME),
    _row("openai/gpt-6", "GPT-6 (Preview)", aa=AA, arena=ARENA_OK),
    _row("acme/widget-free", "Acme Widget", aa=AA),
    _row("acme/gadget:batch", "Acme Gadget Batch", aa=AA),
    _row("acme/gadget", "Acme Gadget", aa=AA),
    # Snapshot mit Bindestrich-Datum …
    _row("vendor/thing-1", "Vendor Thing One",
         slug="vendor/thing-2026-09-02", aa=AA),
    # … und zwei Snapshots, die nach dem Bereinigen auf denselben Slug fallen
    # (mehrdeutig → darf keinen Treffer liefern).
    _row("vendor/duo-0301", "Vendor Duo 0301", slug="vendor/duo-20260301", aa=AA),
    _row("vendor/duo-0620", "Vendor Duo 0620", slug="vendor/duo-20260620", aa=AA),
    # Trägt keine Benchmarks → kein Index-Eintrag.
    _row("acme/empty", "Acme Empty", aa=None, with_bench=False),
    _row("acme/nulls", "Acme Nulls", aa={}),
]


def _settings(**over):
    s = {
        "enabled": True,
        "refreshDays": 30,
        "fields": ["coding_index", "agentic_index", "intelligence_index", "code_elo"],
        "aliases": {},
    }
    s.update(over)
    return s


@contextlib.contextmanager
def patched(**attrs):
    """Modulattribute temporär überschreiben, danach zurücksetzen."""
    old = {k: getattr(benchmarks, k) for k in attrs}
    for k, v in attrs.items():
        setattr(benchmarks, k, v)
    try:
        yield
    finally:
        for k, v in old.items():
            setattr(benchmarks, k, v)


@contextlib.contextmanager
def temp_cache():
    """Eigene Cache-Datei (kein /app/data nötig) + zurückgesetzter STATE."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "model-benchmarks.json")
        with patched(CACHE_PATH=path):
            with patched(STATE={"lastError": None, "lastAttempt": None, "lastRefresh": None}):
                yield path


def _idx(rows=None):
    return benchmarks._index(ROWS if rows is None else rows)


def _model(ref, modelID=None, name=None):
    m = {"ref": ref}
    if modelID is not None:
        m["modelID"] = modelID
    if name is not None:
        m["name"] = name
    return m


# --- Normalisierung --------------------------------------------------------


def test_norm_and_name_norm():
    assert benchmarks._norm("Qwen3.8-Max") == "qwen38max"
    assert benchmarks._norm("Foo-Bar-Free") == "foobar"
    assert benchmarks._norm("  Qwen3.8 Max  ") == "qwen38max"
    assert benchmarks._norm("") == ""
    assert benchmarks._norm(None) == ""
    assert benchmarks._norm("GPT-4o (mini)") == "gpt4omini"
    assert benchmarks._norm("a_b/c:d!") == "abcd"
    assert benchmarks._name_norm("GPT-6 (Preview)") == "gpt6"
    assert benchmarks._name_norm("Claude 3.5 Sonnet") == "claude35sonnet"
    # Klammerzusätze fliegen komplett raus (auch der Datumsteil).
    assert benchmarks._name_norm("Qwen3.8 Max (0902)") == "qwen38max"
    assert benchmarks._name_norm("Qwen3.8 Max 0902") == "qwen38max0902"


def test_slug_no_date():
    assert benchmarks._slug_no_date("qwen/qwen3.8-max-20260902") == "qwen/qwen3.8-max"
    assert benchmarks._slug_no_date("vendor/thing-2026-09-02") == "vendor/thing"
    assert benchmarks._slug_no_date("qwen/qwen3.8-max") == "qwen/qwen3.8-max"
    assert benchmarks._slug_no_date("acme/widget-0902") == "acme/widget-0902"


# --- Zuordnung -------------------------------------------------------------


def test_alias_wins():
    aliases = {"opencode-go/qwen3.8-max": "qwen/qwen3.8-max-0902"}
    hit = benchmarks._match(_model("opencode-go/qwen3.8-max", "qwen3.8-max",
                                   "Qwen3.8 Max"), _idx(), aliases)
    assert hit and hit["source_id"] == "qwen/qwen3.8-max-0902"
    # Alias schlägt auch einen ID-Treffer.
    aliases = {"opencode-go/gpt-6": "qwen/qwen3.8-max-0902"}
    hit = benchmarks._match(_model("opencode-go/gpt-6", "gpt-6"), _idx(), aliases)
    assert hit and hit["source_id"] == "qwen/qwen3.8-max-0902"


def test_match_model_id_and_free_suffix():
    hit = benchmarks._match(_model("x/gpt-6", "gpt-6"), _idx(), {})
    assert hit and hit["source_id"] == "openai/gpt-6"
    # "-free" wird vom Suffix-Abbau in _norm geschluckt → trifft das Basismodell.
    hit = benchmarks._match(_model("x/widget", "widget-free"), _idx(), {})
    assert hit and hit["source_id"] == "acme/widget-free"


def test_match_name_fallback():
    hit = benchmarks._match(_model("x/gpt-6", "irgendwas/anders", "GPT-6 (Preview)"),
                            _idx(), {})
    assert hit and hit["source_id"] == "openai/gpt-6"


def test_match_canonical_slug_date_stripped():
    idx = _idx()
    for ref in ("opencode-go/qwen3.8-max", "opencode/qwen3.8-max"):
        hit = benchmarks._match(_model(ref, "qwen3.8-max"), idx, {})
        assert hit, ref
        assert hit["source_id"] == "qwen/qwen3.8-max-0902", hit["source_id"]
        assert hit["coding_index"] == 76.2
    # Auch das Bindestrich-Datum wird erkannt.
    hit = benchmarks._match(_model("x/thing", "thing"), idx, {})
    assert hit and hit["source_id"] == "vendor/thing-1"


def test_prime_does_not_match_max():
    idx = _idx()
    prime = benchmarks._match(_model("opencode/qwen3.8-max-prime", "qwen3.8-max-prime"),
                              idx, {})
    assert prime and prime["source_id"] == "qwen/qwen3.8-max-prime"
    assert prime["coding_index"] == 80.5
    # Der Max-Treffer darf nicht auf das Prime-Modell durchschlagen.
    mx = benchmarks._match(_model("opencode/qwen3.8-max", "qwen3.8-max"), idx, {})
    assert mx and mx["source_id"] == "qwen/qwen3.8-max-0902"
    assert mx["coding_index"] != prime["coding_index"]


def test_ambiguous_canonical_slug_has_no_hit():
    # "vendor/duo-0301" und "vendor/duo-0620" → beide Slug-Tail "duo".
    assert benchmarks._match(_model("x/duo", "duo"), _idx(), {}) is None
    # Eindeutig, aber nur beim canonical_slug? Dann gibt es keinen Treffer.
    rows = [r for r in ROWS if r["id"] != "vendor/duo-0620"]
    hit = benchmarks._match(_model("x/duo", "duo"), _idx(rows), {})
    assert hit and hit["source_id"] == "vendor/duo-0301"


def test_colon_variant_preferred():
    hit = benchmarks._match(_model("x/gadget", "gadget"), _idx(), {})
    assert hit and hit["source_id"] == "acme/gadget"


def test_no_benchmarks_no_hit():
    assert benchmarks._match(_model("x/empty", "empty"), _idx(), {}) is None
    assert benchmarks._match(_model("x/nulls", "nulls"), _idx(), {}) is None


# --- Werte-Extraktion ------------------------------------------------------


def test_entry_values():
    entry = benchmarks._entry(ROWS[0], "qwen/qwen3.8-max-0902")
    assert entry["coding_index"] == 76.2
    assert entry["agentic_index"] == 56
    assert entry["intelligence_index"] == 45.4
    # Höchster codecategories-ELO (1720) samt Rank 7.
    assert entry["code_elo"] == 1720
    assert entry["code_rank"] == 7
    assert entry["source_id"] == "qwen/qwen3.8-max-0902"


def test_entry_ignores_other_categories():
    row = _row("acme/onlymath", "Acme OnlyMath", aa=AA, arena=ARENA_WRONG_CATEGORY)
    entry = benchmarks._entry(row, "acme/onlymath")
    assert entry["code_elo"] is None and entry["code_rank"] is None
    assert entry["coding_index"] == 76.2


def test_entry_all_null_is_dropped():
    assert benchmarks._entry(ROWS[10], "acme/nulls") is None       # leere AA
    assert benchmarks._entry(ROWS[9], "acme/empty") is None        # kein benchmarks
    row = _row("acme/blank", "Acme Blank",
               aa={"intelligence_index": None, "coding_index": None}, arena=[])
    assert benchmarks._entry(row, "acme/blank") is None


# --- Cache -----------------------------------------------------------------


def test_cache_roundtrip():
    with temp_cache() as path:
        payload = {"fetchedAt": 123, "source": "openrouter", "count": 1,
                   "entries": {"a/b": {"coding_index": 1.0, "source_id": "x/y"}}}
        assert benchmarks.write_cache(payload) is True
        assert os.path.exists(path)
        back = benchmarks.read_cache()
        assert back["fetchedAt"] == 123
        assert back["entries"]["a/b"]["source_id"] == "x/y"
        # Beschädigte Cache → leere Struktur, kein Crash.
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{kapott")
        assert not benchmarks.read_cache().get("entries")


def test_for_refs_only_configured_fields():
    with temp_cache():
        benchmarks.write_cache({
            "fetchedAt": int(time.time()),
            "entries": {"a/b": {"coding_index": 76.2, "agentic_index": 56.0,
                                "intelligence_index": 45.4, "code_elo": 1720,
                                "code_rank": 7, "source_id": "qwen/qwen3.8-max-0902"}},
        })
        with patched(read_settings=lambda: _settings()):
            got = benchmarks.for_refs(["a/b", "gibtsnicht"])
        assert set(got) == {"a/b"}
        entry = got["a/b"]
        assert set(entry) == {"coding_index", "agentic_index", "intelligence_index",
                              "code_elo", "code_rank", "source_id"}
        assert entry["code_rank"] == 7
        assert entry["source_id"] == "qwen/qwen3.8-max-0902"
        # code_rank wird automatisch mitgezogen, sobald code_elo gefordert ist.
        with patched(read_settings=lambda: _settings(fields=["code_elo"])):
            got = benchmarks.for_refs(["a/b"])
        assert set(got["a/b"]) == {"code_elo", "code_rank", "source_id"}


# --- refresh: Fault-Tolerance, Drossel, TTL --------------------------------

def _refresh_env(rows=None, settings=None, fetch=None, models=None):
    rows = ROWS if rows is None else rows
    if fetch is None:
        def fetch():
            return rows
    return patched(
        read_settings=lambda: (settings or _settings()),
        catalog_models=lambda: (models if models is not None else []),
        _fetch=fetch,
    )


def test_refresh_maps_canonical_slug():
    models = [_model("opencode-go/qwen3.8-max", "qwen3.8-max"),
              _model("opencode/qwen3.8-max", "qwen3.8-max"),
              _model("opencode/qwen3.8-max-prime", "qwen3.8-max-prime")]
    with temp_cache():
        with _refresh_env(models=models):
            res = benchmarks.refresh(force=True)
            assert res["refreshed"] is True, res
            entries = benchmarks.read_cache()["entries"]
        assert entries["opencode-go/qwen3.8-max"]["source_id"] == "qwen/qwen3.8-max-0902"
        assert entries["opencode/qwen3.8-max"]["source_id"] == "qwen/qwen3.8-max-0902"
        assert entries["opencode/qwen3.8-max-prime"]["source_id"] == \
            "qwen/qwen3.8-max-prime"


def test_fetch_error_keeps_old_cache():
    models = [_model("opencode-go/qwen3.8-max", "qwen3.8-max")]
    with temp_cache() as path:
        with _refresh_env(models=models):
            benchmarks.refresh(force=True)
        good = benchmarks.read_cache()
        assert good["entries"]

        def boom():
            raise RuntimeError("HTTP 503")

        with _refresh_env(models=models, fetch=boom):
            res = benchmarks.refresh(force=True)
        assert res["refreshed"] is False and res["reason"] == "error"
        assert "503" in (res["error"] or "")
        assert res.get("cacheKept") is True
        assert benchmarks.STATE["lastError"]
        # Alter Cache unangetastet.
        assert benchmarks.read_cache()["entries"] == good["entries"]
        assert os.path.exists(path)


def test_throttle_no_second_fetch():
    calls = []

    def counting_fetch():
        calls.append(1)
        return ROWS

    models = [_model("x/unknown", "gibtsnicht")]
    with temp_cache():
        with _refresh_env(models=models, fetch=counting_fetch):
            benchmarks.refresh(force=True)
            assert len(calls) == 1
            # Ref weiterhin ohne Eintrag → zweiter Versuch wird gedrosselt (6 h).
            res = benchmarks.refresh()
            assert res["refreshed"] is False and res["reason"] == "throttled"
            assert len(calls) == 1


def test_ttl_fresh_vs_stale():
    calls = []

    def counting_fetch():
        calls.append(1)
        return ROWS

    models = [_model("opencode-go/qwen3.8-max", "qwen3.8-max")]

    def with_fetched_at(ts):
        with temp_cache():
            benchmarks.write_cache({"fetchedAt": ts, "entries":
                                    {"opencode-go/qwen3.8-max": {
                                        "coding_index": 1.0,
                                        "source_id": "alt"}}})
            with _refresh_env(models=models, fetch=counting_fetch):
                return benchmarks.refresh()

    now = int(time.time())
    # Frisch (1 Tag) + alle Refs belegt → kein Fetch.
    res = with_fetched_at(now - 86400)
    assert res["refreshed"] is False and res["reason"] == "fresh", res
    assert len(calls) == 0
    # Älter als refreshDays → Fetch.
    res = with_fetched_at(now - 31 * 86400)
    assert res["refreshed"] is True and res["reason"] == "stale", res
    assert len(calls) == 1
    # Frisch, aber force → Fetch.
    res = with_fetched_at(now - 86400)
    assert res["refreshed"] is False
    with temp_cache() as path:
        benchmarks.write_cache({"fetchedAt": now - 86400, "entries":
                                {"opencode-go/qwen3.8-max": {"coding_index": 1.0,
                                                             "source_id": "alt"}}})
        with _refresh_env(models=models, fetch=counting_fetch):
            res = benchmarks.refresh(force=True)
        assert res["refreshed"] is True and res["reason"] == "forced", res
        assert len(calls) == 2
        assert benchmarks.read_cache()["entries"]["opencode-go/qwen3.8-max"][
            "source_id"] == "qwen/qwen3.8-max-0902"


# --- Standalone-Runner -----------------------------------------------------

def main() -> int:
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    fails = []
    for name, fn in tests:
        try:
            fn()
            print(f"  ok   {name}")
        except Exception as e:  # noqa: BLE001
            fails.append(name)
            print(f"  FAIL {name}: {type(e).__name__}: {e}")
    print()
    print(f"{len(tests) - len(fails)}/{len(tests)} Tests grün"
          + (f" — FEHLGESCHLAGEN: {fails}" if fails else ""))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
