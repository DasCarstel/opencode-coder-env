#!/usr/bin/env python3
"""suggest.py — KI-Modellvorschlag über eine OpenCode-Session (REST).

Muster wie im Hermes-'space-matrix'-Bericht dokumentiert (Server v2.0.24):
  POST /api/session                → Session anlegen (richtiger Workspace)
  POST /api/session/<sid>/prompt   → Auftrag feuern (kehrt sofort zurück)
  GET  /api/session/<sid>/message  → Antwort lesen
  GET  /api/session/active         → Ende-Erkennung (sid fehlt ⇒ fertig)
"""
import base64
import hashlib
import json
import os
import threading
import time
import urllib.request
import urllib.error

import catalog
from catalog import fetch_models, usable

OC_SERVER_URL = os.environ.get("OC_SERVER_URL", "http://localhost:49374").rstrip("/")
OC_SERVER_USER = os.environ.get("OC_SERVER_USER", "opencode")
OC_SERVER_PASSWORD = os.environ.get("OC_SERVER_PASSWORD", "")
OC_SUGGEST_DIR = os.environ.get(
    "OC_SUGGEST_DIR",
    "/home/carstenmueller2002/opencode-coder-env/workspaces/infrastructure/server-management",
)
# Analyse-Modell: standardmäßig das aktuelle @heavy-Modell aus den Overrides.
# Override per OC_SUGGEST_MODEL ("provider/model#variant"); leer = Tier-Heavy.
OC_SUGGEST_MODEL = os.environ.get("OC_SUGGEST_MODEL", "").strip()
PRESET = os.environ.get("HESTIA_PRESET_PROVIDER", "hestia")
PROVIDERS = [p.strip() for p in
             os.environ.get("HESTIA_PROVIDERS", PRESET).split(",") if p.strip()]
POLL_INTERVAL = 5.0
POLL_TIMEOUT = float(os.environ.get("OC_SUGGEST_TIMEOUT", "150"))
STALL_TIMEOUT = float(os.environ.get("OC_SUGGEST_STALL_TIMEOUT", "45"))
TRIES = int(os.environ.get("OC_SUGGEST_TRIES", "1"))
# Analyse-Sessions nach dem Lauf automatisch löschen (1 = zum Debuggen behalten)
KEEP_SESSIONS = os.environ.get("OC_SUGGEST_KEEP_SESSIONS", "0") == "1"


def _headers():
    tok = base64.b64encode(f"{OC_SERVER_USER}:{OC_SERVER_PASSWORD}".encode()).decode()
    return {"Authorization": f"Basic {tok}", "Content-Type": "application/json"}


def _req(method: str, url: str, body: dict | None = None, timeout: int = 30):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=_headers(), method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read().decode() or "{}"
            return resp.status, json.loads(payload)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}


def _fmt(value) -> str:
    """Benchmark-Wert für den Prompt — None wird als '—' (unbekannt) dargestellt."""
    if value is None:
        return "—"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _sources_name(key: str) -> str:
    """Quell-Key → Anzeigename (für die Prompt-Notiz)."""
    return {
        "aa": "Artificial Analysis",
        "artificialanalysis": "Artificial Analysis",
        "or": "OpenRouter",
        "openrouter": "OpenRouter",
    }.get(str(key or "").lower(), str(key))


def _benchmarks_block(models: list) -> tuple:
    """Benchmark-Werte je Kandidat-Modell (aus dem lokalen Benchmark-Cache).

    Spalten = die in der SOT konfigurierten Felder, beschriftet über
    `fieldLabels` (nicht mehr über fest verdrahtete Namen). Quellen-Angabe:
    Artificial Analysis (primär) / OpenRouter (Fallback).

    Liefert (zeilen_zusatz, anweisung) — ohne Cache/Fehler bleibt die Tabelle
    unverändert und der Prompt funktioniert wie bisher weiter.
    """
    try:
        import benchmarks as bench_mod
        settings = bench_mod.read_settings()
        fields = settings["fields"]
        entries = bench_mod.for_refs([m["ref"] for m in models], fields)
        st = bench_mod.state()
        labels = (st.get("fieldLabels") or {}) or bench_mod.field_labels(fields)
    except Exception as e:  # noqa: BLE001 — Benchmarks dürfen nie den Lauf kippen
        _log(f"benchmarks nicht verfügbar: {e}")
        return {}, None
    cols = [(f, labels.get(f, f)) for f in fields if f in bench_mod.ALL_FIELDS]
    if not cols:
        cols = [("coding_index", "Coding"), ("agentic_index", "Agentic-Index"),
                ("intelligence_index", "Intelligenz"), ("code_elo", "Code-Elo")]
    header = " | ".join(label for _, label in cols)
    unknown = " | ".join(["—"] * len(cols))
    rows = {}
    # Effort-Kurve je Modell: AA fuehrt jede Stufe als eigenen Datensatz. Der Preis
    # ist ueber alle Stufen gleich — es entscheiden Intelligenz und Antwortzeit.
    effort = {}
    for m in models:
        e = entries.get(m["ref"]) or {}
        curve = e.get(bench_mod.VARIANT_FIELD) or {}
        parts = []
        for eff in bench_mod.EFFORT_SUFFIXES:
            c = curve.get(eff)
            if not isinstance(c, dict):
                continue
            iq = _fmt(c.get("intelligence_index"))
            tt = c.get("ttft")
            parts.append(f"{eff} {iq}/{_fmt(tt)}s" if tt is not None else f"{eff} {iq}")
        if parts:
            effort[m["ref"]] = " · ".join(parts)
    for m in models:
        e = entries.get(m["ref"])
        if not e:
            continue
        rows[m["ref"]] = " | ".join(_fmt(e.get(f)) for f, _ in cols)
    if not rows and not effort:
        return {}, None, {}
    sources = st.get("sources") or {}
    ok = [k for k, v in sources.items() if isinstance(v, dict) and v.get("ok")]
    source = " + ".join(_sources_name(k) for k in ok) or st.get("source") or "openrouter"
    note = (
        f"(Quelle {source} — {st.get('attribution')}; Stand {st.get('ageDays')} Tage alt, "
        f"{st.get('count')} Modelle)"
    )
    instruction = (
        "BENCHMARK-REGEL (wichtig): Die Benchmarks in der Modelltabelle sind die EINZIGEN\n"
        "Werte, die du verwenden darfst. Verwende diese Zahlen AUSSCHLIESSLICH so, wie sie\n"
        "dort stehen — rufe KEINE eigenen Benchmarks ab, schätze sie nicht und ergänze sie\n"
        "nicht aus dem Gedächtnis oder aus anderen Quellen. Ein '—' bedeutet 'unbekannt':\n"
        "dieses Modell hat dazu keine Daten — schätze den Wert NICHT und leite nichts daraus ab.\n"
        "Fehlt zu einem Modell die ganze Benchmark-Spalte ('—'), darfst du Qualitätsaussagen\n"
        "für dieses Modell nur aus Preis, Varianten und Modellnamen begründen.\n"
        f"Die Werte stammen aus {st.get('attribution') or 'den oben genannten Quellen'} "
        "(Attribution Pflicht) — gib sie als solche weiter.\n"
        "EFFORT-REGEL (wichtig): Steht hinter einem Modell eine Zeile "
        "'Effort (IQ/Antwortzeit)', so ist der Preis über ALLE Stufen identisch — es "
        "entscheidet allein Intelligenz gegen Antwortzeit. Wähle die Stufe, die die "
        "Intelligenz nahe ans Maximum bringt, ohne unnötige Wartezeit zu erzeugen: suche "
        "den Knick, also den letzten großen IQ-Sprung VOR einem Sprung in der Antwortzeit. "
        "'max' hat die höchste Intelligenz, aber meist eine sehr lange Denkzeit; 'low' und "
        "'medium' liegen deutlich darunter, ohne billiger zu sein. Fehlt die Zeile, hat "
        "die Quelle für dieses Modell keine Stufen gemessen — dann gib 'variant' leer an."
    )
    return rows, (header, instruction, note, unknown), effort


