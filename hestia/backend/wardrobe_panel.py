#!/usr/bin/env python3
"""Wardrobe-Panel: Bildmodell-Verwaltung + Verbrauchs-/Budget-Tracking.

Das Outfit-Bild-Rendering (outfits-render, eigener FastAPI-Dienst auf einem
anderen Host, Projekt PRO-347) schreibt jede Bildanfrage in die Tabelle
`render_usage` der Wardrobe-DB (Postgres) und pflegt Einstellungen in
`panel_settings` (key,value). Dieses Modul liest BEIDE Tabellen und liefert
die Payload für GET /api/wardrobe/panel. Die Autorisierung ist DB-seitig: die
Rolle `hestia` darf nur SELECT auf render_usage und SELECT/UPDATE auf
panel_settings — bewusst KEIN Join auf outfits (deshalb zeigen wir outfit_id
gekrürzt, erste 8 Zeichen).

DSN-Beschaffung:
  1. `WARDROBE_DSN` (klassische postgres:// DSN, Port 5433 von außen) —
     im Container von bootstrap.py gefüllt (OpenBao secret/data/wardrobe/db,
     Feld `dsn`, AA-Muster). Auch als Kommandozeilen-Fallback direkt setzbar.
  2. Fehlt die DSN oder ist psycopg2 nicht installiert → Payload mit
     `db: {"ok": false, "reason": …}` statt Crash; Einstellungen fallen auf
     die Defaults, Verbrauch auf leere Werte.

Preistabelle IMAGE_MODELS lebt HIER (nicht in wardrobe_costs.py): sie gilt
für die Gemini-Bild-Modelle des Render-Dienstes (USD je 1M Tokens, Stand
Nov 2026, Quelle ai.google.dev). `per_image` schätzt die Kosten je Bildgröße
aus den gemessenen Output-Token-Zahlen (Tabelle `tokens`).

Funktionen sind testbar ohne Netz und ohne DB (`row_cost`, `summarize`,
`ampel`, `validate_settings`, `defaults_merged`) — der Server-Endpunkt ruft
nur `payload()`/`apply_settings()` auf.
"""
import datetime
import os

# Workflow für den Text-Modell-Wechsel (workflow_dispatch, Muster accounts.py
# SWITCH_WORKFLOW) — verteilt AI_TEXT_MODEL/AI_VISION_MODEL in die Wardrobe-
# Container auf dem Docker-Host (hestia/ci/update-wardrobe-model.sh).
WARDROBE_MODEL_WORKFLOW = os.environ.get(
    "HESTIA_WARDROBE_MODEL_WORKFLOW", "wardrobe-model.yml"
)

DSN_ENV = "WARDROBE_DSN"
CACHE_TTL = 30.0  # s — das Panel pollt im Minutentakt, DB ist klein
RECENT_LIMIT = 20
BUDGET_MAX = 500.0  # USD/Monat — obere Validierungsgrenze apply_settings

CACHE = {"ts": 0.0, "data": None}

DEFAULT_IMAGE_MODEL = "gemini-nano-banana-2.1"
DEFAULT_IMAGE_SIZE = "2K"
DEFAULT_BUDGET = 20.0  # USD/Monat — geplantes Panel-Budget, überschreibbar
DEFAULT_TEXT_MODEL = "glm-5.3-flash"

IMAGE_SIZES = ("1K", "2K", "4K")

