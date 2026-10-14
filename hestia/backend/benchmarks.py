#!/usr/bin/env python3
"""benchmarks.py — Modell-Benchmarks aus zwei Quellen: Artificial Analysis (AA)
als PRIMÄRquelle, OpenRouter (OR) als Fallback/Ergänzung.

  AA (primär): GET https://artificialanalysis.ai/api/v2/data/llms/models
               Header `x-api-key: $AA_API_KEY` (Opt-in; ohne Key wird AA
               übersprungen). Rate-Limit 1.000 Requests/Tag.
  OR (Fallback): GET https://openrouter.ai/api/v1/models (kein Key, kein Limit).
               Liefert zusätzlich `agentic_index` (AA kennt ihn nicht) und die
               design_arena-Werte `code_elo`/`code_rank` (codecategories).

Feldschlüssel bleiben stabil (UI/Prompt hängen daran), nur die Herkunft ändert
sich. Merge-Regel: **pro Feld gewinnt AA**, nur wo AA leer ist, greift OR.

  Key                  | AA                                     | OpenRouter
  ---------------------|----------------------------------------|---------------------------
  intelligence_index   | artificial_analysis_intelligence_index | intelligence_index
  coding_index         | artificial_analysis_coding_index       | coding_index
  agentic_index        | —                                      | agentic_index
  terminalbench        | terminalbench_v4_0 → _v2_1 → _hard, ×100 | —
  scicode              | scicode ×100                          | —
  tau2                 | tau2 ×100                             | —
  speed                | median_output_tokens_per_second (Tok/s) | —
  code_elo / code_rank | —                                      | design_arena/codecategories

Anteilige AA-Werte (…Benchmarks) liegen als 0..1 vor und werden ×100 gerundet
(1 Dezimale) ausgegeben; AA-Indizes liegen bereits als 0..100 vor.
`livecodebench` ist bei AA für unsere Modelle durchgängig leer (0/59) und wird
deshalb bewusst nicht vorgeschlagen.

Attribution (Pflicht): artificialanalysis.ai / OpenRouter — die Werte stammen
aus den öffentlichen Benchmarks von Artificial Analysis
(https://artificialanalysis.ai) und aus dem OpenRouter-Katalog
(https://openrouter.ai).

Parameter (enabled/refreshDays/fields/aliases) liegen in der SOT
.opencode/hestia-benchmarks.jsonc (HESTIA_BENCHMARKS_PATH), fehlt sie, gelten
die Env-Defaults. Der Abruf selbst wird lokal gecacht — der Cache liegt NICHT im
Repo, sondern unter HESTIA_BENCHMARKS_CACHE (Default
/app/data/model-benchmarks.json, Bind-Mount /etc/hestia wie der Suggest-State).

Zuordnung unserer Katalog-Ref auf eine Quelle, in dieser Priorität:
  1. expliziter Alias (aliases[ref] → exakte Quell-ID)
  2. normalisierte modelID gegen den Teil hinter "/" der Quell-ID
  3. normalisierter Name gegen den Quell-Namen (ohne Klammerzusätze)
  4. datumsbereinigter canonical_slug (OpenRouter listet Modelle teils nur als
     gepinnte Snapshots: "qwen/qwen3.8-max-0902" → canonical_slug
     "qwen/qwen3.8-max-20260902" ist dasselbe Modell wie "qwen3.8-max").
     Priorität ist 2 → 3 → 4; der canonical_slug greift also nur, wenn weder
     ID noch Name treffen, und ausschließlich bei Eindeutigkeit: Bildet ein
     Ref auf mehrere Einträge ab, gibt es keinen Treffer ("—" statt eines
     falschen Werts).
Bei AA wird zusätzlich über `slug` bzw. `model_creator.slug + "/" + slug`
(normalisiert) zugeordnet; bei mehreren Kandidaten gewinnt der mit den meisten
gefüllten Werten.
Nicht zuordenbare Modelle werden weggelassen (das Panel zeigt dann "—").
Werte können null sein — null heißt "keine Daten von der Quelle", nicht 0.

Fehlerverhalten: fällt eine Quelle aus, läuft der Lauf mit der anderen weiter;
schlagen BEIDE fehl, bleibt der alte Cache unangetastet.
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
# OpenRouter (Fallback/Ergänzung) — wie bisher.
SOURCE_URL = os.environ.get(
    "HESTIA_BENCHMARKS_URL", "https://openrouter.ai/api/v1/models"
)
# Artificial Analysis (Primärquelle) — braucht AA_API_KEY (bootstrap.py).
AA_MODELS_URL = os.environ.get(
    "AA_MODELS_URL", "https://artificialanalysis.ai/api/v2/data/llms/models"
)
AA_API_KEY = os.environ.get("AA_API_KEY", "").strip()
SOURCE = "openrouter"
# Kurzschlüssel der Quellen im Cache/State.
AA_KEY_NAME = "aa"
OR_KEY_NAME = "or"
ATTRIBUTION = "artificialanalysis.ai / OpenRouter"
FETCH_TIMEOUT = int(os.environ.get("HESTIA_BENCHMARKS_TIMEOUT", "25"))
# Mindestabstand zwischen zwei Versuchen, die nur wegen fehlender Zuordnung
# ausgelöst werden (verhindert Abruf-Hämmern bei dauerhaft unzuordenbaren Refs).
MISSING_RETRY_THROTTLE = float(
    os.environ.get("HESTIA_BENCHMARKS_RETRY_THROTTLE", str(6 * 3600))
)

DEFAULT_ENABLED = os.environ.get("HESTIA_BENCHMARKS", "1") != "0"
DEFAULT_REFRESH_DAYS = int(os.environ.get("HESTIA_BENCHMARKS_REFRESH_DAYS", "30"))
DEFAULT_FIELDS = [
    "intelligence_index",
    "coding_index",
    "terminalbench",
    "speed",
    "agentic_index",
    "code_elo",
]

# Metrik-Felder (nie weggelassen) und Anzeige-/Prompt-Felder (SOT `fields`).
METRIC_FIELDS = (
    "intelligence_index",
    "coding_index",
    "agentic_index",
    "terminalbench",
    "scicode",
    "tau2",
    "speed",
)
EXTRA_FIELDS = ("code_elo", "code_rank")
ALL_FIELDS = METRIC_FIELDS + EXTRA_FIELDS

# Anzeige-/Prompt-Bezeichnung je Feld — die UI und der Suggest-Prompt lesen
# diese Map, damit neue Felder ohne Frontend-Änderung benannt sind.
FIELD_LABELS = {
    "intelligence_index": "Intelligenz",
    "coding_index": "Coding",
    "agentic_index": "Agentic-Index",
    "terminalbench": "Terminal-Bench",
    "scicode": "SciCode",
    "tau2": "τ²-bench",
    "speed": "Tok/s",
    "code_elo": "Code-Elo",
    "code_rank": "Code-Rang",
}

# AA-Evaluations → unsere Felder. Anteile (0..1) werden ×100 skaliert.
AA_SCALE_100 = {
    "terminalbench": ("terminalbench_v4_0", "terminalbench_v2_1", "terminalbench_hard"),
    "scicode": ("scicode",),
    "tau2": ("tau2",),
}
AA_PLAIN = {
    "intelligence_index": "artificial_analysis_intelligence_index",
    "coding_index": "artificial_analysis_coding_index",
}

LOCK = threading.Lock()
STATE = {
    "lastError": None,
    "lastAttempt": None,
    "lastRefresh": None,
}


def _log(msg: str):
    print(f"[hestia-benchmarks] {time.strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)


def aa_enabled() -> bool:
    """AA nur nutzen, wenn ein Key hinterlegt ist (AA ist ohne Key nutzlos)."""
    return bool(AA_API_KEY)


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
    """Quellen-ID ohne Variantensuffix ("…:free" → "…")."""
    return str(rid or "").split(":", 1)[0].strip()


# Abschließendes Datums-Suffix eines Snapshots: -20260902 oder -2026-09-02.
_DATE_SUFFIX = re.compile(r"-(?:\d{8}|\d{4}-\d{2}-\d{2})$")


def _slug_no_date(s) -> str:
    """Slug ohne abschließendes Datums-Suffix.

    "qwen/qwen3.8-max-20260902" → "qwen/qwen3.8-max"
    "acme/widget-2026-09-02"     → "acme/widget"
    """
    return _DATE_SUFFIX.sub("", str(s or "").strip())


def _tail(s) -> str:
    """Teil hinter "/" eines Slugs (ohne Namespace, z. B. "qwen/qwen3.8-max")."""
    text = str(s or "").strip()
    return text.split("/", 1)[1] if "/" in text else text


def _num(val):
    """Zahl übernehmen (bool ausgeschlossen), sonst None."""
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        return None
    return val


def _pct(val):
    """AA-Anteil (0..1) → Prozentwert, 1 Dezimale."""
    val = _num(val)
    return None if val is None else round(val * 100, 1)


def _first_pct(ev: dict, keys: tuple):
    """Ersten gefüllten Wert einer Fallback-Kette nehmen und ×100 skalieren."""
    for key in keys:
        val = _num(ev.get(key))
        if val is not None:
            return round(val * 100, 1)
    return None


def _num1(val):
    """Zahl auf 1 Dezimale runden (Tok/s)."""
    val = _num(val)
    return None if val is None else round(val, 1)


# --- OpenRouter ------------------------------------------------------------


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
        out[field] = _num(aa.get(field))

    elo = rank = None
    best = None
    for item in (bench.get("design_arena") or []):
        if not isinstance(item, dict) or item.get("category") != "codecategories":
            continue
        val = _num(item.get("elo"))
        if val is None:
            continue
        if best is None or val > best:
            best = val
            rank = item.get("rank")
    out["code_elo"] = best
    out["code_rank"] = rank if isinstance(rank, int) else None
    out["aa_id"] = None
    out["or_id"] = rid
    out["source_id"] = rid          # Rückwärtskompatibilität (alte Caches/UI)
    if all(out[f] is None for f in ALL_FIELDS):
        return None
    return out


def _index(rows: list) -> dict:
    """Suchindex über die OpenRouter-Zeilen: exakte ID / ID-Rumpf / Slug-Datum / Name."""
    idx: dict = {"id": {}, "tail": {}, "canon": {}, "name": {}}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        rid = str(row.get("id") or "").strip()
        if not rid:
            continue
        entry = _entry(row, rid)
        if entry is None:
            continue
        base = _base_id(rid)
        idx["id"].setdefault(rid.lower(), entry)
        idx["id"].setdefault(base.lower(), entry)
        tail = _norm(base.split("/", 1)[1] if "/" in base else base)
        if tail:
            idx["tail"].setdefault(tail, []).append(entry)
        # canonical_slug ohne Datums-Suffix → gepinnte Snapshots desselben
        # Modells (nur nach _pick/_unique, nie als Überschreibung bestehender Keys).
        # Wie der tail-Index wird nur der Teil hinter "/" verglichen.
        slug = row.get("canonical_slug")
        if slug:
            canon = _norm(_slug_no_date(_tail(_base_id(slug))))
            if canon:
                idx["canon"].setdefault(canon, []).append(entry)
        name = _name_norm(row.get("name"))
        if name:
            idx["name"].setdefault(name, []).append(entry)
    return idx


def _pick(candidates: list) -> dict | None:
    """Bei mehreren Treffern: Eintrag ohne `:`-Variantensuffix bevorzugen."""
    if not candidates:
        return None
    return sorted(candidates, key=lambda e: (":" in e["source_id"], e["source_id"]))[0]


def _unique(candidates: list) -> dict | None:
    """Nur bei Eindeutigkeit liefern — mehrdeutig heißt "kein Treffer".

    Bewusst konservativ: lieber "—" im Panel als ein falsch zugeordneter Wert.
    """
    distinct = {}
    for entry in candidates or []:
        if isinstance(entry, dict) and entry.get("source_id"):
            distinct[entry["source_id"]] = entry
    if len(distinct) != 1:
        return None
    return next(iter(distinct.values()))


def _match(model: dict, idx: dict, aliases: dict) -> dict | None:
    """Ein Katalog-Modell auf einen OpenRouter-Eintrag abbilden (oder None).

    Priorität: Alias → modelID → Name → datumsbereinigter canonical_slug.
    Bestehende (früher gefundene) Treffer werden nie überschrieben.
    """
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
    if tail:
        hit = _unique(idx["canon"].get(tail) or [])
        if hit:
            return hit
    return None


# --- Artificial Analysis ---------------------------------------------------


def _aa_entry(row: dict) -> dict | None:
    """Einen AA-Datensatz auf unsere Felder eindampfen (oder None)."""
    ev = row.get("evaluations")
    ev = ev if isinstance(ev, dict) else {}
    out = {
        field: _num(ev.get(key)) for field, key in AA_PLAIN.items()
    }
    for field, chain in AA_SCALE_100.items():
        out[field] = _first_pct(ev, chain)
    out["speed"] = _num1(row.get("median_output_tokens_per_second"))
    # agentic_index / code_elo / code_rank kennt AA nicht.
    out["agentic_index"] = None
    out["code_elo"] = None
    out["code_rank"] = None
    out["aa_id"] = str(row.get("slug") or "").strip() or None
    out["or_id"] = None
    out["source_id"] = out["aa_id"] or ""
    if all(out.get(f) is None for f in ALL_FIELDS):
        return None
    return out


def _filled(entry: dict) -> int:
    return sum(1 for f in ALL_FIELDS if _num(entry.get(f)) is not None)


def _best(candidates: list) -> dict | None:
    """Bei mehreren AA-Kandidaten: den mit den meisten gefüllten Werten nehmen."""
    if not candidates:
        return None
    return sorted(candidates, key=lambda e: (-_filled(e), str(e.get("aa_id") or "")))[0]


def _aa_index(rows: list) -> dict:
    """Suchindex über AA-Zeilen: slug / creator+slug / Name."""
    idx: dict = {"slug": {}, "full": {}, "name": {}}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        entry = _aa_entry(row)
        if entry is None:
            continue
        slug = str(row.get("slug") or "").strip()
        creator = row.get("model_creator")
        creator_slug = str((creator or {}).get("slug") or "").strip() \
            if isinstance(creator, dict) else ""
        if slug:
            idx["slug"].setdefault(_norm(slug), []).append(entry)
            idx["slug"].setdefault(_norm(_slug_no_date(slug)), []).append(entry)
            if creator_slug:
                idx["full"].setdefault(_norm(f"{creator_slug}/{slug}"), []).append(entry)
                idx["full"].setdefault(
                    _norm(f"{creator_slug}/{_slug_no_date(slug)}"), []
                ).append(entry)
        name = _name_norm(row.get("name"))
        if name:
            idx["name"].setdefault(name, []).append(entry)
    return idx


def _aa_match(model: dict, idx: dict, aliases: dict) -> dict | None:
    """Katalog-Modell → AA-Datensatz (Alias → modelID/slug → Name → creator/slug)."""
    ref = str(model.get("ref") or "").strip()
    aliased = str((aliases.get(ref) or "")).strip()
    if aliased:
        hit = _best(idx["slug"].get(_norm(aliased)) or []) \
            or _best(idx["full"].get(_norm(aliased)) or [])
        if hit:
            return hit
    model_id = str(model.get("modelID") or "").strip()
    tail = _norm(model_id)
    if tail:
        hit = _best(idx["slug"].get(tail) or [])
        if hit:
            return hit
    name = _name_norm(model.get("name"))
    if name:
        hit = _best(idx["name"].get(name) or [])
        if hit:
            return hit
    # Slug-Fallback: vollständiger "creator/slug" (mit oder ohne Namespace).
    for candidate in (model_id, ref):
        key = _norm(_slug_no_date(candidate))
        if key and "/" in str(candidate or ""):
            hit = _best(idx["full"].get(key) or [])
            if hit:
                return hit
    return None


# --- Abruf -----------------------------------------------------------------


def _fetch_or(timeout: int = FETCH_TIMEOUT) -> list:
    """GET gegen OpenRouter (read-only, kein Key)."""
    req = urllib.request.Request(
        SOURCE_URL,
        headers={"Accept": "application/json", "User-Agent": "hestia-benchmarks/1.0"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode())
    rows = payload.get("data") if isinstance(payload, dict) else payload
    return rows or []


# Alte Bezeichnung (OR) bleibt als Alias nutzbar.
_fetch = _fetch_or


def _fetch_aa(timeout: int = FETCH_TIMEOUT) -> list:
    """GET gegen Artificial Analysis (read-only, Key im Header `x-api-key`)."""
    if not aa_enabled():
        raise RuntimeError("AA_API_KEY nicht gesetzt — Artificial Analysis übersprungen")
    req = urllib.request.Request(
        AA_MODELS_URL,
        headers={
            "Accept": "application/json",
            "User-Agent": "hestia-benchmarks/1.1",
            "x-api-key": AA_API_KEY,
        },
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


def _empty_entry() -> dict:
    return {f: None for f in ALL_FIELDS}


def _merge(aa_hit: dict | None, or_hit: dict | None) -> dict | None:
    """AA- und OR-Treffer felderweise zusammenführen — AA gewinnt pro Feld.

    None, wenn beide Seiten keine Werte liefern (dann ist der Ref fachlich
    unzuordenbar → Eintrag weglassen, UI zeigt "—").
    """
    if not aa_hit and not or_hit:
        return None
    out = _empty_entry()
    for field in ALL_FIELDS:
        val = aa_hit.get(field) if aa_hit else None
        if val is None and or_hit:
            val = or_hit.get(field)
        out[field] = val
    out["aa_id"] = (aa_hit or {}).get("aa_id")
    out["or_id"] = (or_hit or {}).get("or_id") or (or_hit or {}).get("source_id")
    # Rückwärtskompatibilität: alte Caches/UI kennen nur `source_id` (OR-ID).
    out["source_id"] = out["or_id"] or out["aa_id"]
    if all(out[f] is None for f in ALL_FIELDS):
        return None
    return out


def _source_state(ok: bool, count: int, error: str | None = None) -> dict:
    st = {"ok": bool(ok), "ts": int(time.time()), "count": int(count)}
    if error:
        st["error"] = str(error)[:300]
    return st


def _cache_sources(cache: dict) -> dict:
    """Quellen-Status aus dem Cache (abwärtskompatibel: alte Caches ohne `sources`)."""
    src = cache.get("sources")
    if isinstance(src, dict):
        out = {}
        for key in (AA_KEY_NAME, OR_KEY_NAME):
            raw = src.get(key) if isinstance(src.get(key), dict) else {}
            out[key] = {
                "ok": bool(raw.get("ok")),
                "ts": raw.get("ts"),
                "count": int(raw.get("count") or 0),
                **({"error": raw["error"]} if raw.get("error") else {}),
            }
        return out
    # Legacy: Cache vor dem AA-Update → alles kam aus OpenRouter.
    entries = cache.get("entries") or {}
    with_aa = sum(1 for e in entries.values() if isinstance(e, dict) and e.get("aa_id"))
    with_or = sum(1 for e in entries.values()
                  if isinstance(e, dict) and (e.get("or_id") or e.get("source_id")))
    return {
        AA_KEY_NAME: {"ok": False, "ts": None, "count": with_aa},
        OR_KEY_NAME: {"ok": bool(cache.get("fetchedAt")),
                 "ts": cache.get("fetchedAt"), "count": with_or},
    }


def _missing_sources(cache: dict) -> list:
    """Aktivierte Quellen, die im Cache noch nicht erfolgreich vorliegen.

    Der reale Fall: AA_API_KEY wird nachträglich in OpenBao hinterlegt → AA ist
    aktiv, aber der (noch frische) Cache kennt sie nicht (`sources.aa` fehlt
    oder `ok=false`). Ohne diese Prüfung bliebe AA bis zum nächsten TTL- oder
    force-Refresh komplett außen vor. OR ist immer aktiv (kein Key nötig);
    alte Caches ohne `sources` fallen über _cache_sources() korrekt auf
    "nicht erfolgreich gelaufen" zurück.
    """
    cached = _cache_sources(cache or {})
    active = [OR_KEY_NAME]
    if aa_enabled():
        active.insert(0, AA_KEY_NAME)
    return [key for key in active if not (cached.get(key) or {}).get("ok")]


def refresh(force: bool = False) -> dict:
    """Benchmarks holen — aber nur wenn es etwas zu holen gibt.

    Neu geholt wird nur bei: force ODER Cache fehlt ODER Cache älter als
    `refreshDays` ODER eine aktivierte Quelle fehlt im Cache (reason
    `new-source`, z. B. AA-Key nachträglich gesetzt) ODER ein Katalog-Ref hat
    keinen Eintrag — jeweils mit Drossel (kein erneuter Versuch innerhalb von
    6 h). Fällt eine Quelle aus, läuft der Lauf mit der anderen weiter; bei
    Netzfehlern in BEIDEN Quellen bleibt der alte Cache unangetastet und der
    Fehler landet im Ergebnis (kein Crash).
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
    new_source = _missing_sources(cache)

    if not settings["enabled"]:
        return {"refreshed": False, "reason": "disabled", "error": None,
                "count": len(entries), **state()}
    if not force:
        if not stale and not missing and not new_source:
            return {"refreshed": False, "reason": "fresh", "error": last_error,
                    "count": len(entries), **state()}
        # Drossel gilt für beide "es gibt was zu tun"-Gründe: ein dauerhaft
        # fehlschlagender Abruf (z. B. ungültiger AA-Key) darf nicht bei jedem
        # Statusabruf erneut zuschlagen.
        if (missing or new_source) and not stale and last_attempt is not None \
                and (now - last_attempt) < MISSING_RETRY_THROTTLE:
            return {"refreshed": False, "reason": "throttled", "error": last_error,
                    "count": len(entries), **state()}

    sources = {}
    aa_rows: list = []
    or_rows: list = []
    errors = []
    if aa_enabled():
        try:
            aa_rows = _fetch_aa() or []
            sources[AA_KEY_NAME] = _source_state(True, len(aa_rows))
        except Exception as e:  # noqa: BLE001 — AA ist optional
            errors.append(f"AA: {e}")
            sources[AA_KEY_NAME] = _source_state(False, 0, e)
            _log(f"Artificial Analysis fehlgeschlagen ({e}) — nutze OpenRouter")
    else:
        sources[AA_KEY_NAME] = {"ok": False, "ts": None, "count": 0}
    try:
        or_rows = _fetch_or() or []
        sources[OR_KEY_NAME] = _source_state(True, len(or_rows))
    except Exception as e:  # noqa: BLE001 — OR ist optional, wenn AA lief
        errors.append(f"OR: {e}")
        sources[OR_KEY_NAME] = _source_state(False, 0, e)
        _log(f"OpenRouter fehlgeschlagen ({e}) — nutze Artificial Analysis")

    if not sources.get(AA_KEY_NAME, {}).get("ok") and not sources.get(OR_KEY_NAME, {}).get("ok"):
        # Beide Quellen tot → alter Cache bleibt unangetastet.
        err = "; ".join(errors) or "keine Quelle erreichbar"
        with LOCK:
            STATE["lastError"] = err[:300]
            STATE["lastAttempt"] = time.time()
        return {"refreshed": False, "reason": "error", "error": err[:300],
                "count": len(entries), "cacheKept": True, **state()}

    try:
        aa_idx = _aa_index(aa_rows)
        or_idx = _index(or_rows)
        new_entries = {}
        for m in models:
            ref = str(m.get("ref") or "").strip()
            if not ref:
                continue
            merged = _merge(
                _aa_match(m, aa_idx, settings["aliases"]) if sources[AA_KEY_NAME]["ok"]
                else None,
                _match(m, or_idx, settings["aliases"]) if sources[OR_KEY_NAME]["ok"] else None,
            )
            if merged:
                new_entries[ref] = merged
        payload = {
            "fetchedAt": int(time.time()),
            "source": SOURCE,          # Legacy-Key
            "attribution": ATTRIBUTION,
            "sources": sources,
            "count": len(new_entries),
            "entries": new_entries,
        }
        write_cache(payload)
    except Exception as e:  # noqa: BLE001 — alter Cache bleibt erhalten
        with LOCK:
            STATE["lastError"] = str(e)[:300]
            STATE["lastAttempt"] = time.time()
        _log(f"Aufbau fehlgeschlagen ({e}) — alter Cache bleibt")
        return {"refreshed": False, "reason": "error", "error": str(e)[:300],
                "count": len(entries), "cacheKept": True, **state()}

    with LOCK:
        STATE["lastError"] = "; ".join(errors)[:300] if errors else None
        STATE["lastAttempt"] = time.time()
        STATE["lastRefresh"] = time.time()
    missing_after = _missing(refs, new_entries)
    reason = ("forced" if force else "stale" if stale
              else "new-source" if new_source else "missing")
    _log(f"Abruf ok ({reason}): {len(new_entries)}/{len(refs)} Modelle zugeordnet "
         f"(AA {len(aa_rows)} / OR {len(or_rows)} Zeilen), ohne Zuordnung: "
         f"{len(missing_after)}")
    return {"refreshed": True, "reason": reason, "error": STATE.get("lastError"),
            "count": len(new_entries), "missing": len(missing_after), **state()}