def _fmt_price(p: dict) -> str:
    """Preis eines Tarifs als Kurztext."""
    return (f"in ${p.get('input')}/M, out ${p.get('output')}/M, "
            f"cache r ${p.get('cacheRead')}/w ${p.get('cacheWrite')}")


def _price_line(m: dict) -> str:
    """Preis + Go-Budget eines Modells für den Katalog im Prompt.

    Zeigt den GÜLTIGEN Tarif zuerst (die Kontextstufe, in der unsere Läufe
    praktisch immer liegen), darunter den billigeren Grundtarif — und getrennt
    davon das Monatsbudget dieses Modells im Go-Abo. Preis und Budget werden
    bewusst NICHT zu einer Kennzahl verrechnet (siehe PRO-302-Notiz
    "Hestia - Preise, Kontextstufen und Go-Budgets").
    """
    cost = m.get("cost") or {}
    tiers = m.get("costTiers") or []
    eff = m.get("costEffective") or cost
    ref = m.get("ref") or ""
    model_id = m.get("modelID") or ""
    parts = []

    if not cost.get("input") and not cost.get("output"):
        parts.append("KOSTENLOS")
    elif tiers:
        parts.append(f"PREIS GÜLTIG ab {eff.get('from')} Kontexttoken: {_fmt_price(eff)}")
        parts.append(f"Preis nur darunter: {_fmt_price(cost)}")
    else:
        parts.append(f"Preis: {_fmt_price(eff)}")

    try:
        import gobudget
        b = gobudget.budget_for(ref)
        if b is None:
            parts.append("kein Go-Abo-Budget (zählt nicht ins Abo-Limit)")
        elif b.get("unlimited"):
            parts.append("Budget unbegrenzt (Go-Abo, Gratis-Modell)")
        else:
            parts.append(f"Budget ${b['month']:g}/Monat (5-h-Fenster ${b['h5']:g})")
    except Exception:  # noqa: BLE001 — fehlende Budgets dürfen den Prompt nicht kippen
        pass

    try:
        import gobudget
        rule = gobudget.peak_rule(model_id, m.get("providerID"))
        if rule:
            spans = "/".join(rule.get("windows") or [])
            jetzt = ("PEAK (doppelter Preis)" if gobudget.is_peak(model_id, None, m.get("providerID"))
                     else "aktuell Off-Peak")
            parts.append(f"Peak {spans} UTC Mo-Fr = {rule.get('factor', 2):g}x — {jetzt}")
    except Exception:  # noqa: BLE001
        pass

    return " | " + " | ".join(parts) if parts else ""