# Bild-Render-Modelle (Gemini generativelanguage, USD je 1M Tokens):
#   text_in  — Prompt-/Input-Tokens
#   text_out — Text-Output-Tokens (None = kein Text-Billing preisnotiert)
#   image_out — Bild-Output-Tokens (die eigentlichen Render-Kosten)
#   tokens   — gemessene/gemeldete Output-Token-Zahl je Bildgröße
#              (Schätzbasis für `per_image`)
# display-name: die echte Modell-Identität (aus der Live-Gemini-API
# ListModels, displayName je Kanon-Id): "#Gemini 3.1 Flash Image" etc. sind
# Marketing-Aliase — hinter "gemini-3.1-flash-image" steckt z. B. "Nano
# Banana 2", hinter "gemini-3-pro-image" "Nano Banana Pro". Die -preview/
# Legacy-Aliase sind bewusst NICHT in der Liste — die Ids hier sind die
# finalen GA-Kanon-Namen.
IMAGE_MODELS = [
    {"id": "gemini-nano-banana-2.1", "name": "Nano Banana 2.1", "text_in": 1.50, "text_out": 7.50, "image_out": 30.0,
     "tokens": {"1K": 1120, "2K": 1680, "4K": 3780}},
    {"id": "gemini-3.1-flash-image", "name": "Nano Banana 2 (= Gemini 3.1 Flash Image)", "text_in": 0.50, "text_out": 3.0, "image_out": 60.0,
     "tokens": {"0.5K": 747, "1K": 1120, "2K": 1680}},
    {"id": "gemini-3.1-flash-lite-image", "name": "Nano Banana 2 Lite (= 3.1 Flash-Lite Image)", "text_in": 0.25, "text_out": 1.5, "image_out": 30.0,
     "tokens": {"1K": 1120}},
    {"id": "gemini-3-pro-image", "name": "Nano Banana Pro (= Gemini 3 Pro Image)", "text_in": 2.00, "text_out": None, "image_out": 120.0,
     "tokens": {"0.5K": 1120, "1K": 1120, "2K": 1120, "4K": 2000}},
    {"id": "gemini-2.5-flash-image", "name": "Nano Banana 1 (= Gemini 2.5 Flash Image)", "text_in": 0.30, "text_out": None, "image_out": 30.0,
     "tokens": {"1K": 1290}},
]

# Text-Modelle (Tagging + Outfit-Text im Wardrobe):
#   note    — Anzeige im Panel
#   billing — "go" = läuft über das OpenCode-Go-Abo (wardrobe-shim),
#             "gemini" = direkte Gemini-Abrechnung
TEXT_MODELS = [
    {"id": "glm-5.3-flash", "note": "Go-Flat aktiv", "billing": "go"},
    {"id": "gpt-6-luna", "note": "Go-Limit $15/mo", "billing": "go"},
    {"id": "gemini-flash-latest", "note": "Gemini billed", "billing": "gemini"},
    {"id": "gemini-4-argon", "note": "Gemini billed, 7–14× teurer als glm", "billing": "gemini"},
]

# Go-Modell-Liste für den wardrobe-shim (GO_MODELS in ai-shim/.env): Modelle
# in dieser Liste laufen über das Go-Abo, alle anderen routet der Shim direkt
# zu Gemini. Der Wardrobe-Model-Wechsel (update-wardrobe-model.sh) versieht
# bei Go-Modellen genau diese beiden Einträge — NICHT mehr.
GO_TEXT_MODELS = ("glm-5.3-flash", "gpt-6-luna")


class SettingsError(Exception):
    pass


# ── Presistabelle: Schätz- und Rechfunktionen (rein, offline testbar) ───────


def image_model_spec(model: str) -> dict | None:
    """Preis-Spezifikation eines Bild-Modells (None = unbekannt)."""
    for m in IMAGE_MODELS:
        if m["id"] == model:
            return m
    return None


def row_cost(model: str, prompt_tokens, text_output_tokens, image_tokens) -> float | None:
    """Kosten einer render_usage-Zeile aus dem Token-Split (USD).

    prompt*text_in + text_out*text_out + image_tokens*image_out, alles /1M.
    Unbekanntes Modell → None (Zeile bleibt aus Summen/ø bewusst draußen,
    taucht aber in `recent` mit cost=null auf).
    """
    spec = image_model_spec(model)
    if spec is None:
        return None

    def num(v):
        try:
            return float(v) if v is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    cost = num(prompt_tokens) * spec["text_in"]
    if spec["text_out"] is not None:
        cost += num(text_output_tokens) * spec["text_out"]
    cost += num(image_tokens) * spec["image_out"]
    return cost / 1_000_000.0


