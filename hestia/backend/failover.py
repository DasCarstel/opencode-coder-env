#!/usr/bin/env python3
"""failover.py — Ausfallschutz für die Tier-Modelle.

Prüft die aktiven Tier-Modelle und weicht bei Ausfall auf das hinterlegte
bezahlte Modell aus (Datei .opencode/hestia-fallbacks.json, vom Panel gepflegt):

    {"fast":   {"model": "opencode/<gratis>",   "variant": ""},
     "medium": {"model": "opencode/<gratis>",   "variant": ""}}

Ablauf:
  - Probe: winzige Session je Tier-Modell ("Antworte nur mit OK") über den
    OpenCode-Server; Erfolg = session.execution.succeeded innerhalb des Fensters.
  - Nach HESTIA_FAIL_THRESHOLD Fehlschlägen in Folge UND wenn ein Ausfall-Modell
    hinterlegt ist, wird dieses als Tier-Modell geschrieben (Commit + Rollout) —
    automatisch nur wenn HESTIA_AUTO_FAILOVER=1, sonst per Klick im Panel.
  - Beim Swap werden auch die hergeleiteten Primary-Agents (build/plan) auf das
    neue @medium gezogen; manuell gesetzte Agenten bleiben unangetastet.

Schutz vor Blindflug und Dauer-Neustarts (jede Regel verhindert einen konkreten
Fehlfall aus der Praxis):

  1. Kontingent ≠ Ausfall. Ist das aktive OpenCode-Go-Konto erschöpft oder
     rate-limited (HTTP 429), hilft ein Modellwechsel nicht — dann passiert
     NICHTS und accounts.py übernimmt (Kontowechsel).
  2. Kein No-op-Swap. Steht das Tier schon auf dem Ausweich-Modell, wird nicht
     geschrieben: das erzeugte vorher einen (leeren) Commit und damit einen
     Rollout mit Container-Neustarts, ohne dass sich irgendetwas änderte.
     `github_api.put_files` überspringt identische Inhalte zusätzlich generell.
  3. Ausweich-Modell wird vorher geprobt. Antwortet es selbst nicht, ist der
     Wechsel sinnlos.
  4. Modell-Gedächtnis + Quarantäne (.opencode/hestia-model-health.json): ein
     Modell, das mehrfach in Folge echt ausgefallen ist, wird für
     HESTIA_QUARANTINE_HOURS nicht als Ziel gewählt → kein Hin-und-Her-Flip-Flop.
     Die Datei liegt außerhalb des paths-Filters von rollout.yml, ein Schreiben
     kostet also nie einen Container-Neustart.
  5. Ein Rollout pro Prüflauf. Vorher dispatchte jedes Tier einzeln; bei zwei
     gleichzeitig ausfallenden Tiers liefen zwei Rollouts parallel und starteten
     die Workspaces doppelt neu.

Die Laufzeit-Parameter (enabled/threshold/Intervall/Timeout/Quarantäne) liegen
in der SOT .opencode/hestia-failover.jsonc und werden über
POST /api/failover/settings vom Panel geschrieben; fehlt die Datei, gelten die
HESTIA_*-Defaults. Die SOT hat Vorrang vor der Env-Variable HESTIA_AUTO_FAILOVER.
"""
import json
import os
import threading
import time

import catalog
import github_api
import suggest

TIERS = ("fast", "medium", "heavy")
PRIMARY_AGENTS = ("build", "plan")
FALLBACKS_PATH = os.environ.get("HESTIA_FALLBACKS_PATH", ".opencode/hestia-fallbacks.json")
AGENTS_PATH = os.environ.get("HESTIA_AGENTS_PATH", ".opencode/hestia-primary-agents.json")
FAILOVER_SETTINGS_PATH = os.environ.get(
    "HESTIA_FAILOVER_SETTINGS_PATH", ".opencode/hestia-failover.jsonc"
)
# Modell-Gedächtnis (Fehlschlagsserie + Quarantäne je Tier und je Modell).
# Bewusst NICHT im paths-Filter von rollout.yml → Schreiben löst keinen Rollout aus.
HEALTH_PATH = os.environ.get("HESTIA_HEALTH_PATH", ".opencode/hestia-model-health.json")
PROBE_TIMEOUT = int(os.environ.get("HESTIA_PROBE_TIMEOUT", "35"))
THRESHOLD = int(os.environ.get("HESTIA_FAIL_THRESHOLD", "2"))
INTERVAL = int(os.environ.get("HESTIA_PROBE_INTERVAL", "900"))
QUARANTINE_HOURS = int(os.environ.get("HESTIA_QUARANTINE_HOURS", "6"))
AUTO = os.environ.get("HESTIA_AUTO_FAILOVER", "0") == "1"

