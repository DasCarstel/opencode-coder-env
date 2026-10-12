#!/usr/bin/env python3
"""rollout_coder.py — Config-Rollout auf alle Coder-Workspaces via Coder-API.

Der Pipeline-Job (GitHub Actions) ruft dieses Script nach dem Overrides-Commit auf:
  - Listet die laufenden Workspaces des Token-Users
  - Filtert auf die drei OpenCode-Templates (infrastructure, general, muellerconnect)
  - Startet je Workspace rebuild/restart → entrypoint.sh zieht frisches Repo →
    Router-Overrides sind danach in allen Containern aktiv.

Env (aus GitHub-Secrets):
  CODER_URL   z.B. https://coder.mueller-nas.de
  CODER_TOKEN Coder-API-Token (User mit Rechten auf die Workspaces)
"""
import json
import os
import sys
import time
import urllib.request
import urllib.error

CODER_URL = (os.environ.get("CODER_URL") or "").rstrip("/")
CODER_TOKEN = os.environ.get("CODER_TOKEN") or ""
TARGET_TEMPLATES = [t.lower() for t in os.environ.get(
    "CODER_TARGET_TEMPLATES", "infrastructure,general,muellerconnect").split(",")]


def req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(
        f"{CODER_URL}{path}",
        data=data,
        headers={
            "Coder-Session-Token": CODER_TOKEN,
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}


def main():
    if not CODER_URL or not CODER_TOKEN:
        print("✗ CODER_URL / CODER_TOKEN nicht gesetzt (GitHub-Secrets anlegen)")
        sys.exit(1)

    st, ws = req("GET", "/api/v2/workspaces?limit=50")
    if st != 200:
        print(f"✗ Workspaces-Liste fehlgeschlagen ({st}): {ws}")
        sys.exit(1)
    items = ws if isinstance(ws, list) else ws.get("workspaces", [])
    targets = [
        w for w in items
        if (w.get("template_name") or "").lower() in TARGET_TEMPLATES
        and w.get("latest_build", {}).get("status") == "running"
    ]
    print(f"→ {len(items)} Workspaces gefunden, {len(targets)} Ziele: "
          f"{[w.get('name') for w in targets]}")
    if not targets:
        print("⚠ Keine laufenden Ziel-Workspaces — nichts zu tun")
        return

    failed = []
    for w in targets:
        wid = w["id"]
        name = w.get("name")
        print(f"→ restart {name} ({wid[:8]}…)")
        st, res = req("POST", f"/api/v2/workspaces/{wid}/builds", {"transition": "restart"})
        if st in (200, 201):
            print(f"  ✓ Restart-Build angelegt ({res.get('id', '?')[:8]}…)")
            continue
        if st in (400, 409) and "transition" in json.dumps(res.get("message", "")):
            # Fallback: stop + start (älter Coder-APIs haben kein 'restart')
            print("  ↻ kein restart-Support — stop/start-Fallback")
            st2, _ = req("POST", f"/api/v2/workspaces/{wid}/builds", {"transition": "stop"})
            if st2 not in (200, 201):
                failed.append(name)
                print(f"  ✗ stop fehlgeschlagen ({st2})")
                continue
            for _ in range(60):
                time.sleep(5)
                st3, cur = req("GET", f"/api/v2/workspaces/{wid}")
                if cur.get("latest_build", {}).get("status") == "stopped":
                    break
            st4, _ = req("POST", f"/api/v2/workspaces/{wid}/builds", {"transition": "start"})
            if st4 in (200, 201):
                print("  ✓ restart via stop+start ok")
                continue
        failed.append(name)
        print(f"  ✗ fehlgeschlagen ({st}): {res.get('message', '')}")

    if failed:
        print(f"✗ {len(failed)} Workspace(s) nicht neugestartet: {failed}")
        sys.exit(1)
    print("ROLLOUT OK — alle Workspaces neu gestartet, entrypoint zieht das neue Repo.")


if __name__ == "__main__":
    main()
