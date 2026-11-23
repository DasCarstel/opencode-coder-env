#!/usr/bin/env python3
"""ha_registry.py – Entity-/Label-/Category-Registry von Home Assistant per WebSocket-API lesen und schreiben.

Verwendung:
    ha_registry.py check [--json PFAD]
    ha_registry.py apply PLAN.json [--dry-run]
    ha_registry.py --help

Token: erste Zeile von stdin oder Umgebungsvariable HA_TOKEN.
Das Token wird niemals ausgegeben oder geloggt.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from itertools import count

import aiohttp

WS_URL = "ws://localhost:8123/api/websocket"
TIMEOUT = 20  # Sekunden pro WS-Nachricht
SCOPES = ("automation", "script", "helpers")
DOMAINS = (
    "automation",
    "script",
    "input_boolean",
    "input_number",
    "input_text",
    "input_select",
    "input_datetime",
    "input_button",
    "counter",
    "timer",
    "schedule",
)


class HaError(Exception):
    """Behandelbarer Fehler mit deutscher Meldung."""


def get_token() -> str | None:
    """Token aus HA_TOKEN oder stdin-Zeile 1 lesen (niemals loggen)."""
    tok = os.environ.get("HA_TOKEN")
    if tok:
        return tok
    if not sys.stdin.isatty():
        line = sys.stdin.readline()
        if line:
            tok = line.strip()
            if tok:
                return tok
    return None


class WsClient:
    """Minimaler HA-WebSocket-Client: Auth + Kommando/Ergebnis mit passender id."""

    def __init__(self, ws: aiohttp.ClientWebSocketResponse, session: aiohttp.ClientSession):
        self._ws = ws
        self._session = session
        self._ids = count(1)

    @classmethod
    async def connect(cls, token: str) -> "WsClient":
        session = aiohttp.ClientSession()
        try:
            # aiohttp >= 3.10 erwartet ClientWSTimeout, ältere Versionen einen float
            try:
                ws_timeout = aiohttp.ClientWSTimeout(ws_close=TIMEOUT)
            except AttributeError:
                ws_timeout = TIMEOUT
            ws = await session.ws_connect(WS_URL, timeout=ws_timeout)
        except Exception as exc:
            await session.close()
            raise HaError(f"WebSocket-Verbindung fehlgeschlagen: {exc}") from exc
        client = cls(ws, session)
        try:
            hello = await client._recv_msg()
            if hello.get("type") != "auth_required":
                raise HaError(f"Unerwartete WS-Antwort statt auth_required: {hello.get('type')}")
            await ws.send_json({"type": "auth", "access_token": token})
            reply = await client._recv_msg()
            if reply.get("type") != "auth_ok":
                raise HaError("Authentifizierung fehlgeschlagen (auth_invalid) – Token prüfen.")
        except Exception:
            await client.close()
            raise
        return client

    async def _recv_msg(self) -> dict:
        while True:
            msg = await asyncio.wait_for(self._ws.receive(), TIMEOUT)
            if msg.type == aiohttp.WSMsgType.TEXT:
                return json.loads(msg.data)
            if msg.type in (
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.CLOSING,
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.ERROR,
            ):
                raise HaError("WebSocket-Verbindung wurde geschlossen.")
            # PING/PONG und andere Steuerframes ignorieren
            if msg.type == aiohttp.WSMsgType.ERROR:
                raise HaError(f"WebSocket-Fehler: {msg.data}")

    async def command(self, cmd_type: str, **params) -> object:
        """Kommando senden und Antwort mit gleicher id abwarten (success=True)."""
        cmd_id = next(self._ids)
        await self._ws.send_json({"id": cmd_id, "type": cmd_type, **params})
        while True:
            data = await self._recv_msg()
            if data.get("id") != cmd_id:
                continue  # fremde/interne Nachricht überspringen
            if data.get("type") != "result":
                continue
            if not data.get("success"):
                err = data.get("error") or {}
                raise HaError(
                    f"{cmd_type} fehlgeschlagen: {err.get('code')}: {err.get('message')}"
                )
            return data.get("result")

    async def close(self) -> None:
        try:
            await self._ws.close()
        finally:
            await self._session.close()


async def _list_tolerant(client: WsClient, cmd_type: str, scope: str) -> list:
    """Registry-Liste holen; lehnt der Server den scope-Parameter ab, ohne scope wiederholen."""
    try:
        return await client.command(cmd_type, scope=scope) or []
    except HaError:
        return await client.command(cmd_type) or []


def _domain(entity_id: str) -> str:
    return entity_id.split(".", 1)[0]


def _entity_name(entity: dict) -> str:
    return entity.get("name") or entity.get("original_name") or ""


def _entity_labels(entity: dict) -> list:
    return entity.get("labels") or []


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------

async def cmd_check(token: str, json_path: str | None) -> int:
    client = await WsClient.connect(token)
    entities_by_scope: dict[str, list] = {}
    labels_by_scope: dict[str, list] = {}
    cats_by_scope: dict[str, list] = {}
    try:
        for scope in SCOPES:
            entities_by_scope[scope] = await _list_tolerant(
                client, "config/entity_registry/list", scope
            )
            labels_by_scope[scope] = await _list_tolerant(
                client, "config/label_registry/list", scope
            )
            cats_by_scope[scope] = await _list_tolerant(
                client, "config/category_registry/list", scope
            )
    finally:
        await client.close()

    # Entities über Scopes deduplizieren
    entities: dict[str, dict] = {}
    for scope in SCOPES:
        for ent in entities_by_scope[scope]:
            entities[ent["entity_id"]] = ent

    counts: dict[str, dict] = {}
    ungrouped: list[dict] = []
    for domain in DOMAINS:
        members = [e for e in entities.values() if _domain(e["entity_id"]) == domain]
        no_cat = [e for e in members if not (e.get("categories") or {})]
        counts[domain] = {
            "total": len(members),
            "ohne_kategorie": len(no_cat),
            "ohne_icon": sum(1 for e in members if not e.get("icon")),
            "ohne_label": sum(1 for e in members if not _entity_labels(e)),
        }
        for e in no_cat:
            ungrouped.append(
                {
                    "domain": domain,
                    "entity_id": e["entity_id"],
                    "name": _entity_name(e),
                    "icon": e.get("icon"),
                    "labels": _entity_labels(e),
                }
            )

    print("Registry-Check (Scopes: " + ", ".join(SCOPES) + ")")
    print()
    print(f"{'Domain':<16}{'Total':>6}{'ohne Kat.':>11}{'ohne Icon':>11}{'ohne Label':>12}")
    for domain in DOMAINS:
        c = counts[domain]
        print(
            f"{domain:<16}{c['total']:>6}{c['ohne_kategorie']:>11}"
            f"{c['ohne_icon']:>11}{c['ohne_label']:>12}"
        )

    print()
    print("Entities ohne Kategorie")
    for domain in DOMAINS:
        members = [u for u in ungrouped if u["domain"] == domain]
        print(f"{domain}:")
        if not members:
            print("  (alle kategorisiert)")
        for u in members:
            labels = ",".join(u["labels"])
            print(f"  {u['entity_id']} | {u['name'] or '-'} | {u['icon'] or '-'} | {labels or '-'}")

    if json_path:
        payload = {
            "counts": counts,
            "ungrouped": ungrouped,
            "labels": labels_by_scope,
            "categories": cats_by_scope,
        }
        try:
            with open(json_path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
        except OSError as exc:
            raise HaError(f"JSON-Datei {json_path} nicht schreibbar: {exc}") from exc
        print()
        print(f"JSON geschrieben: {json_path}")
    return 0


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------

async def cmd_apply(token: str, plan_path: str, dry_run: bool) -> int:
    try:
        with open(plan_path, encoding="utf-8") as fh:
            plan = json.load(fh)
    except OSError as exc:
        raise HaError(f"Plan nicht lesbar: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise HaError(f"Plan ist kein gültiges JSON: {exc}") from exc
    if not isinstance(plan, dict):
        raise HaError("Plan muss ein JSON-Objekt sein.")

    prefix = "DRY-RUN: " if dry_run else ""
    ok = 0
    errors: list[str] = []
    alias_ids: dict[str, str] = {}  # Kategoriename -> category_id (diese Runde)
    cats_by_scope: dict[str, dict[str, dict]] = {s: {} for s in SCOPES}

    client = await WsClient.connect(token)
    try:
        # Bestehende Kategorien je Scope laden (für @-Aliase)
        for scope in SCOPES:
            for cat in await _list_tolerant(client, "config/category_registry/list", scope):
                cats_by_scope[scope][cat.get("name")] = cat

        # 1) Neue Kategorien anlegen
        for nc in plan.get("new_categories") or []:
            scope = nc.get("scope")
            name = nc.get("name")
            if not scope or not name:
                errors.append(f"new_categories ohne scope/name: {nc!r}")
                continue
            existing = cats_by_scope.get(scope, {}).get(name)
            if existing:
                alias_ids[name] = existing.get("category_id")
                print(f"Kategorie '{name}' existiert bereits (Scope {scope}): {existing.get('category_id')}")
                continue
            if dry_run:
                print(f"{prefix}Kategorie '{name}' (Scope {scope}, icon {nc.get('icon')}) würde angelegt.")
                continue
            params: dict = {"scope": scope, "name": name}
            if nc.get("icon"):
                params["icon"] = nc["icon"]
            try:
                res = await client.command("config/category_registry/create", **params)
                cid = res.get("category_id") if isinstance(res, dict) else None
                if not cid:
                    raise HaError(f"Antwort ohne category_id: {res!r}")
                alias_ids[name] = cid
                cats_by_scope[scope][name] = res
                print(f"Kategorie '{name}' angelegt (Scope {scope}): {cid}")
            except HaError as exc:
                errors.append(f"Kategorie '{name}' (Scope {scope}): {exc}")

        def resolve_cid(value, scope: str):
            """@Name -> category_id dieser Runde oder bestehender Kategorie."""
            if not isinstance(value, str) or not value.startswith("@"):
                return value
            cname = value[1:]
            if cname in alias_ids:
                return alias_ids[cname]
            existing = cats_by_scope.get(scope, {}).get(cname)
            if existing:
                return existing.get("category_id")
            if dry_run:
                return value  # Platzhalter behalten, nichts würde senden
            raise HaError(f"Kategorie-Alias @{cname} (Scope {scope}) nicht gefunden")

        # 2) Ist-Zustände je Domain laden
        updates = plan.get("updates") or []
        domains = sorted({_domain(u["entity_id"]) for u in updates if u.get("entity_id")})
        entity_index: dict[str, dict] = {}
        for dom in domains:
            for ent in await _list_tolerant(client, "config/entity_registry/list", dom):
                entity_index[ent["entity_id"]] = ent

        # 3) Updates anwenden
        for upd in updates:
            eid = upd.get("entity_id")
            if not eid:
                errors.append(f"Update ohne entity_id: {upd!r}")
                continue
            ent = entity_index.get(eid)
            if ent is None:
                errors.append(f"{eid}: Entity nicht in der Registry gefunden")
                continue
            try:
                new_name = upd.get("name")
                new_icon = upd.get("icon")
                new_labels = upd.get("labels")
                new_cats = None
                if upd.get("categories") is not None:
                    new_cats = {
                        scope: resolve_cid(val, scope)
                        for scope, val in upd["categories"].items()
                    }

                changes = []
                if new_name is not None:
                    changes.append(f"alias {ent.get('name')} -> {new_name}")
                if new_icon is not None:
                    changes.append(f"icon {ent.get('icon')} -> {new_icon}")
                if new_labels is not None:
                    changes.append(f"labels {_entity_labels(ent)} -> {new_labels}")
                if new_cats is not None:
                    changes.append(f"cat {ent.get('categories') or {}} -> {new_cats}")
                if not changes:
                    print(f"{prefix}{eid}: keine Aenderungen im Plan")
                    continue
                print(f"{prefix}{eid}: " + " | ".join(changes))

                if dry_run:
                    ok += 1
                    continue
                params = {"entity_id": eid}
                if new_name is not None:
                    params["name"] = new_name
                if new_icon is not None:
                    params["icon"] = new_icon
                if new_labels is not None:
                    params["labels"] = new_labels
                if new_cats is not None:
                    params["categories"] = new_cats
                await client.command("config/entity_registry/update", **params)
                ok += 1
            except HaError as exc:
                errors.append(f"{eid}: {exc}")
    finally:
        await client.close()

    for err in errors:
        print(f"FEHLER: {err}", file=sys.stderr)
    print(f"Zusammenfassung: OK: {ok}, FEHLER: {len(errors)}")
    return 1 if errors else 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ha_registry.py",
        description=(
            "Home-Assistant-Entity-Registry per WebSocket-API lesen und schreiben "
            "(Entity-/Label-/Category-Registry)."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser(
        "check", help="Registry-Stand lesen: Tabelle pro Domain + Entities ohne Kategorie"
    )
    p_check.add_argument(
        "--json",
        metavar="PFAD",
        help="Zusammenfassung (counts, ungrouped, labels, categories) als JSON-Datei schreiben",
    )

    p_apply = sub.add_parser(
        "apply", help="Plan-JSON anwenden: neue Kategorien anlegen, Entities aktualisieren"
    )
    p_apply.add_argument("plan", help="Pfad zur Plan-JSON-Datei")
    p_apply.add_argument(
        "--dry-run", action="store_true", help="nur anzeigen, nichts an Home Assistant senden"
    )
    return parser


async def run(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    token = get_token()
    if not token:
        print("FEHLER: kein Token (stdin-Zeile 1 oder HA_TOKEN)", file=sys.stderr)
        return 2
    if args.command == "check":
        return await cmd_check(token, args.json)
    if args.command == "apply":
        return await cmd_apply(token, args.plan, args.dry_run)
    return 2  # unerreichbar (argparse required=True)


def main() -> None:
    try:
        code = asyncio.run(run(sys.argv[1:]))
    except HaError as exc:
        print(f"FEHLER: {exc}", file=sys.stderr)
        code = 2
    except KeyboardInterrupt:
        code = 130
    except Exception as exc:  # kein Traceback für den Nutzer
        print(f"FEHLER: unerwartet: {exc}", file=sys.stderr)
        code = 2
    sys.exit(code)


if __name__ == "__main__":
    main()