def _build_prompt() -> str:
    models = [m for m in fetch_models()
              if m["providerID"] in PROVIDERS and m["status"] == "active"]
    bench_rows, bench_meta, bench_effort = _benchmarks_block(models)
    bench_header = ""
    if bench_meta:
        bench_header = f" | Bench ({bench_meta[0]})"
    lines = []
    for m in models:
        v = ",".join(v for v in m["variants"] if v) or "-"
        lim = m.get("limit") or {}
        line = (
            f"- {m['ref']} | {m['name']}"
            + _price_line(m)
            + f" | ctx {lim.get('context', '?')} | variants [{v}]"
        )
        if bench_header:
            vals = bench_rows.get(m["ref"])
            line += bench_header + (f" {vals}" if vals else f" {bench_meta[3]}")
        if bench_effort.get(m["ref"]):
            line += f" | Effort (IQ/1. Antwort): {bench_effort[m['ref']]}"
        lines.append(line)
    catalog = "\n".join(lines)
    from github_api import get_overrides
    try:
        current = get_overrides()["config"].get("presets", {}).get(PRESET, {})
        current_str = json.dumps(
            {k: current[k].get("model") for k in ("fast", "medium", "heavy") if k in current},
            ensure_ascii=False,
        )
    except Exception:
        current_str = "unbekannt"
    # Budget-Lage der OpenCode-Go-Konten (P3): das AKTIVE Abo trägt alle opencode-go-Aufrufe.
    try:
        import accounts as accounts_mod
        acc = accounts_mod.state()
        blines = []
        for a in (acc.get("accounts") or []):
            label = a.get("label") or a.get("id")
            if not a.get("ok"):
                blines.append(f"- {label}: {a.get('error') or 'unbekannt'}")
                continue
            wins = ", ".join(
                f"{k} {v.get('free')}% frei" for k, v in (a.get("windows") or {}).items()
            )
            blines.append(
                f"- {label}{' (AKTIV)' if a.get('isActive') else ''}"
                f" [Prio {a.get('priority')}]: {wins}"
            )
        budget = "\n".join(blines) or "- unbekannt"
    except Exception:
        budget = "- unbekannt"
    bench_instruction = bench_meta[1] + ("\n" + bench_meta[2]) if bench_meta else \
        "BENCHMARK-REGEL: Für diesen Lauf liegen keine Benchmark-Daten vor — bewerte die\nModelle ausschließlich aus Preis, Varianten und Modellnamen und rate KEINE Kennzahlen."
    # Verbrauch je Modell (Ampel) aus den eigenen Sitzungen — Quelle usage.py.
    try:
        import usage as usage_mod
        verbrauch = usage_mod.prompt_block([m["ref"] for m in models])
    except Exception:  # noqa: BLE001 — fehlende Verbrauchsdaten kippen keinen Lauf
        verbrauch = ""
    return f"""Du bist Modell-Stratege für ein Homelab (Coder-Workspaces, OpenCode Agenten-Fleet).
Wähle aus dem Live-Katalog unten die beste 3-Tier-Anordnung für das Router-Plugin
(alle drei Tier müssen Models DIESER Liste sein; erlaubte Provider: {", ".join(PROVIDERS)}):
Kostenlose Modelle (mit "KOSTENLOS" markiert) sind ausdrücklich zugelassen.

ROLLEN (Task-Taxonomie des Routers):
- @fast (costRatio 1): nur-lesende Exploration, Suche, grep — sehr viele kleine Dispatches, Kostentreiber #1.
- @medium (costRatio 5): Implementation, Refactoring, Tests — der Arbeitspferd-Tier.
- @heavy (costRatio 12): Architektur, komplexes Debugging (nach 2+ Fehlschlägen), Security-Review — selten, aber stark.

OPENCODE-KONTEXT — so werden die Modelle tatsächlich eingesetzt:
- @fast/@medium/@heavy sind Router-Subagents für DELEGIERTE Aufgaben (Tier-Routing).
- Das Medium-Modell hat zusätzlich eine Doppelrolle als Primary-Agent:
  * `build` (Standard-Coding-Agent, mit dem der Nutzer interaktiv arbeitet) läuft auf **Medium mit Standardvariante**
    — es antwortet auf JEDE Nachricht: Latenz/Preis pro Nachricht zählen, es muss aber auch selbst implementieren können.
  * `plan` (Nur-Lese-Planung) läuft auf **Medium mit der HÖCHSTEN Effort-Variante** — hier zählt Reasoning-Qualität,
    Geschwindigkeit ist zweitrangig; ein Modell ohne sinnvoll abgestufte Varianten (low..max) ist dafür unattraktiv.
- Eingebaute Subagents ohne eigenes Modell (`general`, `explore`) erben das Session-Modell (= Medium).
- Effort-Varianten (low/medium/high/xhigh/max) steuern das Reasoning-Budget.
=> Bewerte Medium deshalb DOPPELT: als Arbeitspferd für Implementation UND als interaktives Chat-/Plan-Modell.

AKTUELLE WERTE: {current_str}

BUDGET-STATUS der OpenCode-Go-Konten (je Fenster "% frei"; das AKTIVE Konto trägt alle
opencode-go-Aufrufe — @fast/@medium/@heavy laufen über dieses Abo):
{budget}

MODELL-VERBRAUCH (eigene Sitzungen, rollierend über 5 h / Woche / 30 Tage; Prozent = Anteil
des jeweiligen Fensterlimits DIESES Modells. "GESPERRT" heißt: das Modell nimmt im laufenden
Fenster nichts mehr an, bis es abläuft; "knapp" heißt: nur noch wenig Luft):
{verbrauch or "- keine Verbrauchsdaten vorhanden"}

MODELL-KATALOG (nur erlaubte Provider, alle active; Preise = USD pro Mio Tokens).
Zu den PREIS-Angaben: "PREIS GÜLTIG ab <n> Kontexttoken" ist der Tarif, der bei unserem
Nutzungsprofil abgerechnet wird — unsere Läufe liegen fast immer weit über dieser Grenze
(viel Verlauf und MCP-Tools). "Preis nur darunter" gilt ausschließlich für kleine Kontexte
und ist die Ausnahme. Teuer sind vor allem die Cache-Writes (cache w), nicht Input/Output.
Zu den BUDGET-Angaben: das ist das MONATSLIMIT des Go-Abos für genau dieses Modell; das
5-h-Fenster ist nur 20 % davon (Woche 50 %). "kein Go-Abo-Budget" heißt: läuft nicht über
das Abo und belastet es deshalb nicht.
{catalog}

{bench_instruction}

AUFGABE: Wähle je Tier das kosten-günstigste Modell, das die Rolle zuverlässig bewältigt
(cheap-but-capable statt Kompromiss-frei). Berücksichtige: Tool-Fähigkeit ist Pflicht;
@heavy braucht starkes Reasoning (max-effort Variante bevorzugen wenn vorhanden);
wähle Varianten aus der Liste. Wenn sich zwischen zwei Modellen der Preis kaum
unterscheidet, nimm das neuere/stärkere. Nutze für Qualitätsvergleiche die Bench-Spalte
der Modelltabelle (nicht-preisliche Argumente). Für @medium gilt zusätzlich die Doppelrolle
(build/plan interaktiv) aus dem OpenCode-Kontext — träge Standardvarianten abwerten.
ZU KOSTENLOSEN Modellen ("KOSTENLOS" in der Liste): sehr attraktiv für @fast (viele kleine
Lesedispatches) und zum Ausprobieren — aber sie sind volatil (Preview/Community, können
verschwinden oder gedrosselt werden) und oft ohne Varianten-Stufung. Deshalb: kostenlos bevorzugt
für @fast; für @medium nur, wenn es die build-Rolle (interaktiv, zuverlässig) UND plan (höchste
Variante) wirklich abdeckt; @heavy nie auf ein volatiles Gratis-Modell legen.

BUDGET-REGEL: Wird beim aktiven Abo ein Fenster knapp (<25 % frei) oder ist ein Fenster
erschöpft, empfiehl günstigere Modelle, weiche bevorzugt auf die "free"-Varianten aus und
erwähne die Budget-Lage kurz im "reasoning"-Satz.
MODELL-BUDGET-REGEL: Zusätzlich zählt das Limit des EINZELNEN Modells. Ist es klein
(z. B. $15/Monat) und läuft der Tier mit vielen Aufrufen (@fast trägt die Masse der
Dispatches), ist das Modell trotz niedrigem Tokenpreis ungeeignet — es ist dann nach
wenigen Stunden gesperrt, obwohl der Tokenpreis günstig aussieht. @fast/@medium brauchen
deshalb Modelle mit großem Monatslimit oder "Budget unbegrenzt"; ein kleines Modell-Limit
ist höchstens für @heavy vertretbar (wenige Aufrufe). Vergleiche also IMMER Preis UND
Monatslimit — bei knappem Budget der erste Blick, den du im reasoning nennst.
Ist ein Modell im MODELL-VERBRAUCH als "GESPERRT" markiert, darfst du es NICHT wählen,
auch wenn Preis und Benchmarks perfekt passen — nimm dann das nächstbeste Modell mit
Zustand "ok". Bei "knapp" nur, wenn es keinen gleichwertigen Ersatz gibt, und nenne dann
den Grund ausdrücklich im reasoning.

ANTWORTE NUR MIT folgendem JSON (keine weiteren Worte, keine Backticks):
{{"reasoning": "ein Satz Gesamtbegründung",
  "fast":   {{"paid": {{"model": "<provider>/<modelID>", "variant": "<variant|leer>", "reason": "<1 Satz>"}},
             "free": {{"model": "opencode/<modelID>", "variant": "<variant|leer>", "reason": "<1 Satz>"}}}},
  "medium": {{"paid": {{"model": "<provider>/<modelID>", "variant": "<variant|leer>", "reason": "<1 Satz>"}},
             "free": {{"model": "opencode/<modelID>", "variant": "<variant|leer>", "reason": "<1 Satz>"}}}},
  "heavy":  {{"paid": {{"model": "<provider>/<modelID>", "variant": "<variant|leer>", "reason": "<1 Satz>"}},
             "free": null}}}}

Für @fast und @medium IMMER beide Varianten angeben: "paid" (stabil, kostenpflichtig) und "free"
(ein KOSTENLOS-Modell aus der Liste, das die Rolle noch vertretbar erfüllt). Für @heavy nur "paid"
(free: null).  Ausfallschutz: das bezahlte Modell ist der Standard, das kostenlose die Option."""