def per_image(spec: dict) -> dict:
    """Schätzkosten je Bildgröße aus der Token-Tabelle (USD je Bild).

    rein: nur output-image-Tokens (Text-Anteil der Schätzwerte ist vernachlässigbar
    und in den gemessenen Zahlen je Skippe nicht sauber getrennt).
    """
    return {size: round(tokens * spec["image_out"] / 1_000_000.0, 4)
            for size, tokens in (spec.get("tokens") or {}).items()}


def image_models_payload(active: str | None = None) -> list:
    """Liste für den Panel-Payload: Preise + Schätzungen + Aktiv-Markierung.

    active: aktuell gesetztes image_model (aus den Einstellungen); None →
    Default (gemini-nano-banana-2.1).
    """
    active = active or DEFAULT_IMAGE_MODEL
    out = []
    for spec in IMAGE_MODELS:
        out.append({
            "id": spec["id"],
            "name": spec.get("name"),
            "text_in": spec["text_in"],
            "text_out": spec["text_out"],
            "image_out": spec["image_out"],
            "per_image": per_image(spec),
            "active": spec["id"] == active,
        })
    return out


def text_models_payload(active: str | None = None) -> list:
    """Text-Modell-Liste mit Aktiv-Markierung (default glm-5.3-flash)."""
    if active is None:
        cached = CACHE.get("data") or {}
        active = (cached.get("settings") or {}).get("text_model") or DEFAULT_TEXT_MODEL
    return [{"id": m["id"], "note": m["note"], "billing": m["billing"],
             "active": m["id"] == active} for m in TEXT_MODELS]


# ── Ampel + Aggregation (rein, offline testbar) ────────────────────────────


def ampel(month_spent: float, budget: float) -> dict:
    """Verbrauchsampel gegen das Monatsbudget.

    Schwelle bewusst fest: ok < 60 %, warn 60–90 %, err > 90 % oder negatives
    Restbudget (Überschreitung ohne Budget-Zeile im Panel erzwingen).
    """
    budget = float(budget or 0.0)
    spent = float(month_spent or 0.0)
    remaining = budget - spent
    if budget > 0:
        pct = round(100.0 * spent / budget, 1)
    else:
        pct = None
    if pct is None or remaining < 0:
        state = "err" if (remaining < 0 or (pct or 0) > 90.0) else "ok"
        if pct is None and remaining >= 0:
            state = "ok"
    elif pct > 90.0:
        state = "err"
    elif pct >= 60.0:
        state = "warn"
    else:
        state = "ok"
    return {"pct": pct, "state": state, "remaining": round(remaining, 6), "budget": budget}


def summarize(month_rows: list, life_rows: list, budget: float) -> dict:
    """Aggregation der render_usage-Model-Zeilen → usage-Block.

    month_rows/life_rows: je [{"model", "count", "prompt_tokens", "text_output_tokens", "image_tokens"}]
    (aus dem SQL GROUP BY — Modelle mit unbekannten Preisen zählen zwar als
    Renders, aber bewusst mit cost=null → nicht in Kwota/ø).
    """
    def agg(rows):
        rows = rows or []
        renders = 0
        spent = 0.0
        by_model = []
        for r in rows:
            n = int(r.get("count") or 0)
            renders += n
            # Token-Spalten sind je Modell SUMMIERT (SQL GROUP BY) — ein Aufruf
            # von row_cost darauf ergibt bereits das Modell-Total, nicht je
            # Render. Unbekanntes Modell → None: zählt als Render, aber
            # bewusst nicht in Summe/ø.
            c = row_cost(r.get("model"), r.get("prompt_tokens"),
                         r.get("text_output_tokens"), r.get("image_tokens"))
            if c is None:
                by_model.append({"id": r.get("model"), "count": n, "spent": None, "avg": None})
                continue
            spent += c
            by_model.append({"id": r.get("model"), "count": n,
                             "spent": round(c, 6),
                             "avg": round(c / n, 6) if n else None})
        by_model.sort(key=lambda m: (-(m["spent"] or -1.0), str(m["id"])))
        return {"spent": round(spent, 6), "renders": renders,
                "avg": round(spent / renders, 6) if renders else None,
                "by_model": by_model}

    month = agg(month_rows)
    life = agg(life_rows)
    return {
        "month": {**month, "budget": float(budget or 0.0), **ampel(month["spent"], budget)},
        "lifetime": life,
    }


