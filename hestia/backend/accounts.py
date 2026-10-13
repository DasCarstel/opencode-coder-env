#!/usr/bin/env python3
"""accounts.py — Budget-Überwachung der Provider-Konten (OpenCode Go).

Quelle der Wahrheit ist .opencode/hestia-accounts.jsonc (SOT, wie die
Fallbacks-Datei via GitHub Contents API gelesen). Jedes Konto hat einen
keyRef auf ein Feld im OpenBao-Pfad secret/data/mcp/opencode-go; der
bootstrap.py lädt diese Felder als OC_GO_DEFAULT1..3 nach Env.

P1 macht bewusst NUR Abfrage + Anzeige. P2 kommt dazu: switch_account()
schreibt zuerst das Feld "active" in die SOT (diese Datei) zurück und
dispatcht danach den Workflow switch-account.yml, der den Key des Zielkontos
aus OpenBao (secret/data/mcp/opencode-go) liest und in die OpenCode-Container
setzt. Hestia ist damit die EINZIGE Stelle, die den aktiven Account bestimmt —
das gleichnamige Feld "active" im OpenBao-Secret wird nicht mehr benutzt.
Der Wechsel passiert asynchron im CI; der Key wird im Panel-Prozess nie berührt.
"""
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

import github_api
import hjsonc

ACCOUNTS_PATH = os.environ.get("HESTIA_ACCOUNTS_PATH", ".opencode/hestia-accounts.jsonc")
CACHE_TTL = int(os.environ.get("HESTIA_ACCOUNTS_CACHE", "60"))   # Sekunden für die Usage-Cache
CONFIG_TTL = 300                                                 # Sekunden für die Config (SOT)
HTTP_TIMEOUT = 10
PROBE_MODEL = os.environ.get("HESTIA_PROBE_MODEL", "mimo-v2.6-flash")
# Workflow, der den eigentlichen Konto-Wechsel ausführt (OpenBao + Container).
SWITCH_WORKFLOW = os.environ.get("HESTIA_SWITCH_WORKFLOW", "switch-account.yml")
DEFAULT_COOLDOWN = int(os.environ.get("HESTIA_SWITCH_COOLDOWN") or 1800)   # Sekunden

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
    # --- P2: Wechsel ---
    "lastSwitch": None,     # Unix-Zeitpunkt des letzten erfolgreichen Dispatches
    "switching": False,     # gerade ein Wechsel in Arbeit (Cooldown/Anzeige)
    "switchWorkflow": SWITCH_WORKFLOW,
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