# Zuordnungs-IDs gehören zu jedem Ref, unabhängig von der Feldauswahl.
ID_FIELDS = ("aa_id", "or_id", "source_id")


def _select(entry: dict, fields: list) -> dict:
    """Eintrag auf die in der SOT gewählten Felder eindampfen (Prompt-Pfad)."""
    keep = [f for f in fields if f in ALL_FIELDS]
    if "code_elo" in keep and "code_rank" not in keep:
        keep.append("code_rank")   # Rang gehört zur Elo-Anzeige dazu
    out = {f: entry.get(f) for f in keep}
    for f in ID_FIELDS:
        out[f] = entry.get(f)
    return out


def _select_all(entry: dict) -> dict:
    """Alle Felder, die für das Modell tatsächlich vorliegen.

    Für die Tabellen-UI: die aufklappbare Detailzeile zeigt jeden Benchmark,
    nicht nur die in der SOT konfigurierten. `null`-Werte entfallen
    (schlanker Payload), die Zuordnungs-IDs sind immer dabei, sobald gesetzt.
    """
    out = {f: entry[f] for f in ALL_FIELDS if entry.get(f) is not None}
    for f in ID_FIELDS:
        if entry.get(f) is not None:
            out[f] = entry[f]
    return out


def for_refs(refs: list, fields: list | None = None,
             all_fields: bool = False) -> dict:
    """Benchmark-Einträge für die angegebenen Refs (nur vorhandene).

    Default (`all_fields=False`): ausschließlich die in der SOT konfigurierten
    Felder — so nutzt der KI-Prompt (suggest.py) weiter nur die Auswahl.
    `all_fields=True` liefert je Ref alle vorhandenen Felder (Catalog/UI).
    """
    entries = read_cache().get("entries") or {}
    if not all_fields and fields is None:
        fields = read_settings()["fields"]
    wanted = set(refs or [])
    return {
        ref: (_select_all(entries[ref]) if all_fields
              else _select(entries[ref], fields))
        for ref in wanted
        if ref in entries
    }