def month_start_epoch() -> float:
    """Start des aktuellen lokalen Kalendermonats als Unix-Epoch (Sekunden)."""
    now = datetime.datetime.now()
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return start.timestamp()


def recent_view(rows: list) -> list:
    """Letzte Renders für den Payload: kostenbekannt → Kosten, sonst null.

    outfit_id gekürzt auf 8 Zeichen (kein Join auf outfits — die hestia-Rolle
    hat bewusst nur SELECT auf render_usage), neueste zuerst.
    """
    out = []
    for r in rows or []:
        c = row_cost(r.get("model"), r.get("prompt_tokens"),
                     r.get("text_output_tokens"), r.get("image_tokens"))
        created = r.get("created_at")
        if isinstance(created, datetime.datetime):
            created = created.isoformat(timespec="seconds")
        outfit = str(r.get("outfit_id") or "")
        out.append({
            "created_at": created,
            "outfit_id": str(r.get("outfit_id") or "") or None,
            "outfit": outfit[:8] if outfit else None,
            "model": r.get("model"),
            "image_size": r.get("image_size"),
            "prompt_tokens": r.get("prompt_tokens"),
            "text_output_tokens": r.get("text_output_tokens"),
            "image_tokens": r.get("image_tokens"),
            "tokens": r.get("total_tokens"),
            "total_tokens": r.get("total_tokens"),
            "cost": round(c, 6) if c is not None else None,
            "status": r.get("status"),
            "error": r.get("error"),
        })
    return out


# ── Einstellungen: Defaults + Validierung (rein, offline testbar) ──────────


def defaults_merged(kvs: dict) -> dict:
    """panel_settings-Zeilen (key→value) auf dieDefaults gemischt.

    Fehlende Keys → Default; image_size/image_model/text_model ungefiltert
    übernommen (DB-Zeilen sinddb-seitig vertrauenswürdig), budget_monthly
    mit Fallback auf den Default, wenn es nicht als Zahl lesbar ist.
    """
    kvs = kvs or {}
    budget_raw = kvs.get("budget_monthly")
    try:
        budget = float(budget_raw) if budget_raw not in (None, "") else DEFAULT_BUDGET
    except (TypeError, ValueError):
        budget = DEFAULT_BUDGET
    return {
        "image_model": kvs.get("image_model") or DEFAULT_IMAGE_MODEL,
        "image_size": kvs.get("image_size") or DEFAULT_IMAGE_SIZE,
        "budget_monthly": budget,
        "text_model": kvs.get("text_model") or DEFAULT_TEXT_MODEL,
    }


def validate_settings(payload: dict) -> dict:
    """Payload validieren → {key: value}-Dict der zu schreibenden Einstellungen.

    Bekannte Felder (image_model, image_size, budget_monthly, text_model) mit
    Typ-/Wert-Prüfung; unbekannte Felder werden bewusst ignoriert (bzw. nur
    loggt der Aufrufer). Fehler → SettingsError; nichts leichtfertig anwenden.
    """
    if not isinstance(payload, dict):
        raise SettingsError("Body muss ein JSON-Objekt sein")
    out = {}
    if "image_model" in payload and payload.get("image_model") is not None:
        model = str(payload.get("image_model")).strip()
        if image_model_spec(model) is None:
            raise SettingsError(
                f"image_model '{model}' unbekannt — erlaubt: "
                + ", ".join(m["id"] for m in IMAGE_MODELS))
        out["image_model"] = model
    if "image_size" in payload and payload.get("image_size") is not None:
        size = str(payload.get("image_size")).strip().upper()
        if size not in IMAGE_SIZES:
            raise SettingsError(
                f"image_size '{size}' unbekannt — erlaubt: " + ", ".join(IMAGE_SIZES))
        out["image_size"] = size
    if "budget_monthly" in payload and payload.get("budget_monthly") is not None:
        raw = payload.get("budget_monthly")
        try:
            budget = float(raw)
        except (TypeError, ValueError):
            raise SettingsError(f"budget_monthly '{raw}' ist keine Zahl")
        if not (0.0 <= budget <= BUDGET_MAX):
            raise SettingsError(
                f"budget_monthly {budget} außerhalb 0–{int(BUDGET_MAX)}")
        out["budget_monthly"] = budget
    if "text_model" in payload and payload.get("text_model") is not None:
        model = str(payload.get("text_model")).strip()
        if not any(m["id"] == model for m in TEXT_MODELS):
            raise SettingsError(
                f"text_model '{model}' unbekannt — erlaubt: "
                + ", ".join(m["id"] for m in TEXT_MODELS))
        out["text_model"] = model
    return out


