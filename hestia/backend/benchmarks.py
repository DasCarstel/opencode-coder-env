#!/usr/bin/env python3
"""benchmarks.py — Modell-Benchmarks aus dem öffentlichen OpenRouter-Katalog.

Quelle: GET https://openrouter.ai/api/v1/models (kein API-Key, kein Limit).
Jedes Modell kann mit `benchmarks` versehen sein:
    artificial_analysis: {intelligence_index, coding_index, agentic_index, …}
    design_arena:       [{arena, category, elo, win_rate, rank}, …]
                        → relevant ist der Eintrag mit category == "codecategories"

Parameter (enabled/refreshDays/fields/aliases) liegen in der SOT
.opencode/hestia-benchmarks.jsonc (HESTIA_BENCHMARKS_PATH), fehlt sie, gelten
die Env-Defaults. Der Abruf selbst wird lokal gecacht — der Cache liegt NICHT im
Repo, sondern unter HESTIA_BENCHMARKS_CACHE (Default /app/data/model-benchmarks.json,
Bind-Mount /etc/hestia wie der Suggest-State).

Zuordnung unserer Katalog-Ref auf eine OpenRouter-ID, in dieser Priorität:
  1. expliziter Alias (aliases[ref] → exakte OpenRouter-ID)
  2. normalisierte modelID gegen den Teil hinter "/" der OpenRouter-ID
  3. normalisierter Name gegen den OpenRouter-Namen (ohne Klammerzusätze)
Nicht zuordenbare Modelle werden weggelassen (das Panel zeigt dann "—").
Werte können null sein — null heißt "keine Daten von der Quelle", nicht 0.
"""
import json
import os
import re
import threading
import time
import urllib.request

import catalog
import github_api

SETTINGS_PATH = os.environ.get(
    "HESTIA_BENCHMARKS_PATH", ".opencode/hestia-benchmarks.jsonc"
)
CACHE_PATH = os.environ.get(
    "HESTIA_BENCHMARKS_CACHE", "/app/data/model-benchmarks.json"
)
SOURCE_URL = os.environ.get(
    "HESTIA_BENCHMARKS_URL", "https://openrouter.ai/api/v1/models"
)
SOURCE = "openrouter"
FETCH_TIMEOUT = int(os.environ.get("HESTIA_BENCHMARKS_TIMEOUT", "25"))
# Mindestabstand zwischen zwei Versuchen, die nur wegen fehlender Zuordnung
# ausgelöst werden (verhindert Abruf-Hämmern bei dauerhaft unzuordenbaren Refs).
MISSING_RETRY_THROTTLE = float(
    os.environ.get("HESTIA_BENCHMARKS_RETRY_THROTTLE", str(6 * 3600))
)

DEFAULT_ENABLED = os.environ.get("HESTIA_BENCHMARKS", "1") != "0"
DEFAULT_REFRESH_DAYS = int(os.environ.get("HESTIA_BENCHMARKS_REFRESH_DAYS", "30"))
DEFAULT_FIELDS = ["coding_index", "agentic_index", "intelligence_index", "code_elo"]

# Metrik-Felder (nie weggelassen) und Anzeige-/Prompt-Felder (SOT `fields`).
METRIC_FIELDS = ("coding_index", "agentic_index", "intelligence_index")
EXTRA_FIELDS = ("code_elo", "code_rank")
ALL_FIELDS = METRIC_FIELDS + EXTRA_FIELDS

LOCK = threading.Lock()
STATE = {
    "lastError": None,
    "lastAttempt": None,
    "lastRefresh": None,
}


