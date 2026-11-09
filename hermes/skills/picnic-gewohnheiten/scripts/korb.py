#!/usr/bin/env python3
"""Prueft den Picnic-Warenkorb gegen den Mindestbestellwert.

Der mcp-picnic-Server bietet dafuer kein Tool; die Picnic-API liefert den
Mindestbestellwert aber direkt (er haengt am Liefer-Slot). Das Skript liest
nur - es bestellt nichts und aendert nichts am Warenkorb.

Aufruf:
    python3 korb.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request

SESSION_FILE = os.environ.get(
    "PICNIC_SESSION_FILE", "/opt/data/picnic/picnic-session.json"
)
DEVICE_FILE = os.environ.get("PICNIC_DEVICE_FILE", "/opt/data/picnic/picnic-device.json")
API_BASE = os.environ.get(
    "PICNIC_API_BASE",
    "https://storefront-prod.de.picnicinternational.com/api/15",
)
TIMEOUT_S = 20


def euro(cents):
    if cents is None:
        return "?"
    return f"{cents / 100:.2f} EUR".replace(".", ",")


def auth_headers():
    with open(SESSION_FILE, encoding="utf-8") as handle:
        auth = json.load(handle)["authKey"]
    device = ""
    try:
        with open(DEVICE_FILE, encoding="utf-8") as handle:
            data = json.load(handle)
        device = data.get("deviceId") or data.get("device_id") or ""
    except (OSError, ValueError):
        device = ""
    return {
        "User-Agent": "okhttp/4.9.0",
        "Content-Type": "application/json; charset=UTF-8",
        "Accept-Language": "de",
        "x-picnic-auth": auth,
        "x-picnic-agent": "30100;1.246.1-15599;",
        "x-picnic-did": device,
    }


def get(path):
    request = urllib.request.Request(API_BASE + path, headers=auth_headers())
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

    try:
        cart = get("/cart")
    except Exception as exc:
        print(f"FEHLER: Warenkorb nicht lesbar ({exc})", file=sys.stderr)
        return 1

    total = cart.get("checkout_total_price")
    if total is None:
        total = cart.get("total_price") or 0
    positions = len(cart.get("items") or [])

    minimum = None
    try:
        minimum = get("/user-slot-minimum-order-value/minimum").get(
            "minimum_order_value"
        )
    except Exception as exc:
        print(f"# Hinweis: Mindestbestellwert nicht abrufbar ({exc})", file=sys.stderr)

    print(f"# Warenkorb: {positions} Position(en), {euro(total)}")

    if minimum is None:
        print("# Mindestbestellwert: unbekannt - vor der Bestellung beim Nutzer erfragen")
        return 0

    print(f"# Mindestbestellwert: {euro(minimum)}")
    if total < minimum:
        print(f"# Status: NICHT ERREICHT - es fehlen {euro(minimum - total)}")
        print("# Bestellung nicht moeglich. Dem Nutzer die fehlende Summe nennen.")
        return 0

    print(f"# Status: erreicht ({euro(total)} von {euro(minimum)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
