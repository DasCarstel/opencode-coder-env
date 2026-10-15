#!/usr/bin/env python3
"""usage.py — eigener Verbrauch je Modell aus den Sitzungen des OpenCode-Servers.

Quelle ist `/api/session` derselben Basis wie catalog.py: jede Sitzung trägt
`cost`, `tokens` und `model`. Daraus wird der Verbrauch über rollierende Fenster
(5 h / 7 Tage / 30 Tage) summiert und — bei Modellen mit Budget aus gobudget.py —
ins Verhältnis zum jeweiligen Fensterlimit gesetzt (Verbrauchsampel).

Bewusste Grenzen:
  * Eine Sitzung zählt in das Fenster, in dem sie ZULETZT aktiv war
    (`time.updated`). Eine stundenlang laufende Sitzung zählt damit komplett in
    das Fenster ihres letzten Beitrags — ein guter Näherungswert, aber nicht die
    Abrechnung des Anbieters.
  * Sichtbar sind nur die Sitzungen DIESES Servers, nicht die Nutzung des
    Web-Chats in der OpenCode-Konsole.
  * `cost` ist der vom Server verbuchte Preis; Gratis-Kontingente und Rabatte
    des Anbieters kennt er nicht (ein Modell mit dem Namen "free" kann hier
    trotzdem Kosten zeigen — dann wird es tatsächlich abgerechnet).

Schwellen (SOT-unabhängig, bewusst fest): ab 80 % warnen, ab 100 % gesperrt.
"""
import time

WINDOWS = (("h5", 5 * 3600), ("week", 7 * 86400), ("month", 30 * 86400))
LABELS = {"h5": "5 h", "week": "Woche", "month": "30 Tage"}
WARN_PCT = 80.0
BLOCK_PCT = 100.0

CACHE_TTL = 60.0  # s — wie der Modellkatalog; das Panel pollt jede Minute

_CACHE = {"ts": 0.0, "sessions": [], "agg": {}}


def _log(msg: str) -> None:
    print(f"[usage] {msg}", flush=True)


def _empty() -> dict:
    return {"cost": {k: 0.0 for k, _ in WINDOWS}, "tokens": 0, "sessions": 0, "last": 0}


def _aggregate(raw: list) -> dict:
    """Sitzungen → Verbrauch je Modell-Ref."""
    now = int(time.time() * 1000)
    out = {}
    for s in raw:
        if not isinstance(s, dict):
            continue
        m = s.get("model") or {}
        provider = str(m.get("providerID") or "")
        model_id = str(m.get("id") or "")
        if not model_id:
            continue
        ref = f"{provider}/{model_id}"
        e = out.setdefault(ref, _empty())
        tok = s.get("tokens") or {}
        cache = tok.get("cache") or {}
        e["sessions"] += 1
        e["tokens"] += sum(int(tok.get(k) or 0) for k in ("input", "output", "reasoning"))
        e["tokens"] += int(cache.get("read") or 0) + int(cache.get("write") or 0)
        t = s.get("time") or {}
        try:
            ts = int(t.get("updated") or t.get("created") or 0)
        except (TypeError, ValueError):
            ts = 0
        e["last"] = max(e["last"], ts)
        try:
            cost = float(s.get("cost") or 0.0)
        except (TypeError, ValueError):
            cost = 0.0
        for key, span in WINDOWS:
            if ts and now - ts <= span * 1000:
                e["cost"][key] += cost
        for key in e["cost"]:
            e["cost"][key] = round(e["cost"][key], 6)
    return out


def _load(force: bool = False) -> None:
    if not force and _CACHE["ts"] and time.time() - _CACHE["ts"] < CACHE_TTL:
        return
    raw = []
    try:
        import catalog
        d = catalog._get(f"{catalog.OC_SERVER_URL}/api/session?limit=1000", timeout=20)
        items = d.get("data", d) if isinstance(d, dict) else d
        raw = [s for s in (items or []) if isinstance(s, dict)]
    except Exception as e:  # noqa: BLE001 — Verbrauch darf nie den Lauf kippen
        _log(f"Sitzungen nicht lesbar ({e}) — Verbrauch unbekannt")
    _CACHE["sessions"] = raw
    _CACHE["agg"] = _aggregate(raw)
    _CACHE["ts"] = time.time()


def sessions(force: bool = False) -> list:
    """Rohe Sitzungen des OpenCode-Servers (gecacht)."""
    _load(force)
    return _CACHE["sessions"]