def _log(msg: str):
    print(f"[hestia-benchmarks] {time.strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)


def default_settings() -> dict:
    """Startwerte = Env-Defaults (Fallback, falls die SOT fehlt/kaputt ist)."""
    return {
        "enabled": bool(DEFAULT_ENABLED),
        "refreshDays": DEFAULT_REFRESH_DAYS,
        "fields": list(DEFAULT_FIELDS),
        "aliases": {},
    }


def read_settings() -> dict:
    """SOT lesen und normalisieren (Muster failover.read_settings)."""
    data = {}
    try:
        raw = github_api.get_file(SETTINGS_PATH)
        if raw:
            import hjsonc
            data = hjsonc.parse(raw) or {}
    except Exception as e:  # noqa: BLE001
        _log(f"settings: {SETTINGS_PATH} nicht lesbar ({e}) — Defaults")
        data = {}
    if not isinstance(data, dict):
        data = {}
    d = default_settings()

    enabled = d["enabled"]
    if isinstance(data.get("enabled"), bool):
        enabled = data["enabled"]

    try:
        refresh_days = int(data.get("refreshDays"))
    except (TypeError, ValueError):
        refresh_days = d["refreshDays"]
    if not (1 <= refresh_days <= 365):
        refresh_days = d["refreshDays"]

    fields = data.get("fields")
    if not isinstance(fields, list):
        fields = d["fields"]
    fields = [f for f in fields if isinstance(f, str) and f in ALL_FIELDS]
    if not fields:
        fields = list(d["fields"])

    aliases = data.get("aliases")
    if not isinstance(aliases, dict):
        aliases = {}
    aliases = {
        str(k).strip(): str(v).strip()
        for k, v in aliases.items()
        if str(k).strip() and str(v).strip()
    }
    return {
        "enabled": enabled,
        "refreshDays": refresh_days,
        "fields": fields,
        "aliases": aliases,
    }


def read_cache() -> dict:
    """Cache lesen (fehlend/kaputt → leere Struktur, kein Crash)."""
    try:
        with open(CACHE_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}
    except Exception as e:  # noqa: BLE001
        _log(f"cache: {CACHE_PATH} nicht lesbar ({e}) — starte ohne Cache")
        return {}
    if not isinstance(data, dict):
        return {}
    if not isinstance(data.get("entries"), dict):
        data["entries"] = {}
    return data


def write_cache(data: dict) -> bool:
    """Cache atomar schreiben (.tmp + os.replace). Fehler degradieren."""
    try:
        os.makedirs(os.path.dirname(CACHE_PATH) or ".", exist_ok=True)
        tmp = CACHE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        os.replace(tmp, CACHE_PATH)
        return True
    except Exception as e:  # noqa: BLE001
        _log(f"cache: {CACHE_PATH} nicht schreibbar ({e})")
        return False


def _norm(s) -> str:
    """lower, `-free`-Suffix entfernen, alles Nicht-Alphanumerische weg."""
    text = str(s or "").strip().lower()
    text = re.sub(r"-free$", "", text)
    return re.sub(r"[^a-z0-9]+", "", text)


def _name_norm(s) -> str:
    """Wie _norm, zusätzlich ohne Klammerzusätze ("GPT-6 (Preview)" → "gpt6")."""
    text = re.sub(r"\([^)]*\)", " ", str(s or ""))
    return _norm(text)


def _base_id(rid: str) -> str:
    """OpenRouter-ID ohne Variantensuffix ("…:free" → "…")."""
    return str(rid or "").split(":", 1)[0].strip()


def _entry(row: dict, rid: str) -> dict | None:
    """Einen OpenRouter-Datensatz auf unsere Felder eindampfen.

    None, wenn das Modell gar keine Benchmarks trägt oder alle Werte null sind
    (dann ist es fachlich nicht zuordenbar → Eintrag weglassen, UI zeigt "—").
    """
    bench = row.get("benchmarks")
    if not isinstance(bench, dict):
        return None
    aa = bench.get("artificial_analysis")
    aa = aa if isinstance(aa, dict) else {}
    out = {}
    for field in METRIC_FIELDS:
        val = aa.get(field)
        out[field] = val if isinstance(val, (int, float)) else None

    elo = rank = None
    best = None
    for item in (bench.get("design_arena") or []):
        if not isinstance(item, dict) or item.get("category") != "codecategories":
            continue
        val = item.get("elo")
        if not isinstance(val, (int, float)):
            continue
        if best is None or val > best:
            best = val
            rank = item.get("rank")
    out["code_elo"] = best
    out["code_rank"] = rank if isinstance(rank, int) else None
    if all(out[f] is None for f in ALL_FIELDS):
        return None
    out["source_id"] = rid
    return out


def _index(rows: list) -> dict:
    """Suchindex über die OpenRouter-Zeilen: exakte ID / ID-Rumpf / Name."""
    idx: dict = {"id": {}, "tail": {}, "name": {}}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        rid = str(row.get("id") or "").strip()
        if not rid:
            continue
        entry = _entry(row, rid)
        if entry is None:
            continue
        idx["id"].setdefault(rid.lower(), entry)
        idx["id"].setdefault(_base_id(rid).lower(), entry)
        base = _base_id(rid)
        tail = _norm(base.split("/", 1)[1] if "/" in base else base)
        if tail:
            idx["tail"].setdefault(tail, []).append(entry)
        name = _name_norm(row.get("name"))
        if name:
            idx["name"].setdefault(name, []).append(entry)
    return idx


def _pick(candidates: list) -> dict | None:
    """Bei mehreren Treffern: Eintrag ohne `:`-Variantensuffix bevorzugen."""
    if not candidates:
        return None
    return sorted(candidates, key=lambda e: (":" in e["source_id"], e["source_id"]))[0]


def _match(model: dict, idx: dict, aliases: dict) -> dict | None:
    """Ein Katalog-Modell auf einen Benchmark-Eintrag abbilden (oder None)."""
    ref = str(model.get("ref") or "").strip()
    aliased = str((aliases.get(ref) or "")).strip().lower()
    if aliased:
        hit = idx["id"].get(aliased) or idx["id"].get(_base_id(aliased).lower())
        if hit:
            return hit
    tail = _norm(model.get("modelID"))
    if tail:
        hit = _pick(idx["tail"].get(tail) or [])
        if hit:
            return hit
    name = _name_norm(model.get("name"))
    if name:
        hit = _pick(idx["name"].get(name) or [])
        if hit:
            return hit
    return None


def _fetch(timeout: int = FETCH_TIMEOUT) -> list:
    """GET gegen OpenRouter (read-only, kein Key)."""
    req = urllib.request.Request(
        SOURCE_URL,
        headers={"Accept": "application/json", "User-Agent": "hestia-benchmarks/1.0"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode())
    rows = payload.get("data") if isinstance(payload, dict) else payload
    return rows or []


def catalog_models() -> list:
    """Live-Katalog (Fehler → leere Liste, kein Crash)."""
    try:
        return [m for m in (catalog.fetch_models() or []) if isinstance(m, dict)]
    except Exception as e:  # noqa: BLE001
        _log(f"Katalog nicht erreichbar ({e}) — keine Zuordnung")
        return []


def catalog_refs(models: list | None = None) -> list:
    """Refs aus dem Live-Katalog."""
    models = catalog_models() if models is None else models
    return [str(m.get("ref") or "").strip() for m in models if m.get("ref")]


def _missing(refs: list, entries: dict) -> list:
    return [ref for ref in refs if ref not in entries]


def refresh(force: bool = False) -> dict:
    """Benchmarks holen — aber nur wenn es etwas zu holen gibt.

    Neu geholt wird nur bei: force ODER Cache fehlt ODER Cache älter als
    `refreshDays` ODER ein Katalog-Ref hat keinen Eintrag und es gab in den
    letzten 6 h keinen Fehlversuch (Drossel). Bei Netzfehler bleibt der alte
    Cache unangetastet und der Fehler landet im Ergebnis (kein Crash).
    """
    settings = read_settings()
    cache = read_cache()
    entries = cache.get("entries") or {}
    fetched_at = cache.get("fetchedAt") or 0
    now = time.time()
    with LOCK:
        last_error = STATE.get("lastError")
        last_attempt = STATE.get("lastAttempt")
    stale = not fetched_at or (now - fetched_at) > settings["refreshDays"] * 86400
    models = catalog_models()
    refs = catalog_refs(models)
    missing = _missing(refs, entries)

    if not settings["enabled"]:
        return {"refreshed": False, "reason": "disabled", "error": None,
                "count": len(entries), **state()}
    if not force:
        if not stale and not missing:
            return {"refreshed": False, "reason": "fresh", "error": last_error,
                    "count": len(entries), **state()}
        if missing and not stale and last_attempt is not None \
                and (now - last_attempt) < MISSING_RETRY_THROTTLE:
            return {"refreshed": False, "reason": "throttled", "error": last_error,
                    "count": len(entries), **state()}

    try:
        rows = _fetch()
        idx = _index(rows)
        new_entries = {}
        for m in models:
            ref = str(m.get("ref") or "").strip()
            if not ref:
                continue
            hit = _match(m, idx, settings["aliases"])
            if hit:
                new_entries[ref] = dict(hit)
        payload = {
            "fetchedAt": int(time.time()),
            "source": SOURCE,
            "count": len(new_entries),
            "entries": new_entries,
        }
        write_cache(payload)
    except Exception as e:  # noqa: BLE001 — alter Cache bleibt erhalten
        with LOCK:
            STATE["lastError"] = str(e)[:300]
            STATE["lastAttempt"] = time.time()
        _log(f"Abruf fehlgeschlagen ({e}) — alter Cache bleibt")
        return {"refreshed": False, "reason": "error", "error": str(e)[:300],
                "count": len(entries), "cacheKept": True, **state()}

    with LOCK:
        STATE["lastError"] = None
        STATE["lastAttempt"] = time.time()
        STATE["lastRefresh"] = time.time()
    missing_after = _missing(refs, new_entries)
    _log(f"Abruf ok: {len(new_entries)}/{len(refs)} Modelle zugeordnet "
         f"({len(rows)} Quelle), ohne Zuordnung: {len(missing_after)}")
    return {"refreshed": True, "reason": "forced" if force else
            ("stale" if stale else "missing"), "error": None,
            "count": len(new_entries), "missing": len(missing_after), **state()}


def _select(entry: dict, fields: list) -> dict:
    """Eintrag auf die in der SOT gewählten Felder eindampfen."""
    keep = [f for f in fields if f in ALL_FIELDS]
    if "code_elo" in keep and "code_rank" not in keep:
        keep.append("code_rank")   # Rang gehört zur Elo-Anzeige dazu
    out = {f: entry.get(f) for f in keep}
    out["source_id"] = entry.get("source_id")
    return out


def for_refs(refs: list, fields: list | None = None) -> dict:
    """Benchmark-Einträge für die angegebenen Refs (nur vorhandene)."""
    entries = read_cache().get("entries") or {}
    if fields is None:
        fields = read_settings()["fields"]
    wanted = set(refs or [])
    return {
        ref: _select(entries[ref], fields)
        for ref in wanted
        if ref in entries
    }


def state() -> dict:
    """Zustand für GET /api/benchmarks/status und /api/catalog."""
    settings = read_settings()
    cache = read_cache()
    entries = cache.get("entries") or {}
    fetched_at = cache.get("fetchedAt") or None
    refs = catalog_refs()
    with LOCK:
        last_error = STATE.get("lastError")
    return {
        "enabled": bool(settings["enabled"]),
        "fields": list(settings["fields"]),
        "fetchedAt": fetched_at,
        "ageDays": round((time.time() - fetched_at) / 86400, 2) if fetched_at else None,
        "count": len(entries),
        "source": cache.get("source") or SOURCE,
        "missing": _missing(refs, entries),
        "refreshDays": settings["refreshDays"],
        "lastError": last_error,
    }


def _bootstrap():
    try:
        refresh()
    except Exception as e:  # noqa: BLE001
        _log(f"Start-Abruf fehlgeschlagen: {e}")


def start():
    """Einmaliger Abruf im Daemon-Thread (blockiert den Server-Start nicht)."""
    try:
        settings = read_settings()
    except Exception as e:  # noqa: BLE001
        _log(f"Start fehlgeschlagen: {e}")
        return
    if not settings["enabled"]:
        _log("Benchmarks deaktiviert (enabled=false) — kein Abruf")
        return
    threading.Thread(target=_bootstrap, daemon=True).start()