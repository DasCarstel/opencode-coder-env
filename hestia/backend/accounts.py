#!/usr/bin/env python3
"""accounts.py — Budget-Überwachung der Provider-Konten (OpenCode Go).

Quelle der Wahrheit ist .opencode/hestia-accounts.jsonc (SOT, wie die
Fallbacks-Datei via GitHub Contents API gelesen). Jedes Konto hat einen
keyRef auf ein Feld im OpenBao-Pfad secret/data/mcp/opencode-go; der
bootstrap.py lädt diese Felder als OC_GO_DEFAULT1..3 nach Env.

P1 macht bewusst NUR Abfrage + Anzeige: es wird nichts automatisch
umgeschaltet. refresh() baut den Zustand neu auf, evaluate() liefert eine
Wechsel-Empfehlung, die das Panel anzeigt.
"""
import json
import os
import threading
import time
import urllib.error
import urllib.request

import github_api
import hjsonc

ACCOUNTS_PATH = os.environ.get("HESTIA_ACCOUNTS_PATH", ".opencode/hestia-accounts.jsonc")
CACHE_TTL = int(os.environ.get("HESTIA_ACCOUNTS_CACHE", "60"))   # Sekunden für die Usage-Cache
CONFIG_TTL = 300                                                 # Sekunden für die Config (SOT)
HTTP_TIMEOUT = 10

LOCK = threading.Lock()
_CONFIG_CACHE = {"at": 0.0, "cfg": None}
_STATE_CACHE = {"at": 0.0}

STATE = {
    "ok": True,
    "mode": "auto",
    "interval": 300,
    "active": None,
    "accounts": [],
    "lastRun": None,
    "events": [],
    "error": None,
    "recommendation": {"switchRecommended": False, "target": None, "reason": ""},
}


def _log(msg):
    print(f"[hestia-accounts] {time.strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)


def _config(force: bool = False) -> dict:
    """SOT (JSONC) lesen; 300 s Cache, damit der Panel-Polling nicht GitHub plagt."""
    now = time.time()
    with LOCK:
        if not force and _CONFIG_CACHE["cfg"] is not None \
                and now - _CONFIG_CACHE["at"] < CONFIG_TTL:
            return _CONFIG_CACHE["cfg"]
    cfg, err = {}, None
    try:
        raw = github_api.get_file(ACCOUNTS_PATH)
        if raw:
            cfg = hjsonc.parse(raw) or {}
        else:
            err = f"{ACCOUNTS_PATH} nicht lesbar (fehlt oder leer)"
    except Exception as e:  # noqa: BLE001
        err = str(e)
    if not isinstance(cfg, dict):
        cfg = {}
    cfg["_error"] = err
    with LOCK:
        _CONFIG_CACHE["at"] = now
        _CONFIG_CACHE["cfg"] = cfg
    return cfg


def _key(field: str) -> str:
    """API-Key aus dem Env (vom bootstrap.py aus OpenBao geladen)."""
    return os.environ.get("OC_GO_" + str(field or "").upper(), "")


def _fetch(account: dict, provider: dict) -> dict:
    """Usage eines Kontos abfragen. P1: nur http-json-Adapter (OpenCode Go)."""
    if (provider or {}).get("type") != "http-json":
        return {"ok": False, "error": "kein Adapter (Platzhalter)", "windows": {}}
    url = provider.get("url") or ""
    key = _key((account.get("keyRef") or {}).get("field") or account.get("id") or "")
    if not key:
        return {"ok": False, "error": "kein Key im Env", "windows": {}}
    # opencode.ai weist den Default-urllib-User-Agent mit 403 ab → eigener UA nötig.
    headers = {"Accept": "application/json", "User-Agent": "Hestia/1.0 (opencode-model-panel)"}
    if (provider.get("auth") or "bearer").lower() == "bearer":
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode(errors="replace")[:200].strip()
        except Exception:  # noqa: BLE001
            pass
        # Nur ein echtes EntitlementError ist "nicht abonniert" — andere 403
        # (z. B. WAF/User-Agent) mit Code + Body zeigen, damit sie diagnostizierbar sind.
        if e.code == 403 and "EntitlementError" in body:
            return {"ok": False, "error": "nicht abonniert", "windows": {}}
        return {"ok": False, "error": f"HTTP {e.code}: {body}".strip(), "windows": {}}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e), "windows": {}}

    usage = payload.get("usage") or {}
    windows = {}
    for name, api_key in (provider.get("windows") or {}).items():
        w = usage.get(api_key) or {}
        try:
            percent = float(w.get("percent") or 0)
        except (TypeError, ValueError):
            percent = 0.0
        status = w.get("status") or "unknown"
        used = max(0.0, min(100.0, percent))
        free = 0.0 if status == "rate-limited" else max(0.0, 100.0 - used)
        windows[name] = {
            "used": round(used, 1),
            "free": round(free, 1),
            "status": status,
            "resetsAt": w.get("resetsAt") or "",
        }
    return {"ok": True, "error": None, "windows": windows}


def _min_free(account: dict, windows) -> float:
    """Kleinster Frei-Wert über die betrachteten Fenster (fehlende Fenster = 0)."""
    vals = []
    for name in windows:
        w = (account.get("windows") or {}).get(name)
        if not w:
            return 0.0
        vals.append(float(w.get("free") or 0))
    return min(vals) if vals else 0.0


