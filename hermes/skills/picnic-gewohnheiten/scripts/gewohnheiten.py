#!/usr/bin/env python3
"""Verdichtet die Picnic-Kaufhistorie zu einer kompakten Stammartikel-Liste.

Startet den vorhandenen mcp-picnic-Wrapper (stdio), holt die letzten
Lieferungen und gibt pro Artikel eine Zeile aus. Es wird nichts gespeichert
und nichts bestellt.

Aufruf:
    python3 gewohnheiten.py [--lieferungen 10] [--limit 20]
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys

WRAPPER = os.environ.get("PICNIC_WRAPPER", "/opt/data/picnic/run.sh")
HARD_LIMIT_S = 240
UNAVAILABLE_PRICE = 99999


class McpClient:
    """Minimaler JSON-RPC-Client fuer den stdio-MCP-Server."""

    def __init__(self, command: str) -> None:
        self.proc = subprocess.Popen(
            [command],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        self._next_id = 0

    def _send(self, payload: dict) -> None:
        stdin = self.proc.stdin
        if stdin is None:
            raise RuntimeError("MCP-Server hat kein stdin")
        stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        stdin.flush()

    def _read_response(self, wanted_id: int) -> dict:
        stdout = self.proc.stdout
        if stdout is None:
            raise RuntimeError("MCP-Server hat kein stdout")
        while True:
            line = stdout.readline()
            if line == "":
                raise RuntimeError("MCP-Server hat die Verbindung beendet")
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if message.get("id") == wanted_id:
                return message

    def request(self, method: str, params: dict) -> dict:
        self._next_id += 1
        message_id = self._next_id
        self._send(
            {"jsonrpc": "2.0", "id": message_id, "method": method, "params": params}
        )
        return self._read_response(message_id)

    def notify(self, method: str) -> None:
        self._send({"jsonrpc": "2.0", "method": method})

    def call(self, name: str, arguments: dict) -> dict:
        message = self.request("tools/call", {"name": name, "arguments": arguments})
        error = message.get("error")
        if error:
            raise RuntimeError(f"{name}: {error.get('message', error)}")
        for part in (message.get("result") or {}).get("content") or []:
            if part.get("type") == "text":
                text = part.get("text") or ""
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return {"raw": text}
        return {}

    def close(self) -> None:
        try:
            if self.proc.stdin is not None:
                self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.terminate()
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()


def format_price(cents):
    if cents is None:
        return "?"
    if cents == UNAVAILABLE_PRICE:
        return "nicht bestellbar"
    return f"{cents / 100:.2f} EUR".replace(".", ",")


def collect(deliveries, client):
    stats = {}
    for delivery in deliveries:
        delivery_id = delivery.get("delivery_id")
        if not delivery_id:
            continue
        try:
            detail = client.call("picnic_get_delivery", {"deliveryId": delivery_id})
        except RuntimeError as exc:
            print(
                f"# Hinweis: Lieferung {delivery_id} nicht lesbar ({exc})",
                file=sys.stderr,
            )
            continue
        seen = (
            (detail.get("delivery_time") or {}).get("start")
            or detail.get("creation_time")
            or ""
        )
        for order in detail.get("orders") or []:
            for line in order.get("items") or []:
                line_price = line.get("price")
                for article in line.get("items") or []:
                    product_id = article.get("id")
                    if not product_id:
                        continue
                    quantity = article.get("quantity") or line.get("quantity") or 1
                    try:
                        quantity = int(quantity)
                    except (TypeError, ValueError):
                        quantity = 1
                    entry = stats.setdefault(
                        product_id,
                        {
                            "name": article.get("name") or product_id,
                            "unit": article.get("unit_quantity") or "",
                            "count": 0,
                            "price": None,
                            "seen": "",
                        },
                    )
                    entry["count"] += quantity
                    entry["name"] = article.get("name") or entry["name"]
                    if seen >= entry["seen"]:
                        entry["seen"] = seen
                        entry["unit"] = article.get("unit_quantity") or entry["unit"]
                        if line_price is not None:
                            entry["price"] = line_price
    return stats


def run(args) -> int:
    client = McpClient(WRAPPER)
    try:
        client.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "picnic-gewohnheiten", "version": "1.0"},
            },
        )
        client.notify("notifications/initialized")
        summary = client.call("picnic_get_deliveries", {"limit": args.lieferungen})
        deliveries = summary.get("deliveries") or []
        if not deliveries:
            print("Keine Lieferungen in der Kaufhistorie gefunden.")
            return 0
        stats = collect(deliveries, client)
    finally:
        client.close()

    ordered = sorted(stats.items(), key=lambda kv: kv[1]["seen"], reverse=True)
    ordered.sort(key=lambda kv: -kv[1]["count"])

    print(
        f"# Picnic-Stammartikel aus {len(deliveries)} Lieferungen "
        f"({len(stats)} verschiedene Artikel)"
    )
    print("# Produkt-ID | Name | Einheit | Kaeufe | letzter Preis | zuletzt gekauft")
    for product_id, entry in ordered[: args.limit]:
        print(
            " | ".join(
                [
                    product_id,
                    entry["name"],
                    entry["unit"] or "-",
                    f"{entry['count']}x",
                    format_price(entry["price"]),
                    entry["seen"][:10],
                ]
            )
        )
    return 0


def _timeout_handler(signum, frame):
    raise TimeoutError("Zeitlimit erreicht")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Picnic-Stammartikel aus der Kaufhistorie verdichten"
    )
    parser.add_argument("--lieferungen", type=int, default=10)
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

    if not os.path.exists(WRAPPER):
        print(f"FEHLER: {WRAPPER} nicht gefunden", file=sys.stderr)
        return 2

    signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(HARD_LIMIT_S)
    try:
        return run(args)
    except TimeoutError:
        print("FEHLER: Zeitlimit ueberschritten (Picnic antwortet nicht).", file=sys.stderr)
        return 3
    except RuntimeError as exc:
        text = str(exc)
        if "2FA" in text.upper():
            print(
                "FEHLER: Picnic verlangt einen 2FA-Code. Nutzer nach dem SMS-Code fragen.",
                file=sys.stderr,
            )
        else:
            print(f"FEHLER: {text}", file=sys.stderr)
        return 1
    finally:
        signal.alarm(0)


if __name__ == "__main__":
    raise SystemExit(main())