def _probe(key: str, provider: dict) -> tuple:
    """Mini-Modellaufruf, um ein Konto ohne Guthaben zu erkennen.

    Der Usage-Endpoint antwortet auch bei leerem Guthaben mit 200 — der Fehler
    ('Insufficient account funds') kommt erst beim eigentlichen Modell-Aufruf.
    Rückgabe: (ok, detail).
    """
    base = (provider.get("url") or "").rstrip("/")
    if not base or not key:
        return False, "kein Key/URL"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        # Cloudflare blockt 'Hestia/...' mit Fehler 1010 → Browser-UA nötig
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
        "x-opencode-session": "hestia-probe",
    }
    if (provider.get("auth") or "bearer").lower() == "bearer":
        headers["Authorization"] = f"Bearer {key}"
    body = json.dumps({"model": PROBE_MODEL,
                       "messages": [{"role": "user", "content": "hi"}],
                       "max_tokens": 1}).encode()
    req = urllib.request.Request(base + "/chat/completions", data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            resp.read()
        return True, ""
    except urllib.error.HTTPError as e:
        text = ""
        try:
            text = e.read().decode(errors="replace")[:200]
        except Exception:  # noqa: BLE001
            pass
        if e.code in (401, 403) and "Insufficient" in text:
            return False, "keine Guthaben"
        return False, f"HTTP {e.code}: {text}".strip()
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def _expiring(account: dict, windows, within_hours: float, min_remaining: float):
    """Fenster, das innerhalb von within_hours zurücksetzt und noch >= min_remaining % frei hat.

    Rückgabe: {"window": <name>, "hoursLeft": float, "free": float} oder None.
    """
    best = None
    for name in (windows or []):
        w = (account.get("windows") or {}).get(name) or {}
        try:
            free = float(w.get("free") or 0)
        except (TypeError, ValueError):
            continue
        # Nur zählen, wenn vom Fenster noch etwas übrig ist — ein leeres
        # Abo bringt keinen Benefit, das tut schon die Erschöpft-Regel.
        if free < min_remaining:
            continue
        iso = str(w.get("resetsAt") or "")
        if not iso:
            continue
        try:
            dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        hours = (dt - datetime.now(timezone.utc)).total_seconds() / 3600.0
        # Vergangene/ungültige Zeitpunkte und zu weit entfernte Resets ignorieren.
        if not (0 <= hours <= within_hours):
            continue
        if best is None or hours < best["hoursLeft"]:
            best = {"window": name, "hoursLeft": hours, "free": free}
    return best


def evaluate(cfg: dict, accounts: list) -> dict:
    """Reine Funktion: Wechsel-Empfehlung aus Config + aktuellem Zustand.

    Reihenfolge: bald ablaufend ("use it or lose it") → erschöpft → Gleichgewicht.
    """
    policy = cfg.get("policy") or {}
    bal = policy.get("balance") or {}
    out = {"switchRecommended": False, "target": None, "reason": ""}
    if policy.get("mode") == "off":
        out["reason"] = "Policy-Modus: off"
        return out
    active_id = str(cfg.get("active") or "")   # SOT (diese Datei) ist die einzige Quelle
    active = next((a for a in accounts if a.get("id") == active_id), None)
    others = [a for a in accounts
              if a.get("id") != active_id and a.get("ok") and a.get("windows")]

    # --- Regel 0: bald ablaufendes Abo bevorzugen ("use it or lose it") ---
    # Steht das bevorzugte Konto schon auf active, greifen die Regeln unten.
    exp = policy.get("expiring") or {}
    if exp.get("enabled") and active_id:
        within = float(exp.get("withinHours") or 24)
        min_rem = float(exp.get("minRemainingPercent") or 0)
        ewin = exp.get("windows") or ["weekly", "monthly"]
        soon = [(a, _expiring(a, ewin, within, min_rem)) for a in others]
        soon = [(a, e) for a, e in soon if e]
        if soon:
            best_acc, best_exp = min(soon, key=lambda p: p[1]["hoursLeft"])
            out.update({"switchRecommended": True, "target": best_acc["id"],
                        "reason": (f"bald ablaufend ({best_exp['window']}-Reset in "
                                   f"{best_exp['hoursLeft']:.1f} h, {best_exp['free']:.0f} % frei)")})
            return out

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
    active_id = str(cfg.get("active") or "")   # SOT (diese Datei) ist die einzige Quelle
    results = []
    for acc in (cfg.get("accounts") or []):
        provider = providers.get(acc.get("provider")) or {}
        key = _key((acc.get("keyRef") or {}).get("field") or acc.get("id") or "")
        res = _fetch(acc, provider)
        probe_ok, probe_detail = _probe(key, provider)
        results.append({
            "id": acc.get("id"),
            "label": acc.get("label") or acc.get("id"),
            "provider": acc.get("provider"),
            "priority": acc.get("priority"),
            # 'ok' = Usage-Endpoint. Die Mini-Probe ist NUR informativ: Cloudflare
            # blockt Python-urllib per TLS-Fingerprint (curl im CI-Check klappt), sie
            # darf den Kontowechsel daher nicht verhindern.
            "isActive": acc.get("id") == active_id,
            "hasKey": bool(key),
            "ok": bool(res.get("ok")),
            "error": res.get("error"),
            "probe": {"ok": probe_ok, "detail": probe_detail or None},
            "windows": res.get("windows") or {},
        })
    results.sort(key=lambda a: (a.get("priority") is None, a.get("priority") or 0, a.get("id") or ""))
    policy = cfg.get("policy") or {}
    exp = policy.get("expiring") or {}
    if exp.get("enabled"):
        # Pro Konto das bald zurücksetzende Fenster markieren (Anzeige + Regel 0).
        within = float(exp.get("withinHours") or 24)
        min_rem = float(exp.get("minRemainingPercent") or 0)
        ewin = exp.get("windows") or ["weekly", "monthly"]
        for acc in results:
            acc["expiring"] = _expiring(acc, ewin, within, min_rem)
    else:
        for acc in results:
            acc["expiring"] = None
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
        STATE["display"] = cfg.get("display") or {}
        STATE["policy"] = policy
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


def _cooldown() -> int:
    """Mindestabstand zwischen zwei Wechseln: policy.cooldownSeconds > Env > 1800."""
    policy = _config().get("policy") or {}
    try:
        return int(policy.get("cooldownSeconds") or os.environ.get("HESTIA_SWITCH_COOLDOWN") or 1800)
    except (TypeError, ValueError):
        return DEFAULT_COOLDOWN


def _event(**fields) -> dict:
    """Ereignis an dieSTATE anhängen (Ringpuffer, älteste zuerst raus)."""
    ev = {"at": time.time(), **fields}
    with LOCK:
        events = STATE.setdefault("events", [])
        events.append(ev)
        del events[:-200]
    return ev


def _notify(target: str, reason: str) -> bool:
    """Empfehlungs-Event nur einmal pro Ziel+Intervall (notify-Modus, P2)."""
    now = time.time()
    with LOCK:
        events = STATE.setdefault("events", [])
        # gleiches Ziel, gleiche Begründung und innerhalb des Intervalls → nichts Neues
        for ev in events:
            if ev.get("type") != "recommendation" or ev.get("target") != target:
                continue
            if now - float(ev.get("at") or 0) < int(STATE.get("interval") or 300):
                return False
    _event(type="recommendation", target=target, reason=reason)
    return True


def _set_sot_active(target: str, reason: str) -> str:
    """'active' in der SOT (JSONC) setzen und atomar zurückschreiben.

    Bewusst per Regex auf dem Rohtext statt per json.dumps: so überleben die
    Kommentare in hestia-accounts.jsonc. Gibt die Commit-SHA zurück ("" wenn
    die API keine meldet).
    """
    raw = github_api.get_file(ACCOUNTS_PATH)
    if not raw:
        raise RuntimeError(f"{ACCOUNTS_PATH} nicht lesbar")
    pat = re.compile(r'^[ \t]*"active"[ \t]*:[ \t]*"[^"]*"[ \t]*,?[ \t]*$', re.M)
    if pat.search(raw):
        # Kommentarzeilen, die an das active-Feld angehängt sind, bleiben stehen.
        new_raw = pat.sub(f'  "active": "{target}",', raw, count=1)
    else:
        # Feld fehlt: direkt nach der öffnenden Klammer einfügen
        i = raw.find("{")
        if i < 0:
            raise RuntimeError(f"{ACCOUNTS_PATH}: keine öffnende Klammer gefunden")
        new_raw = raw[:i + 1] + f'\n  "active": "{target}",' + raw[i + 1:]
    # Kontrolle: muss wieder parsbar sein und target enthalten
    check = hjsonc.parse(new_raw) or {}
    if str(check.get("active") or "") != target:
        raise RuntimeError(f"active={target} nicht in die SOT geschrieben")
    msg = f"hestia: active OpenCode-Go-Account -> {target} ({reason})"
    res = github_api.put_files({ACCOUNTS_PATH: new_raw}, msg)
    commit = ""
    if isinstance(res, dict):
        commit = str(res.get("commit") or res.get("sha") or "")
    elif isinstance(res, list) and res:
        first = res[0]
        if isinstance(first, dict):
            commit = str(first.get("commit") or first.get("sha") or "")
    _log(f"SOT aktualisiert: active={target} ({ACCOUNTS_PATH}, commit {commit or 'n/a'})")
    return commit


def switch_account(target: str, reason: str = "", force: bool = False, by: str = "manual") -> dict:
    """Konto-Wechsel: SOT 'active' setzen, dann Workflow-Dispatch (Container).

    Reihenfolge ist bewusst SOT zuerst: der Workflow bekommt das Zielkonto als
    Input, und ein CI-Container liest die SOT als Wahrheit. Der Key wird hier
    NICHT gelesen oder angefasst — das macht der Workflow auf dem Runner
    (dort liegt der OpenBao-Token). Rückgabe ist immer ein Dict; Fehler kommen
    als {"switched": False, "error": ...} zurück, damit der Panel-Check und
    die loop() nicht werfen müssen.
    """
    target = str(target or "").strip()
    if not target:
        return {"switched": False, "error": "kein Zielkonto"}

    # Zustand ggf. frisch holen, damit hasKey/ok aus dem aktuellen Lauf stammen
    if not STATE.get("accounts"):
        try:
            refresh(force=True)
        except Exception as e:  # noqa: BLE001
            return {"switched": False, "error": f"Konten nicht lesbar: {e}"}

    acc = next((a for a in STATE.get("accounts") or [] if a.get("id") == target), None)
    if acc is None:
        return {"switched": False, "error": f"unbekanntes Konto: {target}"}
    if not acc.get("hasKey"):
        return {"switched": False, "error": f"kein Key im Env für {target}"}
    if not acc.get("ok"):
        return {"switched": False,
                "error": f"{target} nicht abfragbar: {acc.get('error') or 'unbekannt'}"}

    cooldown = _cooldown()
    last = STATE.get("lastSwitch")
    if last and not force:
        remaining = cooldown - (time.time() - float(last))
        if remaining > 0:
            _event(type="switch-skipped", target=target, reason="Cooldown aktiv", by=by)
            return {"switched": False, "error": "Cooldown aktiv", "cooldownRemaining": int(remaining)}

    with LOCK:
        if STATE.get("switching"):
            return {"switched": False, "error": "Wechsel läuft bereits", "target": target}
        STATE["switching"] = True
    try:
        # 1) SOT zuerst: 'active' ist die einzige Stelle, die den aktiven Account bestimmt.
        commit = _set_sot_active(target, reason or by)
    except Exception as e:  # noqa: BLE001
        with LOCK:
            STATE["switching"] = False
        _event(type="switch-error", target=target, reason=f"SOT: {e}", by=by)
        return {"switched": False, "error": f"SOT nicht geschrieben: {e}"}
    try:
        # 2) Danach der Workflow: Key aus OpenBao lesen und in die Container setzen.
        disp = github_api.dispatch_workflow(
            SWITCH_WORKFLOW,
            inputs={"account": target, "reason": reason or "hestia"},
        )
    except Exception as e:  # noqa: BLE001
        with LOCK:
            STATE["switching"] = False
        STATE["active"] = target          # SOT steht schon auf dem neuen Konto
        _event(type="switch-error", target=target, reason=str(e), by=by)
        return {"switched": False, "error": str(e), "commit": commit, "sotWritten": True}
    with LOCK:
        STATE["switching"] = False
        STATE["lastSwitch"] = time.time()
        STATE["active"] = target
        _CONFIG_CACHE["at"] = 0.0         # nächster _config() liest die neue SOT
    _event(type="switch", target=target, reason=reason or "hestia", by=by)
    _log(f"Wechsel angestoßen: {target} (via {by}, {SWITCH_WORKFLOW})")
    res = {"switched": True, "target": target, "by": by, "dispatched": disp,
           "commit": commit, "sotWritten": True}
    res["hermesSync"] = _sync_hermes_pool(target, reason)
    return res


def _sync_hermes_pool(account: str, reason: str = "") -> bool:
    """Hermes-Pool nachziehen, wenn die SOT pool.followActive = true hat.

    Bewusst NUR ein Workflow-Dispatch: die SOT bleibt wie sie ist, der
    apply-hermes-Workflow liest sie neu und setzt die Keys in der Reihenfolge
    "aktives Abo zuerst". Ein erneuter apply()-aufruf würde nur einen leeren
    Commit erzeugen. Best-Effort: ein Fehler darf den Konto-Wechsel nie
    zurückrollen.

    Ist pool.onlyActiveWhenExpiring gesetzt UND der Grund des Wechsels ist
    "bald ablaufend", läuft der Dispatch mit single=true — dann schreibt
    apply-hermes.sh NUR den Key des aktiven Abos in den Pool (kein 401 durch
    mitgeschleppte, leere Zusatz-Abonnements).
    """
    try:
        import hermes  # lokaler Import: hermes.py importiert accounts nicht,
                       # aber so bleibt die Modul-Reihenfolge in server.py sicher
        pool = hermes.config().get("pool") or {}
        if not pool.get("followActive"):
            return False
        single = bool(pool.get("onlyActiveWhenExpiring")) and reason.startswith("bald ablaufend")
        github_api.dispatch_workflow(
            hermes.WORKFLOW, inputs={"single": "true" if single else "false"}
        )
        _log(f"Hermes-Pool nachgezogen (aktiv={account}, single={single}, via {hermes.WORKFLOW})")
        return True
    except Exception as e:  # noqa: BLE001
        _log(f"Hermes-Pool-Sync übersprungen: {e}")
        return False


def _apply_policy() -> None:
    """Empfehlung aus dem frischen Lauf umsetzen (mode: auto|notify|off)."""
    rec = STATE.get("recommendation") or {}
    mode = STATE.get("mode") or "off"
    if not rec.get("switchRecommended") or not rec.get("target"):
        return
    target, reason = rec["target"], rec.get("reason") or ""
    if mode == "auto":
        # Cooldown in switch_account verhindert Ping-Pong zwischen zwei Konten.
        res = switch_account(target, reason, force=False, by="auto")
        if not res.get("switched"):
            _log(f"automatischer Wechsel nicht ausgeführt: {res.get('error')}")
    elif mode == "notify":
        if _notify(target, reason):
            _log(f"Empfehlung: Wechsel zu {target} ({reason}) — Modus notify, kein Auto-Wechsel")
    # mode == "off": bewusst nichts tun.


def loop():
    while True:
        try:
            interval = int(refresh(force=True).get("interval") or 300)
        except Exception as e:  # noqa: BLE001
            _log(f"Konten-Abfrage fehlgeschlagen: {e}")
            interval = 300
        # Policy erst NACH dem frischen Refresh — die Empfehlung muss aktuell sein.
        try:
            _apply_policy()
        except Exception as e:  # noqa: BLE001
            _log(f"Policy-Ausführung fehlgeschlagen: {e}")
        _log(f"nächste Prüfung in {interval}s")
        time.sleep(max(30, interval))


def start():
    """Hintergrund-Thread: fragt ab und setzt die Policy um (P2)."""
    threading.Thread(target=loop, daemon=True).start()
