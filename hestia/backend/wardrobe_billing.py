#!/usr/bin/env python3
"""wardrobe_billing.py — Google-Billing-Abgleich für den Wardrobe-Tab.

Offizielle Gemini-API-Kosten aus dem Cloud-Billing-BigQuery-Export (Standard-
oder Detailed-Billing-Export), gelesen per Service-Account. Das interne
Tracking (render_usage, wardrobe_panel.py) ist die eigene Schätzung — hier
wird es gegen den Google-Export abgeglichen.

Beschaffung (Audio-Muster wardrobe_panel DSN):
  WARDROBE_GCP_DATASET            — Dataset-Name des Exports (z. B. "billing_export")
  WARDROBE_GCP_PROJECT            — Projekt mit dem Dataset [optional: Fallback
                                    ist das project_id-Feld im SA-Key-JSON]
  GOOGLE_APPLICATION_CREDENTIALS  — SA-Key-Datei (bootstrap.py schreibt sie aus
                                    OpenBao secret/data/wardrobe/gcp nach
                                    /tmp/gcp-sa.json, 0600)

Fehlt die Env oder google-cloud-bigquery → disabled-Payload statt Crash: der
Feature-Toggle ist das OpenBao-Secret, alles andere ist best-effort. Fehler
dürfen die übrigige Panel-API nie kippen (so wie wardrobe_panel.db.ok=false).

Cache 30 MINUTEN (anders als die 30 s des Panels): der Billing-Export läuft
ohnehin bis zu ~24 h nach, häufigeres Nachfragen bringt nichts.
"""
import datetime
import json
import os

DATASET_ENV = "WARDROBE_GCP_DATASET"
PROJECT_ENV = "WARDROBE_GCP_PROJECT"
CREDENTIALS_ENV = "GOOGLE_APPLICATION_CREDENTIALS"

CACHE_TTL = 1800.0  # s — 30 Minuten, siehe Moduldocstring
MAX_DAYS = 35       # Abfragefenster in Tagen (deckt den vollen laufenden Monat)
BILLING_SERVICE = "Gemini API"
TABLE_LIKE_DETAILED = "gcp_billing_export_resource_v1_%"
TABLE_LIKE_ANY = "gcp_billing_export_%"
ROW_LIMIT = 2000  # Ausbremsen: 35 d × Skus — regulär weit darunter

CACHE = {"ts": 0.0, "data": None}


def _ts() -> float:
    return datetime.datetime.now().timestamp()