# Effort-Rang für die Ableitung der Primary-Agents (plan = höchste Variante).
# Bewusst gespiegelt zu server.VARIANT_RANK — hier nur die für den Failover
# nötige Teilmenge, um keinen Import-Zyklus (server → failover) zu erzeugen.
VARIANT_RANK = {"minimal": 1, "none": 2, "low": 3, "medium": 4, "thinking": 4,
                "high": 5, "xhigh": 6, "max": 7}

# Grenzen für die Panel-Eingaben (POST /api/failover/settings)
THRESHOLD_RANGE = (1, 10)
INTERVAL_RANGE = (60, 86400)
TIMEOUT_RANGE = (5, 300)
QUARANTINE_RANGE = (0, 72)          # 0 = Quarantäne aus
SETTINGS_KEYS = ("enabled", "threshold", "intervalSeconds", "timeoutSeconds",
                 "quarantineHours")


def default_settings() -> dict:
    """Startwerte = heutige Env-Defaults (Fallback, falls die SOT fehlt)."""
    return {
        "enabled": bool(AUTO),
        "threshold": THRESHOLD,
        "intervalSeconds": INTERVAL,
        "timeoutSeconds": PROBE_TIMEOUT,
        "quarantineHours": QUARANTINE_HOURS,
    }


STATE = {
    "autoFailover": AUTO,
    "interval": INTERVAL,
    "threshold": THRESHOLD,
    "timeout": PROBE_TIMEOUT,
    "quarantineHours": QUARANTINE_HOURS,
    "fallbacks": {},
    "current": {},
    "fails": {},
    "lastRun": None,
    "lastResults": {},
    "health": {"tiers": {}, "models": {}},
    "quota": {},
    "events": [],
}
LOCK = threading.Lock()


class SettingsError(Exception):
    """Ungültige Nutzereingabe für die Ausfallschutz-Einstellungen."""