def aggregate(force: bool = False) -> dict:
    """Verbrauch je Modell-Ref: {ref: {cost{h5,week,month}, tokens, sessions, last}}."""
    _load(force)
    return _CACHE["agg"]


def ampel(ref: str, budget: dict = None, force: bool = False) -> dict:
    """Verbrauchsampel eines Modells.

    `budget` ist der Block aus gobudget.budget_for() (sonst wird er geholt).
    Rückgabe:
      cost/tokens/sessions/last — Verbrauch aus den eigenen Sitzungen
      pct     — {h5, week, month} in Prozent des jeweiligen Fensterlimits
                (None, wenn das Modell kein Abo-Limit hat)
      window/tightest — das engste Fenster (Name, Prozent)
      state   — ok | warn | blocked | unlimited | nolimit | unknown
                ("unknown" = zu diesem Modell gibt es keine Sitzungen; dann wird
                bewusst KEIN Prozentwert gemeldet, statt 0 % vorzutäuschen)
    """
    if budget is None:
        try:
            import gobudget
            budget = gobudget.budget_for(ref)
        except Exception:  # noqa: BLE001
            budget = None

    agg = aggregate(force).get(ref)
    if agg is None:
        # Keine Sitzungen zu diesem Modell: keine Aussage möglich. Besser "unknown"
        # als "0 %", das eine Messung vortäuschen würde. Ein unbegrenztes Modell
        # bleibt aber auch ohne Sitzungen "unlimited".
        return {"cost": {k: 0.0 for k, _ in WINDOWS}, "tokens": 0, "sessions": 0,
                "last": 0, "pct": {}, "window": None, "tightest": None,
                "state": "unlimited" if (budget and budget.get("unlimited")) else "unknown"}

    pct = {}
    if budget and not budget.get("unlimited"):
        for key, _ in WINDOWS:
            limit = budget.get(key)
            pct[key] = round(100.0 * agg["cost"][key] / limit, 1) if limit else None

    tight = None
    for key, _ in WINDOWS:
        val = pct.get(key)
        if val is not None and (tight is None or val > tight[1]):
            tight = (key, val)

    if tight is None:
        state = "unlimited" if (budget and budget.get("unlimited")) else "nolimit"
    elif tight[1] >= BLOCK_PCT:
        state = "blocked"
    elif tight[1] >= WARN_PCT:
        state = "warn"
    else:
        state = "ok"

    return {
        "cost": agg["cost"], "tokens": agg["tokens"], "sessions": agg["sessions"],
        "last": agg["last"], "pct": pct,
        "window": tight[0] if tight else None,
        "tightest": tight[1] if tight else None,
        "state": state,
    }


def overview(force: bool = False) -> dict:
    """Ampel für alle Modelle, zu denen es Sitzungen gibt."""
    return {ref: ampel(ref, force=force) for ref in sorted(aggregate(force))}


def prompt_block(refs, max_lines: int = 10, force: bool = False) -> str:
    """Verbrauchszeilen für den Prompt — engste Fenster zuerst.

    Modelle ohne Sitzungen werden weggelassen (die Zeile wäre nur Rauschen);
    modelle mit Abo-Limit zeigen die Prozentwerte, alle anderen ihre Kosten.
    """
    rows = []
    for ref in refs:
        a = ampel(ref, force=force)
        # Ungenutzte Modelle weglassen — eine Zeile mit lauter Nullen ist nur
        # Rauschen und kostet den Prompt voller Aufmerksamkeit.
        if a["state"] not in ("blocked", "warn") and a["cost"]["month"] <= 0:
            continue
        rows.append((ref, a))
    rows.sort(key=lambda r: (
        -(r[1]["tightest"] if r[1]["tightest"] is not None else -1.0),
        -r[1]["cost"]["month"],
    ))
    out = []
    for ref, a in rows[:max_lines]:
        if a["pct"]:
            parts = [f"{LABELS[k]} {a['pct'][k]:.0f} %"
                     for k, _ in WINDOWS if a["pct"].get(k) is not None]
            mark = {"blocked": " — GESPERRT", "warn": " — knapp"}.get(a["state"], "")
            out.append(f"- {ref}: " + " | ".join(parts) + mark)
        else:
            c = a["cost"]
            mark = "unbegrenzt" if a["state"] == "unlimited" else "kein Abo-Limit"
            out.append(f"- {ref}: {mark} (eigene Kosten: 5 h ${c['h5']:.2f}, "
                       f"30 Tage ${c['month']:.2f})")
    return "\n".join(out)
