#!/usr/bin/env python3
"""bootstrap.py — Hestia-Container-Start: OpenBao-Secrets laden, dann server.py.

Wird im Dockerfile als CMD benutzt. OpenBao-AppRole (gleiche Bauweise wie bei
Hermes): OPENBAO_ADDR / OPENBAO_ROLE_ID / OPENBAO_SECRET_ID als Container-Env.

Secrets (AppRole-Policy muss covered paths freigeben):
  secret/data/mcp/github                    → GITHUB_TOKEN (api_key)
  secret/data/mcp/hermes-opencode-service   → OC_SERVER_PASSWORD (password)
  secret/data/mcp/hestia                    → HESTIA_PASSWORD (password) [optional]
  secret/data/mcp/notify                    → NTFY_TOKEN (hestia_token) [optional]
  secret/data/mcp/artificialanalysis        → AA_API_KEY (api_key) [optional]
                                           Fehlt der Pfad oder das Feld (403/404),
                                           ist das kein Fehler: Benchmarks laufen
                                           dann mit OpenRouter allein weiter.
  secret/data/wardrobe/db                   → WARDROBE_DSN (dsn) [optional]
                                           Wie beim AA-Key: Fehlt der Pfad (403/404),
                                           wird nur geloggt — wardrobe_panel.py
                                           liefert dann einen Payload mit Fehler-
                                           Hinweis, statt zu crashen.
  secret/data/wardrobe/gcp                  → Google-Billing-Abgleich [optional]
                                            sa_json (base64-Service-Account-Key)
                                            → /tmp/gcp-sa.json (0600) + Env
                                            GOOGLE_APPLICATION_CREDENTIALS;
                                            dataset → WARDROBE_GCP_DATASET;
                                            project_id [optional] →
                                            WARDROBE_GCP_PROJECT. Fehlt der Pfad
                                            oder ein Feld: nur loggen - der
                                            Abgleich (wardrobe_billing.py)
                                            bleibt deaktiviert.
  secret/data/mcp/opencode-go               → OC_GO_DEFAULT1..4 (je ein Feld)
                                          ('active' wird NICHT mehr geladen — die
                                           SOT .opencode/hestia-accounts.jsonc
                                           bestimmt den aktiven Account)
"""
import base64
import json
import os
import sys
import urllib.request

OPENBAO_ADDR = os.environ.get("OPENBAO_ADDR", "https://openbao.mueller-nas.de").rstrip("/")
OPENBAO_ROLE_ID = os.environ.get("OPENBAO_ROLE_ID", "")
OPENBAO_SECRET_ID = os.environ.get("OPENBAO_SECRET_ID", "")

SOURCES = [
    ("secret/data/mcp/github", "api_key", "GITHUB_TOKEN"),
    ("secret/data/mcp/hermes-opencode-service", "password", "OC_SERVER_PASSWORD"),
    ("secret/data/mcp/hestia", "password", "HESTIA_PASSWORD"),
    ("secret/data/mcp/notify", "hestia_token", "NTFY_TOKEN"),
]

# OpenCode-Go-Konten: alle Felder liegen in EINEM Secret → ein Request.
GO_ACCOUNTS_PATH = "secret/data/mcp/opencode-go"
# Das Feld "active" wird bewusst NICHT geladen: Hestia (accounts.py) pflegt das
# Feld "active" in der SOT .opencode/hestia-accounts.jsonc. Der historische Wert
# im OpenBao-Secret bleibt zwar bestehen, hat aber keine Wirkung mehr.
GO_FIELDS = [("default1", "OC_GO_DEFAULT1"),
             ("default2", "OC_GO_DEFAULT2"),
             ("default3", "OC_GO_DEFAULT3"),
             ("default4", "OC_GO_DEFAULT4")]

# Artificial Analysis (Primärquelle der Benchmarks) — OPTIONAL.
# Fehlt der Pfad (403/404) oder das Feld, wird nur geloggt: benchmarks.py
# erkennt AA_API_KEY == "" und nutzt dann OpenRouter allein. Der Start darf
# daran nie scheitern.
AA_SECRET_PATH = "secret/data/mcp/artificialanalysis"
AA_SECRET_KEY = "api_key"
AA_ENVNAME = "AA_API_KEY"