# ── DB-Zugriff (lazy: psycopg2 importiert sich erst bei echtem Bedarf) ─────


def get_dsn() -> str:
    """DSN aus der Env (bootstrap.py füllt sie aus OpenBao — AA-Muster)."""
    return (os.environ.get(DSN_ENV) or "").strip()


def _connect():
    """Verbindung zur Wardrobe-DB — None, wenn DSN/psycopg2 fehlen (kein Crash)."""
    dsn = get_dsn()
    if not dsn:
        return None, "kein DSN — WARDROBE_DSN nicht gesetzt (OpenBao secret/data/wardrobe/db)"
    try:
        import psycopg2
    except ImportError:
        return None, "psycopg2 nicht installiert (DSN vorhanden)"
    try:
        return psycopg2.connect(dsn, connect_timeout=5), None
    except Exception as e:  # noqa: BLE001 — Fehler nur als reason im Payload
        return None, f"DB nicht erreichbar: {e}"


def read_settings(conn) -> dict:
    """panel_settings-Zeilen (key,value) → Dict (nur Plaintext-Keys)."""
    out = {}
    with conn.cursor() as cur:
        cur.execute("SELECT key, value FROM panel_settings")
        for k, v in cur.fetchall():
            out[str(k)] = "" if v is None else str(v)
    return out


def _sum_rows(conn, since_epoch):
    """render_usage je Modell aggregieren (count + Token-Summen).

    since_epoch=None → lifetime (alle Zeilen), sonst nur Zeilen ab dem
    Monatsstart. extract(epoch …) hält die Grenze tz-unabhängig.
    """
    sql = (
        "SELECT model, COUNT(*), COALESCE(SUM(COALESCE(prompt_tokens,0)),0), "
        "COALESCE(SUM(COALESCE(text_output_tokens,0)),0), "
        "COALESCE(SUM(COALESCE(image_tokens,0)),0) FROM render_usage"
    )
    sql += " WHERE COALESCE(extract(epoch FROM created_at), 0) >= %s" if since_epoch is not None else ""
    sql += " GROUP BY model"
    with conn.cursor() as cur:
        cur.execute(sql, (since_epoch,) if since_epoch is not None else ())
        return [{"model": m, "count": int(n), "prompt_tokens": float(p),
                 "text_output_tokens": float(t), "image_tokens": float(i)}
                for m, n, p, t, i in cur.fetchall()]


def _recent_rows(conn):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT created_at, outfit_id, model, image_size, prompt_tokens, "
            "text_output_tokens, image_tokens, total_tokens, status, error "
            f"FROM render_usage ORDER BY created_at DESC LIMIT {int(RECENT_LIMIT)}")
        cols = ("created_at", "outfit_id", "model", "image_size", "prompt_tokens",
                "text_output_tokens", "image_tokens", "total_tokens", "status", "error")
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def upsert_settings(conn, values: dict):
    """UPSERT in panel_settings (key,value): INSERT … ON CONFLICT DO UPDATE."""
    for key, value in (values or {}).items():
        text = str(value)
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO panel_settings (key, value, updated_at) VALUES (%s, %s, now()) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
                (key, text))