def _log(msg):
    print(f"[hestia-failover] {time.strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)


def _now_iso(ts: float | None = None) -> str:
    """UTC-Zeitstempel (ISO-8601, sekundengenau) — Vergleichbarkeit per String."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if ts is None else ts))


def _clamp_int(value, lo: int, hi: int, fallback: int) -> int:
    """Zahl aus der SOT übernehmen; unbrauchbar → Default (kein Panel-Fehler)."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return fallback
    return n if lo <= n <= hi else fallback


def read_settings() -> dict:
    """Ausfallschutz-SOT lesen und normalisieren.

    Fehlende/kaputte Datei oder GitHub-Fehler → die Env-Defaults, damit das
    Panel immer eine plausible Konfiguration sieht. Liegt die SOT vor, hat ihr
    Feld "enabled" Vorrang vor HESTIA_AUTO_FAILOVER.
    """
    data = {}
    try:
        raw = github_api.get_file(FAILOVER_SETTINGS_PATH)
        if raw:
            import hjsonc
            data = hjsonc.parse(raw) or {}
    except Exception as e:  # noqa: BLE001
        _log(f"settings: {FAILOVER_SETTINGS_PATH} nicht lesbar ({e}) — Defaults")
        data = {}
    if not isinstance(data, dict):
        data = {}
    d = default_settings()
    enabled = d["enabled"]
    if isinstance(data.get("enabled"), bool):
        enabled = data["enabled"]
    return {
        "enabled": enabled,
        "threshold": _clamp_int(data.get("threshold"), *THRESHOLD_RANGE, d["threshold"]),
        "intervalSeconds": _clamp_int(
            data.get("intervalSeconds"), *INTERVAL_RANGE, d["intervalSeconds"]),
        "timeoutSeconds": _clamp_int(
            data.get("timeoutSeconds"), *TIMEOUT_RANGE, d["timeoutSeconds"]),
        "quarantineHours": _clamp_int(
            data.get("quarantineHours"), *QUARANTINE_RANGE, d["quarantineHours"]),
    }


def _require_int(payload: dict, key: str, lo: int, hi: int) -> int:
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise SettingsError(f"'{key}': muss eine ganze Zahl zwischen {lo} und {hi} sein")
    if not (lo <= value <= hi):
        raise SettingsError(f"'{key}': {value} liegt außerhalb {lo}..{hi}")
    return value


def update_settings(payload: dict) -> dict:
    """Panel-Einstellungen validieren, als SOT committen und STATE aktualisieren.

    Wirkt sofort: der Probe-Thread wird live gestartet/gestoppt und liest
    Intervall/Schwelle je Iteration neu — kein Container-Neustart nötig.
    """
    if not isinstance(payload, dict):
        raise SettingsError("Body muss ein JSON-Objekt sein")
    unknown = sorted(set(payload) - set(SETTINGS_KEYS))
    if unknown:
        raise SettingsError(f"unbekannte Felder: {', '.join(unknown)}")

    current = read_settings()
    if "enabled" not in payload:
        raise SettingsError("'enabled' fehlt")
    enabled = payload["enabled"]
    if not isinstance(enabled, bool):
        raise SettingsError("'enabled' muss true oder false sein")
    for key in ("threshold", "intervalSeconds"):
        if key not in payload:
            raise SettingsError(f"'{key}' fehlt")

    settings = {
        "enabled": enabled,
        "threshold": _require_int(payload, "threshold", *THRESHOLD_RANGE),
        "intervalSeconds": _require_int(payload, "intervalSeconds", *INTERVAL_RANGE),
        # optional: ohne Feld bleibt der aktuelle Wert (SOT, sonst Default)
        "timeoutSeconds": (
            _require_int(payload, "timeoutSeconds", *TIMEOUT_RANGE)
            if "timeoutSeconds" in payload else current["timeoutSeconds"]
        ),
        "quarantineHours": (
            _require_int(payload, "quarantineHours", *QUARANTINE_RANGE)
            if "quarantineHours" in payload else current["quarantineHours"]
        ),
    }

    header = (
        "// Ausfallschutz-Einstellungen (SOT des Hestia-Panels).\n"
        "// Geschrieben über POST /api/failover/settings, gelesen von hestia/backend/failover.py.\n"
        "// quarantineHours: 0 schaltet die Modell-Quarantäne ab.\n"
    )
    text = header + json.dumps(settings, indent=2, ensure_ascii=False) + "\n"
    commit = github_api.put_files(
        {FAILOVER_SETTINGS_PATH: text}, "hestia: Ausfallschutz-Einstellungen aktualisiert")

    with LOCK:
        STATE["autoFailover"] = settings["enabled"]
        STATE["threshold"] = settings["threshold"]
        STATE["interval"] = settings["intervalSeconds"]
        STATE["timeout"] = settings["timeoutSeconds"]
        STATE["quarantineHours"] = settings["quarantineHours"]
    # Live-Umschaltung: Probe-Thread starten/stoppen, ohne Container-Neustart.
    _apply_loop_state(settings["enabled"])
    _log(
        f"settings: enabled={settings['enabled']} threshold={settings['threshold']} "
        f"intervall={settings['intervalSeconds']}s timeout={settings['timeoutSeconds']}s "
        f"quarantäne={settings['quarantineHours']}h "
        f"(Commit {str((commit or {}).get('sha', ''))[:8]})"
    )
    return {"saved": True, "settings": settings, "commit": commit}


def read_fallbacks() -> dict:
    try:
        raw = github_api.get_file(FALLBACKS_PATH)
        data = json.loads(raw) if raw else {}
    except Exception:
        data = {}
    return {t: (data.get(t) or None) for t in TIERS}


# ── Modell-Gedächtnis (welches Modell macht gerade Probleme — und bis wann?) ───

def read_health() -> dict:
    """Gedächtnis lesen: {"tiers": {...}, "models": {...}}.

    tiers[tier]  = Zustand des aktuell an diesem Tier gesetzten Modells
    models[label] = Historie je Modell (Fehlschlagsserie, Quarantäne) — gilt
                    unabhängig davon, an welchem Tier das Modell gerade hängt.
    Fehlt/kaputt → leeres Gedächtnis (nie ein harter Fehler).
    """
    data = {}
    try:
        raw = github_api.get_file(HEALTH_PATH)
        if raw:
            data = json.loads(raw)
    except Exception as e:  # noqa: BLE001
        _log(f"health: {HEALTH_PATH} nicht lesbar ({e})")
        data = {}
    if not isinstance(data, dict):
        data = {}
    tiers = data.get("tiers") if isinstance(data.get("tiers"), dict) else {}
    models = data.get("models") if isinstance(data.get("models"), dict) else {}
    return {"tiers": dict(tiers), "models": dict(models)}


def _health_sig(health: dict) -> str:
    """Signatur ohne Zeitstempel-Rauschen: nur echte Zustandsänderungen zählen."""
    return json.dumps(health, sort_keys=True, ensure_ascii=False)


def write_health(health: dict, old_sig: str = "") -> bool:
    """Gedächtnis persistieren — nur wenn sich inhaltlich etwas geändert hat."""
    if _health_sig(health) == old_sig:
        return False
    text = json.dumps({"updatedAt": _now_iso(), **health}, indent=2, ensure_ascii=False) + "\n"
    res = github_api.put_files({HEALTH_PATH: text}, "hestia: Modell-Gedächtnis (Ausfallschutz)")
    return not res.get("unchanged")


def _quarantined_until(health: dict, label: str) -> str:
    """Bis wann ist dieses Modell gesperrt? "" = nicht gesperrt."""
    entry = (health.get("models") or {}).get(label) or {}
    until = str(entry.get("quarantineUntil") or "")
    return until if until and until > _now_iso() else ""


def quota_state() -> dict:
    """Ist das aktive OpenCode-Go-Konto erschöpft/rate-limited?

    Kontingent-Erschöpfung ist KEIN Modellausfall — ein Modellwechsel heilt sie
    nicht, er kostet nur einen Rollout. In diesem Fall hält der Ausfallschutz
    still und überlässt das Feld accounts.py (Kontowechsel).
    """
    try:
        import accounts
        st = accounts.state() or {}
    except Exception as e:  # noqa: BLE001
        return {"blocked": False, "reason": f"Quota-Check nicht möglich ({e})"}
    accts = [a for a in (st.get("accounts") or []) if isinstance(a, dict)]
    if not accts:
        return {"blocked": False, "reason": "keine Kontodaten"}
    active_id = st.get("active")
    active = next((a for a in accts if a.get("id") == active_id), None)
    if active is not None and accounts._exhausted(active):
        return {"blocked": True, "active": active_id,
                "reason": f"Konto {active_id or '?'} erschöpft/rate-limited"}
    if all(accounts._exhausted(a) for a in accts):
        return {"blocked": True, "active": active_id, "reason": "alle Konten erschöpft/rate-limited"}
    return {"blocked": False, "active": active_id, "reason": ""}


def probe(model_ref: str, variant: str = "") -> dict:
    """Winzige Test-Session auf dem Modell. Erfolg = execution.succeeded."""
    if not model_ref:
        return {"ok": False, "detail": "kein Modell konfiguriert"}
    ok, info = catalog.usable(model_ref)
    if not ok:
        return {"ok": False, "detail": f"{info} (nicht im Katalog → sofortiger Ausfall)"}
    sstatus, sres = suggest._req("POST", f"{suggest.OC_SERVER_URL}/api/session",
                                 {"location": {"directory": suggest.OC_SUGGEST_DIR},
                                  "title": "Hestia Ausfall-Probe"})
    if sstatus not in (200, 201):
        return {"ok": False, "detail": f"Session-Anlegen fehlgeschlagen ({sstatus})"}
    sid = (sres.get("data") or sres).get("id")
    try:
        provider, _, mid = model_ref.partition("/")
        body = {"providerID": provider, "id": mid}
        if variant:
            body["variant"] = variant
        mst, _ = suggest._req("POST", f"{suggest.OC_SERVER_URL}/api/session/{sid}/model", {"model": body})
        if mst not in (200, 201, 204):
            return {"ok": False, "detail": f"Modell nicht verfügbar (switch {mst})"}
        suggest._req("POST", f"{suggest.OC_SERVER_URL}/api/session/{sid}/prompt",
                     {"text": "Antworte nur mit OK"})
        result = suggest._event_watch(sid, deadline=time.time() + PROBE_TIMEOUT,
                                      stall_timeout=PROBE_TIMEOUT)
        ok = result == "succeeded"
        return {"ok": ok, "detail": result}
    finally:
        suggest._delete_session(sid)


def run_probes(apply_backup: bool = False) -> dict:
    """Alle Tier-Modelle prüfen; optional beim Ausfall auf das Backup wechseln."""
    cfg = github_api.get_overrides()["config"]
    preset = (cfg.get("presets") or {}).get(os.environ.get("HESTIA_PRESET_PROVIDER", "hestia"), {})
    fallbacks = read_fallbacks()
    health = read_health()
    old_sig = _health_sig(health)
    with LOCK:
        quarantine_h = int(STATE.get("quarantineHours") or QUARANTINE_HOURS)
    quota = quota_state()
    results, swaps, state_updates, swapped_tiers = {}, {}, {}, []
    for tier in TIERS:
        entry = preset.get(tier) or {}
        ref = (entry.get("model") or "").strip()
        var = (entry.get("variant") or "").strip()
        if not ref:
            continue
        res = probe(ref, var)
        label_cur = ref + (f"#{var}" if var else "")
        results[tier] = {"model": label_cur, **res}
        fails = 0 if res["ok"] else (STATE["fails"].get(tier, 0) + 1)
        state_updates[tier] = fails

        # ── Gedächtnis pflegen ────────────────────────────────────────────
        tier_mem = dict(health["tiers"].get(tier) or {})
        model_mem = dict(health["models"].get(label_cur) or {})
        if res["ok"]:
            if (model_mem.get("failStreak") or model_mem.get("quarantineUntil")
                    or tier_mem.get("failStreak") or tier_mem.get("lastFailureKind")):
                tier_mem = {"model": label_cur, "ok": True, "failStreak": 0,
                            "lastOkAt": _now_iso()}
                model_mem = {"model": label_cur, "failStreak": 0,
                             "quarantineUntil": "", "lastOkAt": _now_iso()}
            else:
                # gesund und ohne Vorgeschichte: nichts zu schreiben
                tier_mem = {"model": label_cur, "ok": True, "failStreak": 0}
                model_mem = {"model": label_cur, "failStreak": 0, "quarantineUntil": ""}
        else:
            kind = "quota" if quota["blocked"] else "outage"
            streak = int(model_mem.get("failStreak") or 0) + 1
            model_mem = dict(model_mem)
            model_mem.update({"model": label_cur, "failStreak": streak,
                              "lastFailAt": _now_iso(), "lastFailureKind": kind})
            if kind == "outage" and streak >= THRESHOLD and quarantine_h > 0:
                model_mem["quarantineUntil"] = _now_iso(time.time() + quarantine_h * 3600)
            tier_mem = {"model": label_cur, "ok": False, "failStreak": streak,
                        "lastFailAt": model_mem["lastFailAt"], "lastFailureKind": kind,
                        "quarantineUntil": model_mem.get("quarantineUntil") or ""}
        health["tiers"][tier] = tier_mem
        health["models"][label_cur] = model_mem

        # ── Wechselentscheidung ───────────────────────────────────────────
        fb = fallbacks.get(tier) or {}
        fb_ref = (fb.get("model") or "").strip()
        fb_var = (fb.get("variant") or "").strip()
        fb_label = fb_ref + (f"#{fb_var}" if fb_var else "")
        if res["ok"] or not fb_ref or fails < THRESHOLD:
            continue
        if not (apply_backup or _auto_enabled()):
            swaps[tier] = f"bereit (Ausfall erkannt, Auto-Failover aus): {fb_label}"
            continue
        if quota["blocked"]:
            swaps[tier] = (f"übersprungen — {quota['reason']}: Kontingent, kein Modellausfall "
                           f"(Kontowechsel ist accounts.py)")
            continue
        if fb_ref == ref and fb_var == var:
            swaps[tier] = f"übersprungen — {tier} läuft bereits auf {fb_label}"
            state_updates[tier] = 0
            continue
        q_until = _quarantined_until(health, fb_label)
        if q_until:
            swaps[tier] = f"übersprungen — {fb_label} in Quarantäne bis {q_until}"
            continue
        fb_res = probe(fb_ref, fb_var)
        if not fb_res["ok"]:
            swaps[tier] = (f"übersprungen — Ausweich-Modell {fb_label} antwortet ebenfalls "
                           f"nicht ({fb_res['detail']})")
            continue
        try:
            res_swap = _swap_tier(tier, fb)
            state_updates[tier] = 0
            if res_swap.get("unchanged"):
                swaps[tier] = f"übersprungen — {fb_label} ist bereits gesetzt (kein Commit)"
            else:
                swaps[tier] = fb_label
                swapped_tiers.append(tier)
            health["tiers"][tier] = {"model": fb_label, "ok": True, "failStreak": 0,
                                     "lastOkAt": _now_iso(), "swappedAt": _now_iso()}
            health["models"][fb_label] = {"model": fb_label, "failStreak": 0,
                                          "quarantineUntil": "", "lastOkAt": _now_iso()}
        except Exception as e:  # noqa: BLE001
            swaps[tier] = f"FEHLER: {e}"

    health_committed = False
    try:
        health_committed = write_health(health, old_sig)
    except Exception as e:  # noqa: BLE001
        _log(f"Gedächtnis nicht schreibbar: {e}")
    if swapped_tiers:
        # EIN Rollout für alle Wechsel dieses Laufs (vorher: ein Dispatch je Tier
        # → zwei parallele Rollouts mit doppelten Container-Neustarts).
        _dispatch_rollout(swapped_tiers)
    with LOCK:
        STATE["fails"].update(state_updates)
        STATE["fallbacks"] = fallbacks
        STATE["current"] = {t: (preset.get(t) or {}).get("model") for t in TIERS}
        STATE["lastRun"] = time.time()
        STATE["lastResults"] = results
        STATE["health"] = health
        STATE["quota"] = quota
        if swaps:
            STATE["events"] = ([{"at": time.time(), "swaps": swaps}] + STATE["events"])[:10]
            _log(f"Ausweichungen: {swaps}")
    return {"results": results, "swaps": swaps, "health": health, "quota": quota,
            "healthCommitted": health_committed, "rolledOut": swapped_tiers}


def _dispatch_rollout(tiers: list) -> bool:
    """Rollout genau einmal anstoßen (Push auf die Overrides feuert ihn nicht,
    weil der Commit idempotent-geschützt ggf. gar keine Änderung enthält)."""
    try:
        github_api.dispatch_workflow(os.environ.get("ROLLOUT_WORKFLOW", "rollout.yml"))
        _log(f"Rollout ausgelöst ({', '.join(tiers)})")
        return True
    except Exception as e:  # noqa: BLE001
        _log(f"Rollout-Dispatch fehlgeschlagen: {e}")
        return False


def _highest_variant(model_ref: str) -> str:
    """Höchste Effort-Variante eines Modells laut Live-Katalog ("" = keine)."""
    m = catalog.find_model(model_ref)
    if not m:
        return ""
    ranked = sorted([v for v in (m.get("variants") or []) if v in VARIANT_RANK],
                    key=lambda v: VARIANT_RANK[v])
    return ranked[-1] if ranked else ""


def _derived_agents_json(new_preset: dict):
    """Primary-Agents (build/plan) an das neue @medium anpassen.

    Spiegelt server.effective_agents()/agents_to_display(): build = @medium
    (Standardvariante), plan = @medium (höchste Variante). Manuell gewählte
    Agenten (source == "manual") bleiben unangetastet. Rückgabe: neuer JSON-Text
    oder None, wenn keine Änderung / kein @medium vorhanden.

    Ohne diese Synchronisierung blieben build/plan nach einem Auto-Failover auf
    dem (ausgefallenen) Gratis-Modell des alten @medium hängen — der Failover
    wirkte dann nur für die Subagent-Tiers, nicht für die Chat-Agenten.
    """
    medium = (new_preset or {}).get("medium") or {}
    med_ref = (medium.get("model") or "").strip()
    if not med_ref:
        return None
    try:
        raw = github_api.get_file(AGENTS_PATH)
        data = json.loads(raw) if raw else {}
    except Exception:  # noqa: BLE001
        data = {}
    if not isinstance(data, dict):
        data = {}
    out, changed = {}, False
    for agent in PRIMARY_AGENTS:
        entry = data.get(agent) or {}
        model = (entry.get("model") or "").strip()
        source = entry.get("source")
        manual = (source == "manual") or (bool(model) and not source)
        if manual:
            out[agent] = entry
            continue
        variant = (medium.get("variant") or "") if agent == "build" else _highest_variant(med_ref)
        new_entry = {"model": med_ref, "variant": variant or "", "source": "derived"}
        out[agent] = new_entry
        if (entry.get("model") != new_entry["model"]
                or (entry.get("variant") or "") != new_entry["variant"]
                or source != "derived"):
            changed = True
    if not changed:
        return None
    return json.dumps(out, indent=2, ensure_ascii=False) + "\n"


def _swap_tier(tier: str, backup: dict):
    """Ausfall-Modell als Tier-Modell schreiben (nur Commit — Rollout macht der Aufrufer).

    Schreibt nichts, wenn das Tier inhaltlich schon auf dem Ausweich-Modell
    steht (github_api.put_files meldet das als "unchanged"): ein leerer Commit
    löste sonst einen Rollout mit Container-Neustarts ohne jede Änderung aus.
    """
    # aktuelle Werte frisch lesen und nur das betroffene Tier ersetzen
    cfg = github_api.get_overrides()["config"]
    preset = (cfg.get("presets") or {}).get(os.environ.get("HESTIA_PRESET_PROVIDER", "hestia"), {})
    new_preset = dict(preset)
    entry = dict(new_preset.get(tier) or {})
    entry["model"] = backup["model"]
    if backup.get("variant"):
        entry["variant"] = backup["variant"]
    else:
        entry.pop("variant", None)
    new_preset[tier] = entry
    cfg = dict(cfg)
    cfg["presets"] = dict(cfg.get("presets") or {})
    cfg["presets"][os.environ.get("HESTIA_PRESET_PROVIDER", "hestia")] = new_preset

    import hjsonc
    jsonc = hjsonc.serialize(cfg)
    label = backup["model"] + (f"#{backup['variant']}" if backup.get("variant") else "")
    # Primary-Agents (hergeleitet aus @medium) im selben Commit mitziehen.
    files = {github_api.OVERRIDES_PATH: jsonc}
    agents_json = _derived_agents_json(new_preset)
    if agents_json:
        files[AGENTS_PATH] = agents_json
    res = github_api.put_files(files, f"hestia: Ausfallschutz — {tier} auf {label} gewechselt")
    if res.get("unchanged"):
        _log(f"{tier}: schon auf {label} — kein Commit, kein Rollout")
        return res
    _log(f"{tier} -> {label} (Commit {str(res.get('sha', ''))[:8]})"
         + (" · Primary-Agents angepasst" if agents_json else ""))
    return res


# Probe-Thread-Verwaltung: Live-Start/-Stopp bei Settings-Änderung.
_LOOP_THREAD = None
_STOP = threading.Event()


def _auto_enabled() -> bool:
    with LOCK:
        return bool(STATE["autoFailover"])


def _apply_loop_state(enabled: bool):
    """Probe-Thread live starten/stoppen (ohne Container-Neustart)."""
    global _LOOP_THREAD, _STOP
    if not enabled:
        _STOP.set()
        _log("Auto-Ausfallschutz deaktiviert (Probe-Thread gestoppt)")
        return
    if _LOOP_THREAD and _LOOP_THREAD.is_alive():
        return
    _STOP = threading.Event()
    _LOOP_THREAD = threading.Thread(target=loop, args=(_STOP,), daemon=True)
    _LOOP_THREAD.start()


def loop(stop_event: threading.Event):
    with LOCK:
        _log(f"Auto-Ausfallschutz aktiv (Intervall {STATE['interval']}s, "
             f"Schwelle {STATE['threshold']}, Timeout {STATE['timeout']}s, "
             f"Quarantäne {STATE['quarantineHours']}h)")
    while not stop_event.is_set():
        if _auto_enabled():
            try:
                run_probes()
            except Exception as e:  # noqa: BLE001
                _log(f"Probe fehlgeschlagen: {e}")
        # Intervall je Iteration neu lesen → Panel-Änderung wirkt ohne Neustart.
        with LOCK:
            wait = int(STATE["interval"])
        stop_event.wait(wait)
    _log("Auto-Ausfallschutz deaktiviert — Probe-Thread beendet")


def start():
    try:
        settings = read_settings()
        with LOCK:
            STATE["autoFailover"] = settings["enabled"]
            STATE["threshold"] = settings["threshold"]
            STATE["interval"] = settings["intervalSeconds"]
            STATE["timeout"] = settings["timeoutSeconds"]
            STATE["quarantineHours"] = settings["quarantineHours"]
            STATE["fallbacks"] = read_fallbacks()
    except Exception:
        pass
    try:
        with LOCK:
            STATE["health"] = read_health()
    except Exception:
        pass
    if _auto_enabled():
        _apply_loop_state(True)
    else:
        _log("Auto-Ausfallschutz inaktiv (SOT enabled=false) — Proben nur manuell")