def _resolve_tier_model(tier: str, override_ref: str = "", override_variant: str = "") -> tuple:
    """Modell + Info eines Tiers aus den Overrides (None, wenn nicht nutzbar).

    override_ref/override_variant: explizit gewähltes Analyse-Modell (Panel),
    hat Vorrang vor dem committeten Tier-Modell.
    """
    ref = override_ref
    variant = override_variant
    if not ref and tier == "heavy" and OC_SUGGEST_MODEL:
        ref, _, variant = OC_SUGGEST_MODEL.partition("#")
    if not ref:
        try:
            from github_api import get_overrides
            preset = (get_overrides()["config"].get("presets") or {}).get(PRESET) or {}
            entry = preset.get(tier) or {}
            ref = (entry.get("model") or "").strip()
            variant = (entry.get("variant") or "").strip()
        except Exception:
            return None, {}
    if not ref:
        return None, {}
    ok, res = usable(ref)
    if not ok:
        return None, {}
    provider, _, mid = ref.partition("/")
    if variant and variant not in (res.get("variants") or []):
        variant = ""
    body = {"providerID": provider, "id": mid}
    if variant:
        body["variant"] = variant  # leeres variant-Feld lässt den Lauf sofort fehlschlagen
    label = ref + (f"#{variant}" if variant else "")
    return body, {"model": ref, "variant": variant or None, "label": label}


def analysis_candidates(override_ref: str = "", override_variant: str = "") -> list:
    """Kandidaten-Kette: gewähltes/@heavy -> @medium -> Session-Default.

    Ein explizit gewähltes Modell (override_ref) ersetzt den ersten Kandidaten;
    die Fallback-Kette bleibt als Sicherheitsnetz erhalten.
    """
    chain = []
    if override_ref:
        body, info = _resolve_tier_model("heavy", override_ref, override_variant)
        if body:
            chain.append((body, {"source": "panel-selection", **info}))
    if not chain:
        body, info = _resolve_tier_model("heavy")
        if body:
            chain.append((body, {"source": "tier-heavy", **info}))
    body, info = _resolve_tier_model("medium")
    if body:
        chain.append((body, {"source": "tier-medium", **info}))
    chain.append((None, {"source": "session-default", "label": "Session-Default"}))
    return chain


def resolve_analysis_model() -> tuple:
    """Erstes Analyse-Modell (=@heavy bzw. Override); Kettenlogik in analysis_candidates()."""
    chain = analysis_candidates()
    return chain[0][0], chain[0][1]


def session_default_ref() -> str:
    """Modell, das eine Session ohne eigenes Modell erbt (GET /api/model/default).

    Genau dieser Wert war am 14.10.2026 die Ursache: er zeigte auf ein Modell mit
    Trainings-Klausel, und jede ohne eigenes Modell dispatchte Session erbte ihn
    und starb im ersten Turn. Der Wert ist serverseitig und NICHT setzbar — daher
    nur lesbar pruefen.
    """
    status, payload = _req("GET", f"{OC_SERVER_URL}/api/model/default")
    if status != 200:
        return ""
    data = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(data, dict):
        return ""
    provider = data.get("providerID") or ""
    mid = data.get("modelID") or data.get("id") or ""
    return f"{provider}/{mid}" if provider and mid else ""


def check_model_surface() -> dict:
    """Selbsttest der Modellwahl (rein lesend).

    Prueft (a) die Modelle aller Tiers aus den Overrides und (b) den
    Session-Default daraufhin, ob sie nutzbar sind. Der Default ist lokal pruefbar
    und wird IMMER geprueft; die Tiers brauchen die Overrides aus dem Repo und
    sind sonst "degraded" (nicht falsch-negativ).

    Rueckgabe: {"ok": bool, "degraded": bool, "tiers": {...}, "sessionDefault": {...}}
    """
    tiers = {}
    ok = True
    degraded = False
    error = ""
    preset = None
    try:
        from github_api import get_overrides
        preset = (get_overrides()["config"].get("presets") or {}).get(PRESET) or {}
    except Exception as e:  # noqa: BLE001
        degraded = True
        error = f"Overrides nicht lesbar: {e}"

    tier_refs = set()
    if preset is not None:
        for tier in ("fast", "medium", "heavy"):
            ref = ((preset.get(tier) or {}).get("model") or "").strip()
            if not ref:
                tiers[tier] = {"ref": "", "ok": False, "reason": "kein Modell im Preset"}
                ok = False
                continue
            tier_refs.add(ref)
            good, res = usable(ref)
            entry = {"ref": ref, "ok": bool(good)}
            if good:
                entry["label"] = res.get("name") or ref
            else:
                entry["reason"] = res
            tiers[tier] = entry
            ok = ok and bool(good)

    default_ref = session_default_ref()
    if not default_ref:
        dflt = {"ref": "", "ok": False, "reason": "Session-Default nicht lesbar"}
        ok = False
    else:
        good, res = usable(default_ref)
        dflt = {"ref": default_ref, "ok": bool(good)}
        if good:
            dflt["label"] = res.get("name") or default_ref
            if tier_refs and default_ref not in tier_refs:
                dflt["note"] = ("ausserhalb der Tier-Liste - unkritisch, solange der "
                                "Dispatch das Modell selbst setzt")
        else:
            dflt["reason"] = res
            ok = False

    out = {"ok": ok, "degraded": degraded, "tiers": tiers, "sessionDefault": dflt}
    if error:
        out["error"] = error
    return out


class StallError(RuntimeError):
    """Keine Lebenszeichen vom Modell (Event-Stille) — echter Hänger."""