def apply_settings(payload: dict) -> dict:
    """Einstellungen validieren + UPSERTen; Rückgabe für POST /api/wardrobe/settings.

    payload schon vom Caller gelesen (self._body()). Validierungsfehler →
    SettingsError (Server macht 400 {}). Wenn text_model wechselt, meldet die
    Rückgabe `text_model_changed` — der Server stößt darauf den Workflow an.
    """
    changes = validate_settings(payload)
    settings = defaults_merged({})   # Fallback, falls die DB nicht lesbar ist
    old = {}
    conn, reason = _connect()
    if conn is None:
        raise SettingsError(f"Wardrobe-DB nicht nutzbar: {reason}")
    try:
        old = defaults_merged(read_settings(conn))
        if changes:
            upsert_settings(conn, changes)
            conn.commit()
        settings = defaults_merged(read_settings(conn))
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    text_changed = bool(
        changes.get("text_model")
        and changes.get("text_model") != old.get("text_model"))
    # Cache leeren: das Panel soll die frisch gespeicherten Einstellungen
    # sofort sehen, nicht die aus dem 30-s-Cache.
    CACHE.update(ts=0.0, data=None)
    return {
        "settings": settings,
        "applied": sorted(changes.keys()),
        "text_model_changed": text_changed,
    }


# ── Payload-API ────────────────────────────────────────────────────────────


def panel_state(force: bool = False) -> dict:
    """Aktuelle Einstellungen + Verbrauch aus der DB (gecacht, wirft nie)."""
    if not force and CACHE["data"] is not None and (datetime.datetime.now().timestamp() - CACHE["ts"]) < CACHE_TTL:
        return CACHE["data"]

    settings = defaults_merged({})
    usage = summarize([], [], settings["budget_monthly"])
    recent = []
    ok, reason = False, None
    conn, reason = _connect()
    if conn is None:
        reason = reason or "DB-Verbindung fehlgeschlagen"
    else:
        try:
            kvs = read_settings(conn)
            settings = defaults_merged(kvs)
            month_rows = _sum_rows(conn, month_start_epoch())
            life_rows = _sum_rows(conn, None)
            usage = summarize(month_rows, life_rows, settings["budget_monthly"])
            recent = recent_view(_recent_rows(conn))
            ok = True
            conn.rollback()
        except Exception as e:  # noqa: BLE001 — Panel darf nie crashen
            reason = f"Abfrage fehlgeschlagen: {e}"
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    out = {"settings": settings, "usage": usage, "recent": recent,
           "db": {"ok": ok, **({"reason": reason} if not ok else {})}}
    CACHE.update(ts=datetime.datetime.now().timestamp(), data=out)
    return out


def usage_flat(state: dict) -> dict:
    """usage-Block in der Frontend-Kontrakt-Form:

    {month_spent, month_renders, avg_cost, lifetime_spent, lifetime_renders,
     remaining, ampel ('ok'|'warn'|'err'), ampel_pct, by_model, recent}.
    Die verschachtelte Innenstruktur (usage.month/lifetime) bleibt zusätzlich
    drin, alte Leser brechen nicht.
    """
    u = state.get("usage") or {}
    month = dict(u.get("month") or {})
    life = u.get("lifetime") or {}
    amp = month  # ampel-Werte (pct/state/remaining) sind flach gespreaded
    flat = {
        "month_spent": month.get("spent"),
        "month_renders": month.get("renders"),
        "avg_cost": month.get("avg"),
        "lifetime_spent": life.get("spent"),
        "lifetime_renders": life.get("renders"),
        "remaining": amp.get("remaining"),
        "ampel": amp.get("state"),
        "ampel_pct": amp.get("pct"),
        "budget": amp.get("budget"),
        "by_model": month.get("by_model") or [],
    }
    out = {**u, **flat}
    out["recent"] = state.get("recent") or []
    return out


def payload(force: bool = False) -> dict:
    """API-Nutzlast für GET /api/wardrobe/panel (best effort, wirft nie an).

    force=True umgeht den 30-s-Cache (z. B. direkt nach apply_settings).
    """
    state = panel_state(force=force)
    return {
        "settings": state["settings"],
        "usage": usage_flat(state),
        "recent": state["recent"],
        "image_models": image_models_payload(state["settings"]["image_model"]),
        "text_models": text_models_payload(state["settings"]["text_model"]),
        "db": state["db"],
    }


if __name__ == "__main__":
    import json as _json
    print(_json.dumps(payload(), indent=2, ensure_ascii=False))