def field_labels(fields: list | None = None) -> dict:
    """Label-Map — ohne Argument alle bekannten Felder (für die Detailzeile)."""
    if fields is None:
        return dict(FIELD_LABELS)
    return {f: FIELD_LABELS[f] for f in fields if f in FIELD_LABELS}


def state() -> dict:
    """Zustand für GET /api/benchmarks/status und /api/catalog."""
    settings = read_settings()
    cache = read_cache()
    entries = cache.get("entries") or {}
    fetched_at = cache.get("fetchedAt") or None
    refs = catalog_refs()
    with LOCK:
        last_error = STATE.get("lastError")
    sources = _cache_sources(cache)
    return {
        "enabled": bool(settings["enabled"]),
        "fields": list(settings["fields"]),
        "fieldLabels": field_labels(),
        "sources": sources,
        "aaEnabled": aa_enabled(),
        "attribution": ATTRIBUTION,
        "fetchedAt": fetched_at,
        "ageDays": round((time.time() - fetched_at) / 86400, 2) if fetched_at else None,
        "count": len(entries),
        "source": cache.get("source") or SOURCE,     # Legacy-Key fürs Frontend
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
    if not aa_enabled():
        _log("AA_API_KEY nicht gesetzt — nur OpenRouter (Fallback) als Quelle")
    threading.Thread(target=_bootstrap, daemon=True).start()