def _event_watch(sid: str, deadline: float, stall_timeout: float, on_progress=None,
                 on_first_delta=None) -> str:
    """Wartet auf den Abschluss der Session über den SSE-Stream /api/event.

    Liveness = JEDES Event zu unserer Session (reasoning.delta/text.delta/…).
    Erst Event-Stille über stall_timeout gilt als Hänger. Rückgabe:
    "succeeded" | "failed:<typ>" | "stalled" | "timeout" | "unwatchable"

    on_first_delta (optional) wird genau beim ersten .delta-Event aufgerufen —
    für TTFB-Messungen (hermes_speed.probe); ein Fehler im Callback bricht das
    Watching nie ab.
    """
    import http.client
    from urllib.parse import urlparse
    u = urlparse(OC_SERVER_URL)
    host, port = u.hostname, u.port or 80
    auth = base64.b64encode(f"{OC_SERVER_USER}:{OC_SERVER_PASSWORD}".encode()).decode()

    started = time.time()
    last_activity = started
    deltas = 0
    conn = None
    try:
        conn = http.client.HTTPConnection(host, port, timeout=120)
        conn.request("GET", "/api/event", headers={
            "Authorization": f"Basic {auth}",
            "Accept": "text/event-stream",
            "Cache-Control": "no-cache",
        })
        resp = conn.getresponse()
        if resp.status != 200:
            return "unwatchable"
        while True:
            now = time.time()
            if now > deadline:
                _interrupt(sid)
                return "timeout"
            if now - last_activity > stall_timeout:
                _interrupt(sid)
                return "stalled"
            line = resp.readline()          # blockiert bis Zeile/Heartbeat
            if not line:
                return "unwatchable"
            if not line.startswith(b"data: "):
                continue
            try:
                ev = json.loads(line[6:].decode(errors="ignore"))
            except Exception:
                continue
            if sid not in json.dumps(ev):
                continue
            last_activity = time.time()
            etype = ev.get("type", "")
            if etype.endswith(".delta"):
                deltas += 1
                if deltas == 1 and on_first_delta:
                    try:
                        on_first_delta()
                    except Exception:
                        pass
                if on_progress and deltas % 10 == 0:
                    on_progress(deltas=deltas, elapsed=round(time.time() - started, 1))
            if etype.startswith("session.execution."):
                phase = etype.rsplit(".", 1)[-1]
                if phase == "succeeded":
                    if on_progress:
                        on_progress(deltas=deltas, elapsed=round(time.time() - started, 1))
                    return "succeeded"
                if phase in ("failed", "cancelled", "aborted", "interrupted"):
                    return f"failed:{phase}"
    except Exception:
        return "unwatchable"
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass


def _active_ids() -> set:
    _, act = _req("GET", f"{OC_SERVER_URL}/api/session/active")
    active = act.get("data", act) if isinstance(act, dict) else act
    if isinstance(active, dict):
        return set(active.keys())
    if isinstance(active, list):
        return {s.get("id") for s in active}
    return set()


def _progress(sid: str) -> tuple:
    """Fortschritts-Signatur: (tokenSumme, updated-Zeit)."""
    st, res = _req("GET", f"{OC_SERVER_URL}/api/session/{sid}")
    if st != 200:
        return (0, 0)
    d = res.get("data", res) if isinstance(res, dict) else res
    tok = d.get("tokens") or {}
    cache = tok.get("cache") or {}
    total = (tok.get("input") or 0) + (tok.get("output") or 0) + (tok.get("reasoning") or 0) + (cache.get("read") or 0)
    updated = ((d.get("time") or {}).get("updated")) or 0
    return (total, updated)


def _interrupt(sid: str):
    _req("POST", f"{OC_SERVER_URL}/api/session/{sid}/interrupt")


def _delete_session(sid: str):
    """Analyse-Session vollständig entfernen (kein Session-Müll im Workspace)."""
    if KEEP_SESSIONS:
        return False
    try:
        _interrupt(sid)  # falls noch laufend
    except Exception:
        pass
    try:
        st, _ = _req("DELETE", f"{OC_SERVER_URL}/api/session/{sid}")
        return st in (200, 202, 204)
    except Exception:
        return False


def _attempt(prompt: str, model_body, used_info: dict, on_progress=None) -> dict:
    sstatus, sres = _req("POST", f"{OC_SERVER_URL}/api/session",
                         {"location": {"directory": OC_SUGGEST_DIR},
                          "title": "Hestia Modell-Vorschlag"})
    if sstatus not in (200, 201):
        raise RuntimeError(f"Session-Anlegen fehlgeschlagen ({sstatus}): {sres}")
    sid = (sres.get("data") or sres).get("id") if isinstance(sres, dict) else None
    if not sid:
        raise RuntimeError(f"Keine Session-ID: {sres}")

    try:
        if model_body:
            mstatus, _ = _req("POST", f"{OC_SERVER_URL}/api/session/{sid}/model", {"model": model_body})
            if mstatus not in (200, 201, 204):
                used_info.clear()
                used_info.update({"source": "session-default", "note": f"Modell-Umschaltung fehlgeschlagen ({mstatus})"})
        if used_info.get("model"):
            used_info["label"] = used_info["model"] + (f"#{used_info['variant']}" if used_info.get("variant") else "")
        else:
            used_info["label"] = "Session-Default"

        pstatus, pres = _req("POST", f"{OC_SERVER_URL}/api/session/{sid}/prompt", {"text": prompt})
        if pstatus not in (200, 201, 202):
            raise RuntimeError(f"Prompt-Feuern fehlgeschlagen ({pstatus}): {pres}")

        # ── Warten: bevorzugt über den Event-Stream (echte Denk-Aktivität) ──
        _progress_cb = on_progress

        def _live(**kw):
            if _progress_cb:
                try:
                    _progress_cb(**kw)
                except Exception:
                    pass

        result = _event_watch(sid, deadline=time.time() + POLL_TIMEOUT,
                              stall_timeout=STALL_TIMEOUT, on_progress=_live)
        if result == "unwatchable":
            # Fallback: klassisches Polling (falls /api/event nicht verfügbar)
            deadline = time.time() + POLL_TIMEOUT
            last_progress = time.time()
            last_sig = _progress(sid)
            while time.time() < deadline:
                time.sleep(POLL_INTERVAL)
                if sid not in _active_ids():
                    result = "succeeded"
                    break
                sig = _progress(sid)
                if sig != last_sig:
                    last_sig, last_progress = sig, time.time()
                elif time.time() - last_progress > STALL_TIMEOUT:
                    result = "stalled"
                    break
            else:
                result = "timeout"
        if result == "stalled":
            raise StallError(
                f"Keine Lebenszeichen (Events/Tokens) seit {int(STALL_TIMEOUT)}s — echter Hänger."
            )
        if result == "timeout":
            raise RuntimeError(f"Timeout nach {int(POLL_TIMEOUT)}s — bitte erneut versuchen.")
        if result.startswith("failed:"):
            raise RuntimeError(f"Session endete mit '{result.split(':', 1)[1]}'")

        mstatus, mres = _req("GET", f"{OC_SERVER_URL}/api/session/{sid}/message?limit=100")
        if mstatus != 200:
            raise RuntimeError(f"Message-Abruf fehlgeschlagen ({mstatus}): {mres}")
        raw = mres.get("data") if isinstance(mres, dict) else mres
        msgs = raw if isinstance(raw, list) else (raw.get("messages") or [] if isinstance(raw, dict) else [])
        texts = []
        for msg in msgs:
            # v2 API: {"type": "assistant", "content": [{"type": "text", "text": ...}, ...]}
            if msg.get("type") != "assistant" and msg.get("role") != "assistant":
                continue
            parts = msg.get("content") or msg.get("parts") or []
            for part in parts:
                if isinstance(part, dict) and part.get("type") == "text" and part.get("text"):
                    texts.append(part["text"])
        full = "\n".join(texts)
        import re
        m = re.search(r"\{.*\}", full, re.S)
        if not m:
            raise RuntimeError(f"Kein JSON in der Antwort erkannt: {full[:300]}")
        decision = json.loads(m.group(0))
        deleted = _delete_session(sid)
        return {"session": sid, "decision": decision, "raw": full,
                "sessionDeleted": bool(deleted)}
    except Exception:
        _delete_session(sid)
        raise


