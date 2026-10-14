#!/usr/bin/env python3
"""Regressionstests für hestia/backend/benchmarks.py — komplett ohne Netz.

Abgedeckt: Normalisierung (_norm/_name_norm), Zuordnung (Alias → modelID →
Name → datumsbereinigter canonical_slug, Eindeutigkeit, `:`-Varianten),
Werte-Extraktion (artificial_analysis / design_arena codecategories),
AA-Zuordnung über slug bzw. creator/slug, Feld-Merge AA > OR, Ausfall einer
Quelle, Terminal-Bench-Fallbackkette, state()/fieldLabels, Cache-Roundtrip
inkl. Abwärtskompatibilität, Fault-Tolerance, Drossel und TTL.

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
                              "code_elo", "code_rank", "aa_id", "or_id", "source_id"}
        assert entry["code_rank"] == 7
        assert entry["source_id"] == "qwen/qwen3.8-max-0902"
        # code_rank wird automatisch mitgezogen, sobald code_elo gefordert ist.
        with patched(read_settings=lambda: _settings(fields=["code_elo"])):
            got = benchmarks.for_refs(["a/b"])
        assert set(got["a/b"]) == {"code_elo", "code_rank", "aa_id", "or_id", "source_id"}


# --- refresh: Fault-Tolerance, Drossel, TTL --------------------------------

def _refresh_env(rows=None, settings=None, fetch=None, aa_fetch=None,
                 aa_key="", models=None):
    rows = ROWS if rows is None else rows
    if fetch is None:
        def fetch():
            return rows
    if aa_fetch is None:
        def aa_fetch():
            raise AssertionError("AA-Abruf erwartet, aber AA_API_KEY war gesetzt")
    return patched(
        read_settings=lambda: (settings or _settings()),
        catalog_models=lambda: (models if models is not None else []),
        _fetch_or=fetch,
        _fetch_aa=aa_fetch,
        AA_API_KEY=aa_key,
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


# --- Artificial Analysis ----------------------------------------------------

AA_KEY = "test-aa-key"


def _aa_row(slug, name, creator="openai", evaluations=None, speed=None):
    return {
        "id": slug,
        "slug": slug,
        "name": name,
        "model_creator": {"id": creator, "name": creator.title(), "slug": creator},
        "evaluations": evaluations or {},
        "median_output_tokens_per_second": speed,
    }


def _aa_idx(rows=None):
    return benchmarks._aa_index(AA_ROWS if rows is None else rows)


AA_ROWS = [
    # Vollständiger Datensatz (Indizes 0..100, Anteile 0..1).
    _aa_row("gpt-6", "GPT-6", creator="openai",
            evaluations={
                "artificial_analysis_intelligence_index": 58.2,
                "artificial_analysis_coding_index": 71.0,
                "terminalbench_v4_0": 0.4123,
                "scicode": 0.3300,
                "tau2": 0.4567,
                "livecodebench": None,          # bei AA leer → Feld nicht genutzt
            },
            speed=143.26),
    # Nur Coding gefüllt (Rest leer) — genau der Fall für den Merge mit OR.
    _aa_row("claude-fable-5-1", "Claude Fable 5.1", creator="anthropic",
            evaluations={"artificial_analysis_coding_index": 63.5}, speed=88.0),
    # Terminal-Bench nur in der älteren Kette.
    _aa_row("qwen3-max", "Qwen3 Max", creator="qwen",
            evaluations={"terminalbench_v2_1": 0.25}),
    # Terminal-Bench nur in der ältesten Kette.
    _aa_row("qwen3-mini", "Qwen3 Mini", creator="qwen",
            evaluations={"terminalbench_hard": 0.125}),
    # Trägt gar keine verwertbaren Werte → kein Index-Eintrag.
    _aa_row("acme/leer", "Acme Leer", creator="acme", evaluations={}, speed=None),
]


def test_aa_entry_values_and_scaling():
    entry = benchmarks._aa_entry(AA_ROWS[0])
    assert entry["intelligence_index"] == 58.2      # Index bleibt 0..100
    assert entry["coding_index"] == 71.0
    assert entry["terminalbench"] == 41.2           # 0.4123 → 41.23 → 41.2
    assert entry["scicode"] == 33.0
    assert entry["tau2"] == 45.7
    assert entry["speed"] == 143.3                   # 1 Dezimale
    assert entry["aa_id"] == "gpt-6"
    # Felder, die AA nicht kennt, bleiben leer (kommen ggf. aus OR).
    assert entry["agentic_index"] is None
    assert entry["code_elo"] is None and entry["code_rank"] is None
    assert benchmarks._aa_entry(AA_ROWS[4]) is None


def test_aa_terminalbench_fallback_chain():
    # v4_0 hat Vorrang vor v2_1 …
    assert benchmarks._aa_entry(AA_ROWS[0])["terminalbench"] == 41.2
    # … v2_1 vor hard …
    assert benchmarks._aa_entry(AA_ROWS[2])["terminalbench"] == 25.0
    # … und hard ist der letzte Ausweg.
    assert benchmarks._aa_entry(AA_ROWS[3])["terminalbench"] == 12.5
    # Kette priorisiert innerhalb einer Zeile strikt.
    row = _aa_row("kette", "Kette", evaluations={"terminalbench_hard": 0.9,
                                                 "terminalbench_v2_1": 0.4,
                                                 "terminalbench_v4_0": 0.1})
    assert benchmarks._aa_entry(row)["terminalbench"] == 10.0


def test_aa_match_by_slug_and_creator_slug():
    idx = _aa_idx()
    # Über den modelID-Slug.
    hit = benchmarks._aa_match(_model("opencode/gpt-6", "gpt-6"), idx, {})
    assert hit and hit["aa_id"] == "gpt-6"
    assert hit["speed"] == 143.3
    # Über den Namen.
    hit = benchmarks._aa_match(_model("x/x", "gibtsnicht", "Claude Fable 5.1"), idx, {})
    assert hit and hit["aa_id"] == "claude-fable-5-1"
    # Über creator/slug, wenn der modelID nur den Namespace trägt …
    idx2 = benchmarks._aa_index(
        [_aa_row("gpt-6", "GPT-6", creator="openai",
                 evaluations={"artificial_analysis_coding_index": 71.0})])
    hit = benchmarks._aa_match(_model("x/gpt-6", "openai/gpt-6"), idx2, {})
    assert hit and hit["aa_id"] == "gpt-6"
    # … und über einen expliziten Alias (Slug oder creator/slug).
    hit = benchmarks._aa_match(_model("x/y", "nix"), idx,
                               {"x/y": "anthropic/claude-fable-5-1"})
    assert hit and hit["aa_id"] == "claude-fable-5-1"
    hit = benchmarks._aa_match(_model("x/y", "nix"), idx, {"x/y": "claude-fable-5-1"})
    assert hit and hit["aa_id"] == "claude-fable-5-1"
    # Kein Treffer → None.
    assert benchmarks._aa_match(_model("x/z", "gibtsnicht"), idx, {}) is None


def test_aa_best_candidate_has_most_filled_values():
    rows = [_aa_row("duo", "Duo", creator="acme",
                    evaluations={"artificial_analysis_coding_index": 10.0}),
            _aa_row("duo", "Duo", creator="acme",
                    evaluations={"artificial_analysis_coding_index": 20.0,
                                 "artificial_analysis_intelligence_index": 30.0,
                                 "scicode": 0.5})]
    hit = benchmarks._aa_match(_model("x/duo", "duo"), benchmarks._aa_index(rows), {})
    assert hit["intelligence_index"] == 30.0 and hit["scicode"] == 50.0


def test_merge_aa_wins_per_field():
    aa_hit = benchmarks._aa_entry(AA_ROWS[1])              # nur coding + speed
    or_hit = benchmarks._entry(ROWS[0], "qwen/qwen3.8-max-0902")
    merged = benchmarks._merge(aa_hit, or_hit)
    # AA hat coding_index/speed → AA gewinnt.
    assert merged["coding_index"] == 63.5
    assert merged["speed"] == 88.0
    # Wo AA leer ist, greift OR.
    assert merged["intelligence_index"] == 45.4
    assert merged["agentic_index"] == 56
    assert merged["code_elo"] == 1720 and merged["code_rank"] == 7
    assert merged["aa_id"] == "claude-fable-5-1"
    assert merged["or_id"] == "qwen/qwen3.8-max-0902"
    assert merged["source_id"] == "qwen/qwen3.8-max-0902"
    # Nur eine Quelle liefert nichts Verwertbares → kein Eintrag.
    assert benchmarks._merge(None, None) is None


# --- Zwei Quellen im refresh() ---------------------------------------------


def _two_source_models():
    return [_model("opencode/claude-fable-5-1", "claude-fable-5-1", "Claude Fable 5.1"),
            _model("opencode-go/qwen3.8-max", "qwen3.8-max")]


def test_refresh_merges_aa_and_or_offline():
    """Offline-Beleg: ein AA- und ein OR-Datensatz für dasselbe Modell werden
    pro Feld zusammengeführt — AA sichtbar, OR nur, wo AA leer ist."""
    or_rows = [_row("anthropic/claude-fable-5.1", "Claude Fable 5.1",
                    aa={"coding_index": 11.0, "intelligence_index": 22.0,
                        "agentic_index": 33},
                    arena=ARENA_OK)]
    with temp_cache():
        with _refresh_env(rows=or_rows, models=[_model(
                "opencode/claude-fable-5-1", "claude-fable-5.1", "Claude Fable 5.1")],
                fetch=lambda: or_rows, aa_fetch=lambda: [AA_ROWS[1]], aa_key=AA_KEY):
            res = benchmarks.refresh(force=True)
        assert res["refreshed"] is True, res
        cache = benchmarks.read_cache()
        entry = cache["entries"]["opencode/claude-fable-5-1"]
        assert entry["aa_id"] == "claude-fable-5-1"
        assert entry["or_id"] == "anthropic/claude-fable-5.1"
        # AA-Wert sichtbar, OR-Felder nur dort, wo AA leer ist.
        assert entry["coding_index"] == 63.5          # von AA
        assert entry["speed"] == 88.0                 # von AA
        assert entry["agentic_index"] == 33           # nur OR kennt das
        assert entry["code_elo"] == 1720              # nur OR (design_arena)
        assert cache["sources"]["aa"]["ok"] is True and cache["sources"]["aa"]["count"] == 1
        assert cache["sources"]["or"]["ok"] is True


def test_refresh_without_aa_key_uses_or_only():
    models = [_model("opencode-go/qwen3.8-max", "qwen3.8-max")]
    with temp_cache():
        with _refresh_env(models=models):
            res = benchmarks.refresh(force=True)
        assert res["refreshed"] is True, res
        cache = benchmarks.read_cache()
    assert cache["sources"]["aa"]["ok"] is False
    assert cache["sources"]["or"]["ok"] is True
    assert cache["entries"]["opencode-go/qwen3.8-max"]["aa_id"] is None
    assert cache["entries"]["opencode-go/qwen3.8-max"]["or_id"] == "qwen/qwen3.8-max-0902"


def test_aa_error_falls_back_to_or():
    models = [_model("opencode-go/qwen3.8-max", "qwen3.8-max")]

    def boom():
        raise RuntimeError("HTTP 403")

    with temp_cache() as path:
        with _refresh_env(models=models, aa_fetch=boom, aa_key=AA_KEY):
            res = benchmarks.refresh(force=True)
        assert res["refreshed"] is True, res
        cache = benchmarks.read_cache()
        assert cache["sources"]["aa"]["ok"] is False
        assert "403" in cache["sources"]["aa"]["error"]
        assert cache["sources"]["or"]["ok"] is True
        assert cache["entries"]["opencode-go/qwen3.8-max"]["or_id"] == "qwen/qwen3.8-max-0902"
        assert os.path.exists(path)


def test_or_error_falls_back_to_aa():
    models = [_model("opencode/claude-fable-5-1", "claude-fable-5-1", "Claude Fable 5.1")]

    def boom():
        raise RuntimeError("HTTP 500")

    with temp_cache():
        with _refresh_env(fetch=boom, aa_fetch=lambda: AA_ROWS, aa_key=AA_KEY,
                          models=models):
            res = benchmarks.refresh(force=True)
        assert res["refreshed"] is True, res
        cache = benchmarks.read_cache()
    assert cache["sources"]["or"]["ok"] is False
    assert cache["sources"]["aa"]["ok"] is True
    entry = cache["entries"]["opencode/claude-fable-5-1"]
    assert entry["aa_id"] == "claude-fable-5-1" and entry["or_id"] is None
    assert entry["coding_index"] == 63.5 and entry["speed"] == 88.0
    assert entry["code_elo"] is None and entry["agentic_index"] is None


def test_both_sources_failed_keeps_old_cache():
    models = [_model("opencode-go/qwen3.8-max", "qwen3.8-max")]

    def boom():
        raise RuntimeError("HTTP 503")

    with temp_cache() as path:
        with _refresh_env(models=models):
            benchmarks.refresh(force=True)
        good = benchmarks.read_cache()
        assert good["entries"]

        with _refresh_env(models=models, fetch=boom, aa_fetch=boom, aa_key=AA_KEY):
            res = benchmarks.refresh(force=True)
        assert res["refreshed"] is False and res["reason"] == "error"
        assert "503" in (res["error"] or "")
        assert res.get("cacheKept") is True
        assert benchmarks.read_cache()["entries"] == good["entries"]
        assert os.path.exists(path)


# --- state() / fieldLabels --------------------------------------------------


def test_state_exposes_labels_and_sources():
    fields = ["intelligence_index", "coding_index", "terminalbench", "speed",
              "agentic_index", "code_elo", "scicode", "tau2"]
    with temp_cache():
        with _refresh_env(models=[_model("opencode-go/qwen3.8-max", "qwen3.8-max")],
                          aa_fetch=lambda: AA_ROWS, aa_key=AA_KEY,
                          settings=_settings(fields=fields)):
            benchmarks.refresh(force=True)
            st = benchmarks.state()
    assert st["aaEnabled"] is True
    assert st["attribution"] == "artificialanalysis.ai / OpenRouter"
    assert st["sources"]["aa"]["ok"] is True
    assert st["sources"]["aa"]["count"] == len(AA_ROWS)
    assert st["sources"]["or"]["ok"] is True
    labels = st["fieldLabels"]
    assert labels["terminalbench"] == "Terminal-Bench"
    assert labels["speed"] == "Tok/s"
    assert labels["agentic_index"] == "Agentic-Index"
    assert labels["code_elo"] == "Code-Elo"
    assert labels["intelligence_index"] == "Intelligenz"
    assert labels["coding_index"] == "Coding"
    assert labels["scicode"] == "SciCode"
    assert labels["tau2"] == "τ²-bench"
    assert isinstance(st["fields"], list) and st["fields"]
    for key in ("enabled", "count", "missing", "fetchedAt", "ageDays",
                "refreshDays", "lastError"):
        assert key in st, key


def test_state_without_aa_key():
    with temp_cache():
        with patched(AA_API_KEY="", catalog_models=lambda: []):
            st = benchmarks.state()
    assert st["aaEnabled"] is False
    assert st["sources"]["aa"] == {"ok": False, "ts": None, "count": 0}


def test_old_cache_without_sources_is_readable():
    """Abwärtskompatibilität: Cache vor dem AA-Update (kein `sources`)."""
    with temp_cache():
        benchmarks.write_cache({
            "fetchedAt": 1700000000,
            "source": "openrouter",
            "count": 1,
            "entries": {"a/b": {"coding_index": 76.2, "agentic_index": 56.0,
                                "source_id": "qwen/qwen3.8-max-0902"}},
        })
        st = benchmarks.state()
    assert st["sources"]["or"]["ok"] is True
    assert st["sources"]["or"]["count"] == 1
    assert st["sources"]["aa"]["ok"] is False and st["sources"]["aa"]["count"] == 0
    assert st["attribution"] == "artificialanalysis.ai / OpenRouter"


class _GhStub:
    """Minimaler github_api-Ersatz: liefert eine feste SOT (kein Netz)."""

    def __init__(self, raw):
        self._raw = raw

    def get_file(self, path, *a, **kw):
        return self._raw


def test_sot_fields_are_valid():
    """Die SOT darf nur bekannte Felder nennen (read_settings filtert das)."""
    import json as _json
    import json as _json
    raw = _json.dumps({"fields": ["coding_index", "quatsch", 7], "refreshDays": 3})
    with patched(github_api=_GhStub(raw)):
        s = benchmarks.read_settings()
    assert s["fields"] == ["coding_index"]   # unbekannte/falsche Typen fliegen raus
    assert s["refreshDays"] == 3
    assert benchmarks.field_labels(["terminalbench", "speed", "code_elo"]) == {
        "terminalbench": "Terminal-Bench", "speed": "Tok/s", "code_elo": "Code-Elo"}
    for f in benchmarks.DEFAULT_FIELDS:
        assert f in benchmarks.ALL_FIELDS, f
        assert f in benchmarks.FIELD_LABELS, f


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
