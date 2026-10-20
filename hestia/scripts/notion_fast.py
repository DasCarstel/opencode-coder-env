#!/opt/hermes/.venv/bin/python3
"""Narrow Notion fast path for Hermes API/voice.

This CLI talks directly to the official Notion MCP server's JSON-RPC endpoint
using the existing Hermes OAuth token. The model only sees this small CLI via
the `terminal` tool; it never loads the 65 Notion tool schemas.

Commands:
  schema
  list [--limit N] [--status X] [--schuldner X] [--glaeubiger X]
  find QUERY [--limit N] [--schuldenliste] [--max-chars N]
  read ID [--max-chars N]
  add --schuldner X --glaeubiger Y --objekt TEXT [--betrag N]
      [--datum YYYY-MM-DD] [--status X] [--begleichung TEXT] [--dry-run]
  edit ID [--objekt TEXT] [--datum YYYY-MM-DD] [--schuldner X]
      [--glaeubiger Y] [--betrag N] [--status X] [--begleichung TEXT] [--dry-run]
  delete ID --yes
  restore ID

All output is compact JSON. Destructive operations require explicit --yes.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import sys
import time
from datetime import date
from pathlib import Path

import requests

MCP_URL = "https://mcp.notion.com/mcp"
TOKEN_PATH = Path("/opt/data/mcp-tokens/notion.json")
META_PATH = Path("/opt/data/mcp-tokens/notion.meta.json")
CLIENT_PATH = Path("/opt/data/mcp-tokens/notion.client.json")
LOCK_PATH = Path("/opt/data/mcp-tokens/notion.json.refresh.lock")

DATA_SOURCE = "collection://aaa12de9-7dd4-457f-9277-e7f57758096e"
DATABASE_ID = "a23e1158-0498-468f-920f-bf106325cefc"
DATABASE_URL = "https://app.notion.com/p/a23e11580498468f920fbf106325cefc"

SCHULDNER = [
    "Carsten", "Albin", "Papa", "Nico", "Görkem", "Hao", "Phillip", "Ebay",
    "Elias", "Valentin", "Marvin", "Iris", "Vanessa", "Jasmin", "Alex",
    "Online Handel", "Lukas", "Thorsten", "Hiller", "Paul", "Wolfgang",
]
GLAEUBIGER = [
    "Hao", "Carsten", "Görkem", "Phillip", "Iris", "Lukas", "Marvin", "Nico",
    "Vanessa", "Hiller", "Elias", "Wolfgang",
]
STATUS = ["offen", "ignoriert", "Schenkung", "geschlossen"]

NAME_MAP = {
    "Girkin": "Görkem",
    "Görkim": "Görkem",
    "Goerkem": "Görkem",
    "Gorkem": "Görkem",
    "Görkem": "Görkem",
    "Gläubiger": "Gläubiger",
}

STATUS_MAP = {
    "offen": "offen",
    "open": "offen",
    "offen ": "offen",
    "ignoriert": "ignoriert",
    "ignored": "ignoriert",
    "in progress": "ignoriert",
    "geschlossen": "geschlossen",
    "erledigt": "geschlossen",
    "bezahlt": "geschlossen",
    "ausgeglichen": "geschlossen",
    "done": "geschlossen",
    "closed": "geschlossen",
    "schenkung": "Schenkung",
    "geschenkt": "Schenkung",
    "erlassen": "Schenkung",
}


class NotionError(Exception):
    pass


class Unauthorized(NotionError):
    pass


def emit(data: dict, code: int = 0) -> None:
    print(json.dumps(data, ensure_ascii=False))
    if code:
        raise SystemExit(code)


def fail(message: str, code: int = 1) -> None:
    emit({"ok": False, "error": message}, code)


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise NotionError(f"Kann {path.name} nicht lesen: {exc}") from exc


def get_token() -> str:
    data = _load_json(TOKEN_PATH)
    token = data.get("access_token")
    if not token:
        raise NotionError("Notion access_token fehlt; bitte Hermes neu autorisieren.")
    expires_at = float(data.get("expires_at") or 0)
    if expires_at > time.time() + 120:
        return token
    return refresh_token()


def refresh_token() -> str:
    """Refresh the MCP OAuth token once, under the same lock Hermes uses."""
    try:
        LOCK_PATH.touch(mode=0o600, exist_ok=True)
        lock = LOCK_PATH.open("a+")
    except Exception as exc:
        raise NotionError(f"Kann Token-Lock nicht öffnen: {exc}") from exc

    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        data = _load_json(TOKEN_PATH)
        if float(data.get("expires_at") or 0) > time.time() + 120:
            return data["access_token"]

        meta = _load_json(META_PATH)
        client = _load_json(CLIENT_PATH)
        endpoint = meta.get("token_endpoint")
        client_id = client.get("client_id")
        refresh = data.get("refresh_token")
        if not endpoint or not client_id or not refresh:
            raise NotionError("Notion-OAuth-Metadaten unvollständig; bitte Hermes neu autorisieren.")

        resp = requests.post(
            endpoint,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh,
                "client_id": client_id,
            },
            headers={"Accept": "application/json"},
            timeout=30,
        )
        if resp.status_code >= 400:
            raise NotionError(
                "Notion-Token konnte nicht erneuert werden "
                f"(HTTP {resp.status_code}); bitte Hermes neu autorisieren."
            )
        new = resp.json()
        if not new.get("access_token"):
            raise NotionError("Notion-Refresh lieferte kein access_token; bitte Hermes neu autorisieren.")
        data.update(new)
        data["expires_at"] = time.time() + float(new.get("expires_in") or 28800)
        tmp = TOKEN_PATH.with_name(TOKEN_PATH.name + ".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, TOKEN_PATH)
        return data["access_token"]
    finally:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        finally:
            lock.close()


class MCP:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.token = get_token()
        self.headers = {
            "Authorization": "Bearer " + self.token,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2025-06-18",
        }
        self._initialize()

    def _parse(self, resp: requests.Response) -> dict:
        ctype = resp.headers.get("content-type", "")
        # requests defaults text/* without charset to ISO-8859-1; the MCP
        # server sends UTF-8, so decode explicitly to avoid mojibake.
        text = resp.content.decode("utf-8", errors="replace")
        if "text/event-stream" in ctype:
            for line in text.splitlines():
                if line.startswith("data:"):
                    raw = line[5:].strip()
                    try:
                        return json.loads(raw)
                    except Exception:
                        continue
            raise NotionError("Notion MCP lieferte keine JSON-Antwort (SSE).")
        try:
            return json.loads(text)
        except Exception as exc:
            raise NotionError(f"Notion MCP lieferte kein JSON: {text[:300]}") from exc

    def _post(self, payload: dict, timeout: int = 60) -> requests.Response:
        resp = self.session.post(MCP_URL, headers=self.headers, json=payload, timeout=timeout)
        if resp.status_code == 401:
            raise Unauthorized("Notion MCP: nicht autorisiert.")
        if resp.status_code >= 400:
            raise NotionError(f"Notion MCP HTTP {resp.status_code}: {resp.text[:300]}")
        return resp

    def _initialize(self) -> None:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "hermes-notion-fast", "version": "1.0"},
            },
        }
        resp = self._post(payload, timeout=30)
        sid = resp.headers.get("mcp-session-id")
        if sid:
            self.headers["Mcp-Session-Id"] = sid
        self._parse(resp)
        self.session.post(
            MCP_URL,
            headers=self.headers,
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            timeout=30,
        )

    def call(self, name: str, arguments: dict) -> dict:
        payload = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        try:
            resp = self._post(payload, timeout=90)
        except Unauthorized:
            self.token = refresh_token()
            self.headers["Authorization"] = "Bearer " + self.token
            self._initialize()
            resp = self._post(payload, timeout=90)
        data = self._parse(resp)
        if "error" in data:
            raise NotionError(f"Notion MCP Fehler: {json.dumps(data['error'], ensure_ascii=False)[:500]}")
        return data.get("result", {})

    def close(self) -> None:
        try:
            self.session.close()
        except Exception:
            pass


def call_text(name: str, arguments: dict) -> str:
    mcp = MCP()
    try:
        result = mcp.call(name, arguments)
    finally:
        mcp.close()
    if result.get("isError"):
        texts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
        raise NotionError("\n".join(texts)[:1000] or "Notion MCP meldete einen Fehler.")
    texts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
    return "\n".join(texts)


def query_sql(sql: str, params: list | None = None) -> dict:
    text = call_text(
        "notion-query-data-sources",
        {
            "data": {
                "mode": "sql",
                "data_source_urls": [DATA_SOURCE],
                "query": sql,
                "params": params or [],
            }
        },
    )
    try:
        return json.loads(text)
    except Exception as exc:
        raise NotionError(f"Query lieferte kein JSON: {text[:400]}") from exc


def fetch_entity(entity_id: str) -> dict:
    text = call_text("notion-fetch", {"id": entity_id, "include_file_urls": False})
    try:
        return json.loads(text)
    except Exception:
        return {"text": text}


def normalize_name(name: str, allowed: list[str], label: str) -> str:
    name = name.strip()
    name = NAME_MAP.get(name, name)
    if name in allowed:
        return name
    for candidate in allowed:
        if candidate.casefold() == name.casefold():
            return candidate
    raise NotionError(f"{label} '{name}' ist keine bekannte Option. Erlaubt: {', '.join(allowed)}")


def normalize_status(value: str | None) -> str:
    if not value:
        return "offen"
    key = value.strip().casefold()
    if key in STATUS_MAP:
        return STATUS_MAP[key]
    for status in STATUS:
        if status.casefold() == key:
            return status
    raise NotionError(f"Status '{value}' ist unbekannt. Erlaubt: {', '.join(STATUS)}")


def parse_amount(value: str | None) -> float | None:
    if value is None:
        return None
    raw = value.strip().replace("€", "").replace(" ", "")
    if "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".")
    else:
        raw = raw.replace(",", ".")
    try:
        return float(raw)
    except ValueError as exc:
        raise NotionError(f"Betrag '{value}' ist keine Zahl.") from exc


def validate_date(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise NotionError(f"Datum '{value}' ist nicht im Format YYYY-MM-DD.") from exc


def build_title(objekt: str, datum: str) -> str:
    objekt = objekt.strip()
    if "<mention-date" in objekt:
        return objekt
    return f'{objekt} <mention-date start="{datum}"/>'


def extract_uuid(text: str) -> str | None:
    match = re.search(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", text)
    if match:
        return match.group(0)
    match = re.search(r"([0-9a-fA-F]{32})", text)
    if match:
        raw = match.group(1)
        return f"{raw[0:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:32]}"
    return None


def row_summary(row: dict) -> dict:
    return {
        "id": row.get("id"),
        "url": row.get("url"),
        "objekt": row.get("Objekt"),
        "schuldner": row.get("Schuldner"),
        "glaeubiger": row.get("Gläubiger"),
        "betrag": row.get("Betrag"),
        "status": row.get("Status"),
        "datum": row.get("date:Datum:start"),
        "begleichung": row.get("Begleichung"),
        "created": row.get("createdTime"),
    }


def page_summary(entity_id: str) -> dict:
    data = fetch_entity(entity_id)
    return {
        "title": data.get("title"),
        "url": data.get("url"),
        "text": compact_page_text(data.get("text") or "", 800),
    }


def cmd_schema(_: argparse.Namespace) -> None:
    emit(
        {
            "ok": True,
            "database": "Schulden Liste",
            "database_url": DATABASE_URL,
            "data_source": DATA_SOURCE,
            "schuldner": SCHULDNER,
            "glaeubiger": GLAEUBIGER,
            "status": STATUS,
            "title_format": '<Objekt> <mention-date start="YYYY-MM-DD"/>',
            "date_property": "date:Datum:start",
            "commands": {
                "list": "list [--limit N] [--status X] [--schuldner X] [--glaeubiger X]",
                "find": "find QUERY [--limit N] [--schuldenliste]",
                "get": "get QUERY [--limit N] [--schuldenliste]",
                "read": "read ID",
                "add": "add --schuldner X --glaeubiger Y --objekt TEXT [--betrag N] [--datum YYYY-MM-DD] [--status X] [--begleichung TEXT]",
                "edit": "edit ID [--objekt TEXT] [--datum YYYY-MM-DD] [--schuldner X] [--glaeubiger Y] [--betrag N] [--status X] [--begleichung TEXT]",
                "delete": "delete ID --yes",
                "restore": "restore ID",
            },
        }
    )


def cmd_list(args: argparse.Namespace) -> None:
    where: list[str] = []
    params: list = []
    if args.status:
        where.append('"Status" = ?')
        params.append(normalize_status(args.status))
    if args.schuldner:
        where.append('"Schuldner" = ?')
        params.append(normalize_name(args.schuldner, SCHULDNER, "Schuldner"))
    if args.glaeubiger:
        where.append('"Gläubiger" = ?')
        params.append(normalize_name(args.glaeubiger, GLAEUBIGER, "Gläubiger"))

    sql = f"SELECT * FROM [{DATA_SOURCE}]"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f' ORDER BY "date:Datum:start" DESC, "createdTime" DESC LIMIT {int(args.limit)}'
    data = query_sql(sql, params)
    rows = [row_summary(r) for r in data.get("results", [])]
    emit({"ok": True, "count": len(rows), "rows": rows, "has_more": data.get("has_more", False)})


def compact_page_text(text: str, max_chars: int) -> str:
    """Drop the fetch preamble, ancestor path and parent schema; keep the page body."""
    lines: list[str] = []
    skip = False
    skip_schema = False
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped == "<ancestor-path>":
            skip = True
            continue
        if stripped == "</ancestor-path>":
            skip = False
            continue
        if line.startswith("<parent-data-source-schema"):
            skip_schema = True
            continue
        if skip_schema:
            if stripped == "</parent-data-source-schema>":
                skip_schema = False
            continue
        if skip:
            continue
        if line.startswith("Here is the result of") or line.startswith("<iconMetadata>"):
            continue
        lines.append(line)
    return "\n".join(lines).strip()[:max_chars]


def search_pages(query: str, limit: int, schuldenliste: bool) -> dict:
    arguments = {
        "query": query,
        "page_size": max(1, int(limit)),
        "max_highlight_length": 200,
    }
    if schuldenliste:
        arguments["data_source_url"] = DATA_SOURCE
    text = call_text("notion-search", arguments)
    try:
        data = json.loads(text)
    except Exception as exc:
        raise NotionError(f"Suche lieferte kein JSON: {text[:400]}") from exc
    results = data.get("results") or []
    needle = query.casefold().strip()

    def rank(item: dict) -> tuple[int, int]:
        title = (item.get("title") or "").casefold()
        return (
            0 if needle and needle in title else 1,
            0 if title.startswith(needle) else 1,
        )

    results.sort(key=rank)
    data["results"] = results
    return data


def cmd_find(args: argparse.Namespace) -> None:
    try:
        data = search_pages(args.query, args.limit, args.schuldenliste)
        results = []
        for r in (data.get("results") or [])[: args.limit]:
            results.append(
                {
                    "id": r.get("id"),
                    "title": r.get("title"),
                    "url": r.get("url"),
                    "highlight": r.get("highlight"),
                    "path": r.get("path"),
                }
            )
        emit({"ok": True, "query": args.query, "results": results})
    except NotionError:
        raise
    except Exception:
        text = call_text("notion-search", {"query": args.query, "page_size": int(args.limit)})
        emit({"ok": True, "query": args.query, "result": text[: args.max_chars]})


def cmd_get(args: argparse.Namespace) -> None:
    data = search_pages(args.query, max(3, args.limit), args.schuldenliste)
    results = data.get("results") or []
    if not results:
        emit({"ok": False, "query": args.query, "error": f"Kein Treffer für '{args.query}'."}, 3)
    top = results[0]
    page = fetch_entity(top.get("id"))
    emit(
        {
            "ok": True,
            "query": args.query,
            "id": top.get("id"),
            "title": page.get("title") or top.get("title"),
            "url": page.get("url") or top.get("url"),
            "text": compact_page_text(page.get("text") or "", args.max_chars),
            "matches": [
                {"id": r.get("id"), "title": r.get("title"), "url": r.get("url")}
                for r in results[: args.limit]
            ],
        }
    )


def cmd_read(args: argparse.Namespace) -> None:
    data = fetch_entity(args.id)
    emit(
        {
            "ok": True,
            "id": args.id,
            "title": data.get("title"),
            "url": data.get("url"),
            "text": compact_page_text(data.get("text") or "", args.max_chars),
        }
    )


def cmd_add(args: argparse.Namespace) -> None:
    datum = validate_date(args.datum) if args.datum else date.today().isoformat()
    schuldner = normalize_name(args.schuldner, SCHULDNER, "Schuldner")
    glaeubiger = normalize_name(args.glaeubiger, GLAEUBIGER, "Gläubiger")
    status = normalize_status(args.status or "offen")

    properties: dict = {
        "Objekt": build_title(args.objekt, datum),
        "Schuldner": schuldner,
        "Gläubiger": glaeubiger,
        "Status": status,
        "date:Datum:start": datum,
    }
    betrag = parse_amount(args.betrag)
    if betrag is not None:
        properties["Betrag"] = betrag
    if args.begleichung:
        properties["Begleichung"] = args.begleichung

    payload = {
        "parent": {"data_source_id": DATA_SOURCE},
        "pages": [{"properties": properties}],
        "allow_async": False,
    }
    if args.dry_run:
        emit({"ok": True, "dry_run": True, "payload": payload})
        return

    text = call_text("notion-create-pages", payload)
    page_id = extract_uuid(text)
    out = {"ok": True, "id": page_id, "url": f"https://app.notion.com/{page_id.replace('-', '')}" if page_id else None, "raw": text[:1500]}
    if page_id:
        try:
            out["verified"] = page_summary(page_id)
        except Exception as exc:
            out["verify_error"] = str(exc)
    emit(out)


def cmd_edit(args: argparse.Namespace) -> None:
    if args.objekt and not args.datum and "<mention-date" not in args.objekt:
        raise NotionError("Beim Ändern von --objekt bitte auch --datum angeben (Titel-Mention).")

    properties: dict = {}
    if args.objekt:
        datum = validate_date(args.datum) if args.datum else date.today().isoformat()
        properties["Objekt"] = build_title(args.objekt, datum)
    if args.datum:
        properties["date:Datum:start"] = validate_date(args.datum)
    if args.schuldner:
        properties["Schuldner"] = normalize_name(args.schuldner, SCHULDNER, "Schuldner")
    if args.glaeubiger:
        properties["Gläubiger"] = normalize_name(args.glaeubiger, GLAEUBIGER, "Gläubiger")
    if args.betrag is not None:
        properties["Betrag"] = parse_amount(args.betrag)
    if args.status:
        properties["Status"] = normalize_status(args.status)
    if args.begleichung is not None:
        properties["Begleichung"] = args.begleichung

    if not properties:
        raise NotionError("Keine Änderung angegeben.")

    payload = {
        "page_id": args.id,
        "command": "update_properties",
        "properties": properties,
        "allow_async": False,
    }
    if args.dry_run:
        emit({"ok": True, "dry_run": True, "payload": payload})
        return

    text = call_text("notion-update-page", payload)
    out = {"ok": True, "id": args.id, "raw": text[:1000]}
    try:
        out["verified"] = page_summary(args.id)
    except Exception as exc:
        out["verify_error"] = str(exc)
    emit(out)


def cmd_delete(args: argparse.Namespace) -> None:
    if not args.yes:
        emit(
            {
                "ok": False,
                "id": args.id,
                "error": "Löschen erfordert --yes nach ausdrücklicher Bestätigung durch den Nutzer.",
            },
            3,
        )
    text = call_text("notion-delete-pages", {"page_ids": [args.id], "user_confirmed": True})
    emit({"ok": True, "id": args.id, "raw": text[:1500]})


def cmd_restore(args: argparse.Namespace) -> None:
    text = call_text("notion-restore-pages", {"page_ids": [args.id]})
    emit({"ok": True, "id": args.id, "raw": text[:1500]})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Narrow Notion fast path for the Schuldenliste")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("schema").set_defaults(func=cmd_schema)

    p = sub.add_parser("list")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--status")
    p.add_argument("--schuldner")
    p.add_argument("--glaeubiger")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("find")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--schuldenliste", action="store_true")
    p.add_argument("--max-chars", type=int, default=5000)
    p.set_defaults(func=cmd_find)

    p = sub.add_parser("get")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=3)
    p.add_argument("--schuldenliste", action="store_true")
    p.add_argument("--max-chars", type=int, default=2500)
    p.set_defaults(func=cmd_get)

    p = sub.add_parser("read")
    p.add_argument("id")
    p.add_argument("--max-chars", type=int, default=3000)
    p.set_defaults(func=cmd_read)

    p = sub.add_parser("add")
    p.add_argument("--schuldner", required=True)
    p.add_argument("--glaeubiger", required=True)
    p.add_argument("--objekt", required=True)
    p.add_argument("--betrag")
    p.add_argument("--datum")
    p.add_argument("--status")
    p.add_argument("--begleichung")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("edit")
    p.add_argument("id")
    p.add_argument("--objekt")
    p.add_argument("--datum")
    p.add_argument("--schuldner")
    p.add_argument("--glaeubiger")
    p.add_argument("--betrag")
    p.add_argument("--status")
    p.add_argument("--begleichung")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_edit)

    p = sub.add_parser("delete")
    p.add_argument("id")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_delete)

    p = sub.add_parser("restore")
    p.add_argument("id")
    p.set_defaults(func=cmd_restore)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except NotionError as exc:
        fail(str(exc), 1)
    except requests.RequestException as exc:
        fail(f"Netzwerkfehler: {exc}", 1)
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        fail(f"Unerwarteter Fehler: {exc}", 1)


if __name__ == "__main__":
    main()