def suggest(override_ref: str = "", override_variant: str = "", progress=None) -> dict:
    """KI-Vorschlag. progress(dict) wird über den Live-Zustand informiert
    (Modell, Versuch, Hinweis) — für die Anzeige im Panel während des Laufs."""
    if not OC_SERVER_PASSWORD:
        raise RuntimeError("OC_SERVER_PASSWORD nicht gesetzt")
    prompt = _build_prompt()
    candidates = analysis_candidates(override_ref, override_variant)

    def report(**kw):
        if progress:
            try:
                progress(kw)
            except Exception:
                pass

    errors = []
    for idx, (model_body, info) in enumerate(candidates):
        label = info.get("label", "?")
        report(model=label, source=info.get("source"), attempt=idx + 1, total=len(candidates),
               phase="start", note=None)
        def _tier_progress(**kw):
            report(model=label, source=info.get("source"), attempt=idx + 1,
                   total=len(candidates), phase="running", **kw)

        try:
            res = _attempt(prompt, model_body, info, on_progress=_tier_progress)
            res["analyzedWith"] = info
            res["attempt"] = idx + 1
            if idx > 0:
                info["note"] = (
                    f"Fallback (vorher: {', '.join(c[1].get('label', '?') for c in candidates[:idx])} "
                    "antwortete nicht)"
                )
            report(model=label, source=info.get("source"), attempt=idx + 1, total=len(candidates),
                   phase="done", note=info.get("note"))
            return res
        except Exception as e:  # noqa: BLE001 — nächster Kandidat
            errors.append(f"{label}: {e}")
            report(model=label, source=info.get("source"), attempt=idx + 1, total=len(candidates),
                   phase="aborted", note=str(e)[:200])
            continue
    report(phase="failed", note=" | ".join(errors)[:300])
    raise RuntimeError("Analyse fehlgeschlagen — " + " | ".join(errors))


# ── Automatik: ereignisgesteuerter KI-Vorschlag ────────────────────────────
# Die Parameter (enabled/checkIntervalSeconds/cooldownSeconds/analysisModel) liegen
# in der SOT .opencode/hestia-suggest.jsonc und werden über POST /api/suggest/settings
# vom Panel geschrieben; fehlt die Datei, gelten die Env-Defaults.
#
# Ein automatischer Lauf startet nur, wenn sich der relevante Modellkatalog MATERIELL
# ändert (Preis, neues/entferntes Modell, Varianten, Status) UND der Cooldown seit
# dem letzten Auto-Lauf abgelaufen ist. Der lokale Fingerprint-Zustand liegt unter
# STATE_PATH (Bind-Mount /etc/hestia:/app/data) und wird NICHT committet.
SETTINGS_PATH = os.environ.get(
    "HESTIA_SUGGEST_PATH", ".opencode/hestia-suggest.jsonc"
)
STATE_PATH = os.environ.get(
    "HESTIA_SUGGEST_STATE_PATH", "/app/data/hestia-suggest-state.json"
)
DEFAULT_CHECK_INTERVAL = int(os.environ.get("HESTIA_SUGGEST_CHECK_INTERVAL", "21600"))
DEFAULT_COOLDOWN = int(os.environ.get("HESTIA_SUGGEST_COOLDOWN", "86400"))
DEFAULT_ANALYSIS_MODEL = os.environ.get(
    "HESTIA_SUGGEST_ANALYSIS_MODEL", "opencode/space-bunny-free#high"
)
DEFAULT_ENABLED = os.environ.get("HESTIA_SUGGEST_AUTO", "0") == "1"
# Provider, deren Modelle als "relevant" gelten (wie im Katalog/Prompt gefiltert).
FINGERPRINT_PROVIDERS = [
    p.strip() for p in os.environ.get(
        "HESTIA_PROVIDERS", "opencode-go,opencode").split(",") if p.strip()
]

# Grenzen für die Panel-Eingaben (POST /api/suggest/settings)
CHECK_INTERVAL_RANGE = (900, 604800)
COOLDOWN_RANGE = (3600, 2592000)
SETTINGS_KEYS = ("enabled", "checkIntervalSeconds", "cooldownSeconds", "analysisModel")

# Laufzeit-Zustand: settings + persistenter Teil aus STATE_PATH.
STATE = {
    "enabled": DEFAULT_ENABLED,
    "checkIntervalSeconds": DEFAULT_CHECK_INTERVAL,
    "cooldownSeconds": DEFAULT_COOLDOWN,
    "analysisModel": DEFAULT_ANALYSIS_MODEL,
    "fingerprint": "",
    "lastCheck": None,
    "lastChange": None,
    "lastAutoRun": None,
    "generatedAt": None,
    "model": None,
    "result": None,
}
LOCK = threading.Lock()


class SettingsError(Exception):
    """Ungültige Nutzereingabe für die KI-Vorschlag-Einstellungen."""