def _disabled(reason: str) -> dict:
    """Disabled-/Fehler-Payload in der vollen Kontraktform (kein Crash)."""
    return {
        "enabled": False,
        "ok": False,
        "error": reason,
        "table": None,
        "last_date": None,
        "total_month": None,
        "by_day": [],
        "by_sku": [],
        "retrieved_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }


# ── Aggregation (rein, offline testbar) ───────────────────────────────────


def _as_date(v) -> datetime.date | None:
    if isinstance(v, datetime.datetime):
        return v.date()
    if isinstance(v, datetime.date):
        return v
    if v is None:
        return None
    try:
        return datetime.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _num(v) -> float:
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def aggregate(rows: list, today: datetime.date | None = None) -> dict:
    """BigQuery-Zeilen [{day, sku, cost}] → {total_month, by_day, by_sku, last_date}.

    total_month — Summe des LAUFENDEN Kalendermonats (Python-lokal, wie die
    Ampel im Panel). by_day — Summe je Tag (irgendwann im 35-d-Fenster),
    neueste zuerst. by_sku — Monats-Summe je SKU, teuerste zuerst. Ungültige
    Zeilen (kein Tag lesbar) fallen bewusst raus, reißen nie den Auftrag.
    """
    today = today or datetime.date.today()
    month_start = today.replace(day=1)

    day_tot: dict[str, float] = {}
    sku_tot: dict[str, float] = {}
    total = 0.0
    last: datetime.date | None = None
    for r in rows or []:
        d = _as_date(r.get("day"))
        if d is None:
            continue
        c = _num(r.get("cost"))
        day_iso = d.isoformat()
        day_tot[day_iso] = day_tot.get(day_iso, 0.0) + c
        if d >= month_start:
            total += c
            sku = str(r.get("sku") or "")
            sku_tot[sku] = sku_tot.get(sku, 0.0) + c
        if last is None or d > last:
            last = d
    return {
        "total_month": round(total, 6),
        "by_day": [{"day": k, "cost": round(v, 6)}
                   for k, v in sorted(day_tot.items(), reverse=True)],
        "by_sku": [{"sku": k, "cost_month": round(v, 6)}
                   for k, v in sorted(sku_tot.items(), key=lambda kv: (-kv[1], kv[0]))],
        "last_date": last.isoformat() if last else None,
    }


# ── BigQuery-Zugriff (lazy: Import erst bei echtem Bedarf) ─────────────────


def _sa_project() -> str:
    """project_id-Fallback aus der SA-Key-Datei (SA-Muster, nie loggt Material)."""
    path = (os.environ.get(CREDENTIALS_ENV) or "").strip()
    if not path:
        return ""
    try:
        with open(path, encoding="utf-8") as f:
            return str((json.loads(f.read()) or {}).get("project_id", ""))
    except Exception:  # noqa: BLE001 — Fallback scheitern lassen, nie crashen
        return ""


def _client(project: str):
    """BigQuery-Client — None + reason, wenn Bibliothek/Auth fehlen (kein Crash)."""
    try:
        from google.cloud import bigquery
    except ImportError:
        return None, "google-cloud-bigquery nicht installiert (Dependency fehlt)"
    try:
        return bigquery.Client(project=project), None
    except Exception as e:  # noqa: BLE001 — Auth-Fehler etc. als reason
        return None, f"BigQuery-Client-Fehler: {e}"


def _find_table(client, project: str, dataset: str) -> tuple[str | None, str | None]:
    """Export-Tabelle über INFORMATION_SCHEMA.TABLES finden (neueste gewinnt).

    Detailed-Export (…_resource_…) wird bewusst vorgezogen; ein reiner
    Standard-Export erfüllt den Abgleich aber auch (beide tragen sku/cost).
    """
    sql = (
        f"SELECT table_name FROM `{project}.{dataset}.INFORMATION_SCHEMA.TABLES` "
        f"WHERE table_name LIKE '{TABLE_LIKE_DETAILED}' OR table_name LIKE '{TABLE_LIKE_ANY}' "
        "ORDER BY IF(table_name LIKE '%resource%', 0, 1), creation_time DESC LIMIT 1"
    )
    try:
        tables = [dict(r) for r in client.query(sql)]
    except Exception as e:  # noqa: BLE001
        return None, f"Tabellen-Erkennung fehlgeschlagen: {e}"
    if not tables:
        return None, ("keine Billing-Export-Tabelle im Dataset "
                      f"'{project}.{dataset}' (gcp_billing_export_*)")
    return str(tables[0].get("table_name") or ""), None


def _usage_rows(client, project: str, dataset: str, table: str) -> list:
    sql = (
        "SELECT DATE(usage_start_time) AS day, sku.description AS sku, SUM(cost) AS cost "
        f"FROM `{project}.{dataset}.{table}` "
        f"WHERE service.description = '{BILLING_SERVICE}' "
        f"AND DATE(usage_start_time) >= DATE_SUB(CURRENT_DATE(), INTERVAL {int(MAX_DAYS)} DAY) "
        f"GROUP BY day, sku ORDER BY day DESC, sku LIMIT {int(ROW_LIMIT)}"
    )
    return [dict(r) for r in client.query(sql)]


def _fetch() -> dict:
    """Eigentliche Abfrage (ungecached) — try/except rundherum, wirft nie."""
    dataset = (os.environ.get(DATASET_ENV) or "").strip()
    if not dataset:
        return _disabled(
            "kein Dataset — WARDROBE_GCP_DATASET nicht gesetzt "
            "(OpenBao secret/data/wardrobe/gcp)")
    project = (os.environ.get(PROJECT_ENV) or "").strip() or _sa_project()
    if not project:
        return _disabled(
            "kein Projekt — WARDROBE_GCP_PROJECT nicht gesetzt und SA-Key "
            "ohne project_id (OpenBao secret/data/wardrobe/gcp)")
    client, reason = _client(project)
    if client is None:
        return _disabled(reason)
    try:
        table, reason = _find_table(client, project, dataset)
        if not table:
            return _disabled(reason)
        rows = _usage_rows(client, project, dataset, table)
        agg = aggregate(rows)
        return {
            "enabled": True,
            "ok": True,
            "error": None,
            "table": f"{project}.{dataset}.{table}",
            "last_date": agg["last_date"],
            "total_month": agg["total_month"],
            "by_day": agg["by_day"],
            "by_sku": agg["by_sku"],
            "retrieved_at": datetime.datetime.now().isoformat(timespec="seconds"),
        }
    except Exception as e:  # noqa: BLE001 — Abgleich darf nie kippen
        return _disabled(f"BigQuery-Abfrage fehlgeschlagen: {e}")


def billing_state(force: bool = False) -> dict:
    """Google-Billing-Abgleich (gecacht 30 min, wirft nie)."""
    if not force and CACHE["data"] is not None and (_ts() - CACHE["ts"]) < CACHE_TTL:
        return CACHE["data"]
    out = _fetch()
    CACHE.update(ts=_ts(), data=out)
    return out


if __name__ == "__main__":
    import json as _json
    print(_json.dumps(billing_state(), indent=2, ensure_ascii=False))