def _avg_used(account: dict, windows) -> float:
    vals = []
    for name in windows:
        w = (account.get("windows") or {}).get(name)
        if w:
            vals.append(float(w.get("used") or 0))
    return sum(vals) / len(vals) if vals else 0.0


def evaluate(cfg: dict, accounts: list) -> dict:
    """Reine Funktion: Wechsel-Empfehlung aus Config + aktuellem Zustand.

    Erschöpft-Regel hat Vorrang, danach das weiche Gleichgewicht.
    """
    policy = cfg.get("policy") or {}
    bal = policy.get("balance") or {}
    out = {"switchRecommended": False, "target": None, "reason": ""}
    if policy.get("mode") == "off":
        out["reason"] = "Policy-Modus: off"
        return out
    active_id = cfg.get("active") or os.environ.get("OC_GO_ACTIVE") or ""
    active = next((a for a in accounts if a.get("id") == active_id), None)
    others = [a for a in accounts
              if a.get("id") != active_id and a.get("ok") and a.get("windows")]

    # --- Regel 1: aktives Konto erschöpft (0 % frei oder rate-limited) ---
    if policy.get("switchWhenExhausted", True) and active and active.get("ok"):
        dead = [n for n, w in (active.get("windows") or {}).items()
                if (w.get("status") == "rate-limited" or float(w.get("free") or 0) <= 0)]
        if dead:
            pool = [a for a in others if _min_free(a, (active.get("windows") or {}).keys()) > 0]
            if pool:
                best = max(pool, key=lambda a: _min_free(a, (active.get("windows") or {}).keys()))
                out.update({"switchRecommended": True, "target": best["id"],
                            "reason": f"aktiv erschöpft ({', '.join(dead)})"})
            else:
                out["reason"] = f"aktiv erschöpft ({', '.join(dead)}) — kein Ziel frei"
            return out

    # --- Regel 2: weiches Gleichgewicht über die Balance-Fenster ---
    if not (bal.get("enabled") and active and active.get("ok") and others):
        return out
    bwin = bal.get("windows") or ["weekly", "monthly"]
    active_avg = _avg_used(active, bwin)
    others_avg = sum(_avg_used(a, bwin) for a in others) / len(others)
    spread = float(bal.get("maxSpreadPercentPoints") or 25)
    head = float(bal.get("minHeadroomPercent") or 30)
    if active_avg - others_avg >= spread:
        pool = [a for a in others if _min_free(a, bwin) >= head]
        if pool:
            best = max(pool, key=lambda a: _min_free(a, bwin))
            out.update({"switchRecommended": True, "target": best["id"],
                        "reason": "Gleichgewicht (Woche/Monat)"})
    return out


def refresh(force: bool = False) -> dict:
    """Alle Konten abfragen und STATE neu aufbauen."""
    now = time.time()
    with LOCK:
        fresh = _STATE_CACHE["at"] and now - _STATE_CACHE["at"] < CACHE_TTL
    if fresh and not force:
        with LOCK:
            return dict(STATE)

    cfg = _config(force=force)
    providers = cfg.get("providers") or {}
    active_id = cfg.get("active") or os.environ.get("OC_GO_ACTIVE") or ""
    results = []
    for acc in (cfg.get("accounts") or []):
        provider = providers.get(acc.get("provider")) or {}
        key = _key((acc.get("keyRef") or {}).get("field") or acc.get("id") or "")
        res = _fetch(acc, provider)
        results.append({
            "id": acc.get("id"),
            "label": acc.get("label") or acc.get("id"),
            "provider": acc.get("provider"),
            "priority": acc.get("priority"),
            "isActive": acc.get("id") == active_id,
            "hasKey": bool(key),
            "ok": bool(res.get("ok")),
            "error": res.get("error"),
            "windows": res.get("windows") or {},
        })
    results.sort(key=lambda a: (a.get("priority") is None, a.get("priority") or 0, a.get("id") or ""))
    policy = cfg.get("policy") or {}
    rec = evaluate(cfg, results)
    with LOCK:
        STATE["ok"] = not cfg.get("_error") and all(a["ok"] for a in results) if results else False
        STATE["mode"] = policy.get("mode") or "auto"
        STATE["interval"] = int(cfg.get("checkIntervalSeconds") or 300)
        STATE["active"] = active_id or None
        STATE["accounts"] = results
        STATE["lastRun"] = time.time()
        STATE["error"] = cfg.get("_error")
        STATE["recommendation"] = rec
        _STATE_CACHE["at"] = time.time()
        return dict(STATE)


def state() -> dict:
    """Panel-Abfrage: Cache nutzen, aber spätestens nach CACHE_TTL neu laden."""
    try:
        return refresh()
    except Exception as e:  # noqa: BLE001
        with LOCK:
            st = dict(STATE)
        st["error"] = st.get("error") or str(e)
        return st


def run_check() -> dict:
    """Manueller Check (Panel-Button): frisch abfragen, ohne Umschalten (P1)."""
    return refresh(force=True)


def loop():
    while True:
        try:
            interval = int(refresh(force=True).get("interval") or 300)
        except Exception as e:  # noqa: BLE001
            _log(f"Konten-Abfrage fehlgeschlagen: {e}")
            interval = 300
        _log(f"nächste Prüfung in {interval}s")
        time.sleep(max(30, interval))


def start():
    """Hintergrund-Thread (P1: nur Abfrage, kein automatischer Wechsel)."""
    threading.Thread(target=loop, daemon=True).start()