def _log(msg):
    print(f"[hestia-suggest] {time.strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)


def _clamp_int(value, lo: int, hi: int, fallback: int) -> int:
    """Zahl aus der SOT übernehmen; unbrauchbar → Default (kein Panel-Fehler)."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return fallback
    return n if lo <= n <= hi else fallback


def default_settings() -> dict:
    """Startwerte = Env-Defaults (Fallback, falls die SOT fehlt)."""
    return {
        "enabled": bool(DEFAULT_ENABLED),
        "checkIntervalSeconds": DEFAULT_CHECK_INTERVAL,
        "cooldownSeconds": DEFAULT_COOLDOWN,
        "analysisModel": DEFAULT_ANALYSIS_MODEL,
    }


def read_settings() -> dict:
    """SOT der Automatik lesen und normalisieren.

    Fehlende/kaputte Datei oder GitHub-Fehler → die Env-Defaults, damit das Panel
    immer eine plausible Konfiguration sieht (Muster failover.read_settings).
    """
    data = {}
    try:
        import github_api
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
    model = data.get("analysisModel")
    return {
        "enabled": enabled,
        "checkIntervalSeconds": _clamp_int(
            data.get("checkIntervalSeconds"), *CHECK_INTERVAL_RANGE, d["checkIntervalSeconds"]),
        "cooldownSeconds": _clamp_int(
            data.get("cooldownSeconds"), *COOLDOWN_RANGE, d["cooldownSeconds"]),
        "analysisModel": (model.strip() if isinstance(model, str) else d["analysisModel"]),
    }


def catalog_fingerprint() -> str:
    """Kurzer, stabiler Hash über die MATERIELLEN Felder des Modellkatalogs.

    Erfasst: Modell-ID, Status, Varianten, Preise (input/output, inkl. Cache) und
    die Kontext-Tarifstufen (costTiers) — eine neue oder geänderte Stufe IST eine
    materielle Änderung, weil sie den abgerechneten Preis ändert.
    Erfasst NICHT: Namen, Beschreibungen, Limit-/Release-Angaben — reine Doku- oder
    Beschreibungsänderungen dürfen keinen Lauf auslösen. Rückgabe: 12 Hex-Zeichen.
    """
    rows = []
    for m in catalog.fetch_models() or []:
        if not isinstance(m, dict):
            continue
        if (m.get("providerID") or "") not in FINGERPRINT_PROVIDERS:
            continue
        cost = m.get("cost") or {}
        rows.append([
            m.get("providerID") or "",
            m.get("modelID") or "",
            m.get("status") or "",
            sorted(str(v) for v in (m.get("variants") or [])),
            [
                cost.get("input"),
                cost.get("output"),
                (cost.get("cacheRead") if isinstance(cost, dict) else None),
                (cost.get("cacheWrite") if isinstance(cost, dict) else None),
            ],
            [
                [t.get("from"), t.get("input"), t.get("output"),
                 t.get("cacheRead"), t.get("cacheWrite")]
                for t in (m.get("costTiers") or [])
                if isinstance(t, dict)
            ],
        ])
    rows.sort(key=lambda r: (r[0], r[1]))
    blob = json.dumps(rows, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def read_state() -> dict:
    """Persistenter Laufzeit-Zustand lesen (fehlend/kaputt → leere Defaults)."""
    out = {
        "fingerprint": "", "lastCheck": None, "lastChange": None,
        "lastAutoRun": None, "generatedAt": None, "model": None, "result": None,
        "modelHealth": None,
    }
    try:
        with open(STATE_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            for key in out:
                if key in data:
                    out[key] = data[key]
    except FileNotFoundError:
        pass
    except Exception as e:  # noqa: BLE001
        _log(f"state: {STATE_PATH} nicht lesbar ({e}) — starte ohne Historie")
    return out


def write_state(data: dict) -> bool:
    """Laufzeit-Zustand schreiben. Fehler degradieren (nur loggen), kein Crash."""
    payload = {
        "fingerprint": data.get("fingerprint", ""),
        "lastCheck": data.get("lastCheck"),
        "lastChange": data.get("lastChange"),
        "lastAutoRun": data.get("lastAutoRun"),
        "generatedAt": data.get("generatedAt"),
        "model": data.get("model"),
        "result": data.get("result"),
        "modelHealth": data.get("modelHealth"),
    }
    try:
        os.makedirs(os.path.dirname(STATE_PATH) or ".", exist_ok=True)
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.replace(tmp, STATE_PATH)
        return True
    except Exception as e:  # noqa: BLE001
        _log(f"state: {STATE_PATH} nicht schreibbar ({e})")
        return False


def _require_int(payload: dict, key: str, lo: int, hi: int) -> int:
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise SettingsError(f"'{key}': muss eine ganze Zahl zwischen {lo} und {hi} sein")
    if not (lo <= value <= hi):
        raise SettingsError(f"'{key}': {value} liegt außerhalb {lo}..{hi}")
    return value


def _require_model(value: str) -> str:
    """Analyse-Modell prüfen (leer = Tier-Kette aus den Overrides verwenden)."""
    ref = (value or "").strip()
    if not ref:
        return ""
    base = ref.partition("#")[0]
    ok, res = catalog.usable(base)
    if not ok:
        raise SettingsError(f"'analysisModel': {res}")
    return ref


def update_settings(payload: dict) -> dict:
    """Panel-Einstellungen validieren, als SOT committen und STATE aktualisieren.

    Wirkt sofort: der Hintergrund-Thread liest Intervall/Cooldown je Iteration neu.
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

    settings = {
        "enabled": enabled,
        "checkIntervalSeconds": _require_int(
            payload, "checkIntervalSeconds", *CHECK_INTERVAL_RANGE
        ) if "checkIntervalSeconds" in payload else current["checkIntervalSeconds"],
        "cooldownSeconds": _require_int(
            payload, "cooldownSeconds", *COOLDOWN_RANGE
        ) if "cooldownSeconds" in payload else current["cooldownSeconds"],
        "analysisModel": _require_model(
            payload["analysisModel"] if "analysisModel" in payload
            else current["analysisModel"]
        ),
    }

    header = (
        "// KI-Vorschlag-Automatik (SOT des Hestia-Panels).\n"
        "// Geschrieben über POST /api/suggest/settings, gelesen von hestia/backend/suggest.py.\n"
    )
    text = header + json.dumps(settings, indent=2, ensure_ascii=False) + "\n"
    import github_api
    commit = github_api.put_files(
        {SETTINGS_PATH: text}, "hestia: KI-Vorschlag-Automatik aktualisiert"
    )

    with LOCK:
        STATE["enabled"] = settings["enabled"]
        STATE["checkIntervalSeconds"] = settings["checkIntervalSeconds"]
        STATE["cooldownSeconds"] = settings["cooldownSeconds"]
        STATE["analysisModel"] = settings["analysisModel"]
        snapshot = dict(STATE)
    write_state(snapshot)
    # Wird die Automatik erst jetzt (im Panel) eingeschaltet, muss der Thread
    # starten — beim Container-Start lief er wegen enabled=false noch nicht.
    if settings["enabled"]:
        _ensure_thread()
    _log(
        f"settings: enabled={settings['enabled']} "
        f"intervall={settings['checkIntervalSeconds']}s "
        f"cooldown={settings['cooldownSeconds']}s "
        f"modell={settings['analysisModel'] or '(Tier-Kette)'} "
        f"(Commit {str((commit or {}).get('sha', ''))[:8]})"
    )
    return {"saved": True, "settings": settings, "commit": commit}


def _save_all() -> None:
    with LOCK:
        snapshot = dict(STATE)
    write_state(snapshot)


def run_auto_check() -> dict:
    """Ein Prüf-Zyklus: Fingerprint → (geändert + Cooldown ok) → KI-Lauf.

    Rückgabe enthält die Entscheidung, damit der Zustand nachvollziehbar bleibt.
    """
    settings = read_settings()
    with LOCK:
        STATE["enabled"] = settings["enabled"]
        STATE["checkIntervalSeconds"] = settings["checkIntervalSeconds"]
        STATE["cooldownSeconds"] = settings["cooldownSeconds"]
        STATE["analysisModel"] = settings["analysisModel"]
        stored = STATE.get("fingerprint") or ""

    # Modell-Oberflaeche bei JEDEM Zyklus pruefen - unabhaengig vom Katalog-
    # Fingerprint. Ein unbrauchbares Tier- oder Default-Modell (z. B. eines mit
    # Trainings-Klausel) soll im Panel sichtbar werden, nicht erst im Lauf.
    try:
        health = check_model_surface()
    except Exception as e:  # noqa: BLE001
        health = {"ok": False, "error": str(e)}
    with LOCK:
        STATE["modelHealth"] = health
    if not health.get("ok"):
        _log("WARNUNG Modell-Oberflaeche: "
             + json.dumps(health, ensure_ascii=False)[:600])
    elif health.get("degraded"):
        _log("Modell-Oberflaeche nur teilweise pruefbar: "
             + str(health.get("error", ""))[:200])

    now = time.time()
    fp = catalog_fingerprint()

    # 1) Baseline: ohne gespeicherten Fingerprint NUR merken, kein Lauf
    #    (verhindert einen KI-Lauf bei jedem Deploy/Neustart).
    if not stored:
        with LOCK:
            STATE["fingerprint"] = fp
            STATE["lastCheck"] = now
        _save_all()
        return {"action": "baseline", "fingerprint": fp}

    # 2) unverändert → nichts tun
    if fp == stored:
        with LOCK:
            STATE["lastCheck"] = now
        _save_all()
        return {"action": "unchanged", "fingerprint": fp}

    # 3) materiell geändert → Cooldown prüfen
    with LOCK:
        STATE["lastCheck"] = now
        STATE["lastChange"] = now
    _log(f"Katalog geändert: {stored} -> {fp}")

    # Neue/geänderte Modelle ⇒ Benchmarks nachziehen (billig: nur bei Bedarf
    # wirklich ein GET, sonst Cache). Fehler dürfen den Lauf nicht abbrechen.
    try:
        import benchmarks as bench_mod
        bres = bench_mod.refresh()
        _log(f"Benchmarks: refreshed={bres.get('refreshed')} reason={bres.get('reason')} "
             f"count={bres.get('count')}")
    except Exception as e:  # noqa: BLE001
        _log(f"Benchmarks-Update übersprungen: {e}")

    last_run = STATE.get("lastAutoRun") or 0.0
    if now - last_run < settings["cooldownSeconds"]:
        # Fingerprint bewusst NICHT updaten: die Änderung bleibt "anstehend" und
        # läuft nach Ablauf des Cooldowns nach.
        _save_all()
        remain = int(settings["cooldownSeconds"] - (now - last_run))
        _log(f"Änderung im Cooldown ({remain}s verbleibend) — kein Auto-Lauf")
        return {"action": "cooldown", "fingerprint": fp, "cooldownRemaining": remain}

    if not settings["enabled"]:
        _save_all()
        return {"action": "disabled", "fingerprint": fp}

    ref, _, variant = (settings["analysisModel"] or "").partition("#")
    try:
        res = suggest(override_ref=ref, override_variant=variant)
    except Exception as e:  # noqa: BLE001
        _log(f"Auto-Lauf fehlgeschlagen: {e}")
        _save_all()
        return {"action": "failed", "fingerprint": fp, "error": str(e)}

    model = ((res.get("analyzedWith") or {}).get("label")
             or settings["analysisModel"] or "unbekannt")
    with LOCK:
        STATE["fingerprint"] = fp
        STATE["lastAutoRun"] = now
        STATE["generatedAt"] = time.time()
        STATE["model"] = model
        STATE["result"] = {"decision": res.get("decision"), "session": res.get("session"),
                           "analyzedWith": res.get("analyzedWith")}
    _save_all()
    _log(f"Auto-Lauf fertig ({model})")
    return {"action": "ran", "fingerprint": fp, "model": model}


def auto_status() -> dict:
    """Ausschnitt für GET /api/suggest/status."""
    with LOCK:
        enabled = bool(STATE["enabled"])
        interval = int(STATE["checkIntervalSeconds"])
        last_check = STATE["lastCheck"]
        fingerprint = STATE["fingerprint"] or ""
        generated = STATE["generatedAt"]
        model = STATE["model"]
        result = STATE["result"]
        health = STATE.get("modelHealth")
    now = time.time()
    next_in = max(0, int(interval - (now - last_check))) if last_check else interval
    return {
        "enabled": enabled,
        "checkIntervalSeconds": interval,
        "cooldownSeconds": int(STATE["cooldownSeconds"]),
        "analysisModel": STATE["analysisModel"],
        "lastCheck": last_check,
        "lastChange": STATE["lastChange"],
        "lastAutoRun": STATE["lastAutoRun"],
        "fingerprint": fingerprint,
        "nextCheckIn": next_in,
        "modelHealth": health,
    }, ({"generatedAt": generated, "model": model, "result": result}
        if result and generated else None)


def loop():
    _log(f"Automatik-Thread gestartet (Intervall {STATE['checkIntervalSeconds']}s, "
         f"Cooldown {STATE['cooldownSeconds']}s)")
    while True:
        try:
            run_auto_check()
        except Exception as e:  # noqa: BLE001
            _log(f"Prüfzyklus fehlgeschlagen: {e}")
        with LOCK:
            wait = int(STATE["checkIntervalSeconds"])
        time.sleep(max(60, wait))


_THREAD = None


def _ensure_thread() -> None:
    """Prüf-Thread sicherstellen — auch wenn `enabled` erst später im Panel
    aktiviert wird (dann existiert seit dem Container-Start noch kein Thread)."""
    global _THREAD
    if _THREAD is None or not _THREAD.is_alive():
        _THREAD = threading.Thread(target=loop, daemon=True)
        _THREAD.start()


def start():
    """Hintergrund-Thread anstoßen (nur wenn enabled). Fehler → nur loggen."""
    try:
        settings = read_settings()
        persisted = read_state()
        with LOCK:
            STATE["enabled"] = settings["enabled"]
            STATE["checkIntervalSeconds"] = settings["checkIntervalSeconds"]
            STATE["cooldownSeconds"] = settings["cooldownSeconds"]
            STATE["analysisModel"] = settings["analysisModel"]
            for key, value in persisted.items():
                if value is not None:
                    STATE[key] = value
            enabled = bool(STATE["enabled"])
    except Exception as e:  # noqa: BLE001
        _log(f"Start fehlgeschlagen: {e}")
        return
    if enabled:
        _ensure_thread()
    else:
        _log("Automatik deaktiviert (enabled=false) — kein Thread")