# Wardrobe-DB (Postgres, Rendering-Verbrauch + Panel-Einstellungen optional).
# Die DSN landet als WARDROBE_DSN im Container-Env — wardrobe_panel.py liest
# sie erst bei Bedarf (lazy). Gleiche Optionals-Bauweise wie der AA-Key.
WARDROBE_SECRET_PATH = "secret/data/wardrobe/db"
WARDROBE_SECRET_KEY = "dsn"
WARDROBE_ENVNAME = "WARDROBE_DSN"

# Google-Billing-Abgleich (wardrobe_billing.py) — OPTIONAL, gleiches Optionals-
# Muster wie WARDROBE_DSN. sa_json kommt base64-codiert (kein Artefakt-Risiko
# im Secret-Backend) und landet als /tmp/gcp-sa.json (0600).
WARDROBE_GCP_SECRET_PATH = "secret/data/wardrobe/gcp"
WARDROBE_GCP_SA_PATH = "/tmp/gcp-sa.json"


def log(msg):
    print(f"[hestia-bootstrap] {msg}", flush=True)


def bao_request(path, payload=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Vault-Token"] = token
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(f"{OPENBAO_ADDR}/v1/{path}", data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def _load_go_accounts(token):
    """OpenCode-Go-Keys + aktives Konto in einem Request laden (Budget-System)."""
    loaded = []
    try:
        data = bao_request(GO_ACCOUNTS_PATH, token=token)["data"]["data"]
    except Exception as e:
        log(f"  ⚠ OpenCode-Go-Konten: {GO_ACCOUNTS_PATH} nicht lesbar ({e})")
        return
    for field, envname in GO_FIELDS:
        value = data.get(field, "")
        if value:
            os.environ[envname] = value
            loaded.append(envname)
            log(f"  ✓ {envname} geladen")
        else:
            log(f"  ⚠ {envname}: Key '{field}' leer in {GO_ACCOUNTS_PATH}")
    if loaded:
        log(f"  ✓ OpenCode-Go: {len(loaded)}/{len(GO_FIELDS)} Felder geladen "
            f"(aktiv bestimmt Hestia über die SOT)")


def _load_aa(token):
    """Artificial-Analysis-Key laden — optional, Fehler werden nur geloggt."""
    try:
        data = bao_request(AA_SECRET_PATH, token=token)["data"]["data"]
    except Exception as e:
        log(f"  ⚠ {AA_ENVNAME}: {AA_SECRET_PATH} nicht lesbar ({e}) — "
            f"Benchmarks laufen ohne Artificial Analysis (nur OpenRouter)")
        return
    value = (data or {}).get(AA_SECRET_KEY, "")
    if value:
        os.environ[AA_ENVNAME] = value
        log(f"  ✓ {AA_ENVNAME} geladen ({AA_SECRET_PATH})")
    else:
        log(f"  ⚠ {AA_ENVNAME}: Key '{AA_SECRET_KEY}' leer in {AA_SECRET_PATH}")


def _load_wardrobe(token):
    """Wardrobe-DB-DSN laden — optional, Fehler werden nur geloggt."""
    try:
        data = bao_request(WARDROBE_SECRET_PATH, token=token)["data"]["data"]
    except Exception as e:
        log(f"  ⚠ {WARDROBE_ENVNAME}: {WARDROBE_SECRET_PATH} nicht lesbar ({e}) — "
            f"Wardrobe-Panel läuft ohne DB-Zugriff (Einstellungen auf Defaults)")
        return
    value = (data or {}).get(WARDROBE_SECRET_KEY, "")
    if value:
        os.environ[WARDROBE_ENVNAME] = value
        log(f"  ✓ {WARDROBE_ENVNAME} geladen ({WARDROBE_SECRET_PATH})")
    else:
        log(f"  ⚠ {WARDROBE_ENVNAME}: Key '{WARDROBE_SECRET_KEY}' leer in {WARDROBE_SECRET_PATH}")


def _load_wardrobe_gcp(token):
    """Google-Billing-Abgleich: SA-Key + Dataset — optional, Fehler nur loggen.

    sa_json (base64 codierter Service-Account-Key) → /tmp/gcp-sa.json (0600),
    dann GOOGLE_APPLICATION_CREDENTIALS + WARDROBE_GCP_DATASET setzen (und
    ggf. WARDROBE_GCP_PROJECT). Fehlt irgendetwas: nur loggen —
    wardrobe_billing.py liefert dann einen disabled-Payload, der Start darf
    daran nie scheitern.
    """
    try:
        data = bao_request(WARDROBE_GCP_SECRET_PATH, token=token)["data"]["data"]
    except Exception as e:  # noqa: BLE001
        log(f"  ⚠ wardrobe/gcp: {WARDROBE_GCP_SECRET_PATH} nicht lesbar ({e}) — "
            f"Google-Billing-Abgleich bleibt deaktiviert")
        return
    data = data or {}
    sa_b64 = str(data.get("sa_json", ""))
    if not sa_b64:
        log("  ⚠ wardrobe/gcp: Feld 'sa_json' leer — "
            "Google-Billing-Abgleich bleibt deaktiviert")
        return
    try:
        raw = base64.b64decode(sa_b64, validate=False)
        sa = json.loads(raw.decode())
        if not isinstance(sa, dict) or "project_id" not in sa:
            raise ValueError("nicht plausibel (project_id fehlt)")
    except Exception as e:  # noqa: BLE001
        log(f"  ⚠ wardrobe/gcp: sa_json kein brauchbarer Service-Account-Key ({e}) — "
            f"Google-Billing-Abgleich bleibt deaktiviert")
        return
    try:
        os.makedirs(os.path.dirname(WARDROBE_GCP_SA_PATH), exist_ok=True)
        with open(WARDROBE_GCP_SA_PATH, "wb") as f:
            f.write(raw)
        os.chmod(WARDROBE_GCP_SA_PATH, 0o600)
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = WARDROBE_GCP_SA_PATH
        log(f"  ✓ Service-Account-Key geschrieben ({WARDROBE_GCP_SA_PATH}, 0600)")
    except Exception as e:  # noqa: BLE001
        log(f"  ⚠ wardrobe/gcp: Key-Datei nicht schreibbar ({e}) — "
            f"Google-Billing-Abgleich bleibt deaktiviert")
        return
    dataset = str(data.get("dataset", "") or "")
    os.environ["WARDROBE_GCP_DATASET"] = dataset
    project = str(data.get("project_id", "") or "")
    if project:
        os.environ["WARDROBE_GCP_PROJECT"] = project
    if dataset:
        log(f"  ✓ Google-Billing: WARDROBE_GCP_DATASET='{dataset}' geladen "
            f"(Projekt: {project})")
    else:
        log("  ⚠ wardrobe/gcp: Feld 'dataset' leer — "
            "Google-Billing-Abgleich bleibt deaktiviert")


def main():
    if OPENBAO_ROLE_ID and OPENBAO_SECRET_ID:
        try:
            login = bao_request("auth/approle/login",
                                {"role_id": OPENBAO_ROLE_ID, "secret_id": OPENBAO_SECRET_ID})
            token = login["auth"]["client_token"]
            log("OpenBao AppRole-Login ok")
            for path, key, envname in SOURCES:
                try:
                    value = bao_request(path, token=token)["data"]["data"].get(key, "")
                    if value:
                        os.environ[envname] = value
                        log(f"  ✓ {envname} geladen ({path})")
                    else:
                        log(f"  ⚠ {envname}: Key '{key}' leer in {path}")
                except Exception as e:
                    log(f"  ⚠ {envname}: {path} nicht lesbar ({e})")
            _load_go_accounts(token)
            _load_aa(token)
            _load_wardrobe(token)
            _load_wardrobe_gcp(token)
            os.environ["BAO_TOKEN"] = token
        except Exception as e:
            log(f"⚠ OpenBao-Login fehlgeschlagen ({e}) — fahre mit Container-Env fort")
    else:
        log("⚠ Kein OPENBAO_ROLE_ID/OPENBAO_SECRET_ID gesetzt — Container-Env wird direkt benutzt")

    # Restliche Env aus optionalen /etc/hestia.env-artigen Files: bereits im Env
    import server
    server.main()


if __name__ == "__main__":
    main()
