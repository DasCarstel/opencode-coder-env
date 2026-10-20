#!/opt/hermes/.venv/bin/python3
"""Narrow Obsidian/oCIS fast path for Hermes API/voice.

Calls the existing ocis-private MCP server over stdio with a fixed space ID
and only the eight file tools needed for daily vault work. No token is printed.
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import posixpath
import signal
import subprocess
import sys
import tempfile
from typing import Any
from urllib.parse import unquote

CONFIG_PATH = "/opt/data/config.yaml"
SERVER_NAME = "ocis-private"
SPACE_ID = (
    "a0ca6a90-a365-4782-871e-d44447bbc668$a0ca6a90-a365-4782-871e-d44447bbc668!"
    "8da8246b-f611-47ef-a8c7-492c9da42ec6:f9768933-2aea-4be1-8c7d-a8503720ff8f:"
    "96000317-21eb-4d83-a194-eb4be145af45"
)
SPACE_MARKER = SPACE_ID.split("$", 1)[0]
TIMEOUT = 60


class OcisError(RuntimeError):
    pass


def emit(payload: dict[str, Any], code: int = 0) -> None:
    print(json.dumps(payload, ensure_ascii=False))
    if code:
        raise SystemExit(code)


def load_server() -> tuple[str, dict[str, str]]:
    from ruamel.yaml import YAML

    with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
        config = YAML().load(handle)
    servers = config.get("mcp_servers") or config.get("mcpServers") or {}
    server = servers.get(SERVER_NAME) or {}
    raw_env = server.get("env") or {}
    env = os.environ.copy()
    if isinstance(raw_env, dict):
        for key, value in raw_env.items():
            env[str(key)] = str(value)
    elif isinstance(raw_env, list):
        for item in raw_env:
            if isinstance(item, str) and "=" in item:
                key, value = item.split("=", 1)
                env[key] = value
    command = str(server.get("command") or "ocis-mcp-server")
    return command, env


class OcisMCP:
    def __init__(self, timeout: int = TIMEOUT) -> None:
        command, env = load_server()
        self.timeout = timeout
        self._id = 0
        self._stderr = tempfile.TemporaryFile()
        self.proc = subprocess.Popen(
            [command],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._stderr,
            text=True,
            bufsize=1,
            env=env,
        )
        self._request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "obsidian-fast", "version": "1.0"},
            },
        )
        self._notify("notifications/initialized")

    def _stderr_text(self) -> str:
        try:
            self._stderr.flush()
            self._stderr.seek(0)
            data = self._stderr.read()
            if isinstance(data, bytes):
                return data.decode("utf-8", "replace")
            return str(data)
        except Exception:
            return ""

    def _read_line(self) -> str:
        def on_alarm(signum: int, frame: Any) -> None:
            raise OcisError("oCIS-MCP-Timeout")

        old = signal.signal(signal.SIGALRM, on_alarm)
        signal.alarm(self.timeout)
        try:
            return self.proc.stdout.readline()
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old)

    def _write(self, message: dict[str, Any]) -> None:
        if self.proc.stdin is None:
            raise OcisError("oCIS-MCP stdin geschlossen")
        self.proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._id += 1
        request_id = self._id
        self._write(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }
        )
        while True:
            line = self._read_line()
            if not line:
                detail = self._stderr_text().strip()
                raise OcisError(f"oCIS-MCP beendet: {detail[:500] or 'keine Ausgabe'}")
            try:
                response = json.loads(line)
            except json.JSONDecodeError:
                continue
            if response.get("id") != request_id:
                continue
            if "error" in response:
                error = response["error"]
                raise OcisError(str(error)[:800])
            return response.get("result") or {}

    def _notify(self, method: str) -> None:
        self._write({"jsonrpc": "2.0", "method": method})

    @staticmethod
    def _texts(result: dict[str, Any]) -> list[str]:
        texts: list[str] = []
        for item in result.get("content") or []:
            if isinstance(item, dict) and item.get("type") == "text":
                texts.append(str(item.get("text") or ""))
        return texts

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = self._request("tools/call", {"name": name, "arguments": arguments})
        texts = self._texts(result)
        if result.get("isError"):
            raise OcisError(" | ".join(texts)[:800] or "oCIS-Toolfehler")
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            return structured
        if len(texts) == 1:
            try:
                parsed = json.loads(texts[0])
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass
            return {"text": texts[0]}
        if texts:
            return {"content": texts}
        return result

    def close(self) -> None:
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.terminate()
            self.proc.wait(timeout=3)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass


def norm_path(path: str, *, require_file: bool = False) -> str:
    raw = str(path or "").strip().replace("\\", "/")
    if not raw:
        raise OcisError("Pfad fehlt")
    parts = [part for part in raw.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise OcisError("'..' ist im Vault-Pfad nicht erlaubt")
    normalized = "/" + "/".join(parts)
    if require_file and normalized == "/":
        raise OcisError("Das Wurzelverzeichnis ist kein Dateipfad")
    return normalized


def vault_path(raw: Any, fallback: str = "") -> str:
    value = str(raw or "")
    if value.startswith("/dav/spaces/"):
        parts = value.split("/", 4)
        if len(parts) >= 5:
            rest = unquote(parts[4]).strip("/")
            return "/" + rest if rest else "/"
    return value or fallback


def in_obsidian_space(item: dict[str, Any]) -> bool:
    raw = unquote(str(item.get("path") or ""))
    if SPACE_MARKER in raw:
        return True
    return str(item.get("space_id") or "") == SPACE_ID


def compact_info(info: dict[str, Any], path: str | None = None) -> dict[str, Any]:
    keys = (
        "name",
        "path",
        "type",
        "size",
        "content_type",
        "last_modified",
        "etag",
        "file_id",
    )
    result = {key: info.get(key) for key in keys if info.get(key) is not None}
    if path is not None:
        result["path"] = path
    elif result.get("path"):
        result["path"] = vault_path(result["path"])
    return result


def try_info(mcp: OcisMCP, path: str) -> dict[str, Any] | None:
    try:
        return mcp.call("ocis_get_file_info", {"space_id": SPACE_ID, "path": path})
    except OcisError as exc:
        message = str(exc).lower()
        if "not found" in message or "404" in message or "nicht gefunden" in message:
            return None
        raise


def content_payload(args: argparse.Namespace, path: str) -> tuple[str, bool, str]:
    if (args.content is None) == (args.file is None):
        raise OcisError("Genau eine Quelle angeben: --content oder --file")
    if args.file:
        with open(args.file, "rb") as handle:
            raw = handle.read()
        content_type = (
            args.content_type
            or mimetypes.guess_type(path)[0]
            or "application/octet-stream"
        )
        return base64.b64encode(raw).decode("ascii"), True, content_type
    content_type = args.content_type or (
        "text/markdown" if path.lower().endswith(".md") else "text/plain"
    )
    return str(args.content), False, content_type


def cmd_schema(args: argparse.Namespace) -> None:
    emit(
        {
            "ok": True,
            "space_id": SPACE_ID,
            "commands": {
                "list": "list [PATH] [--limit N] [--all]",
                "find": "find QUERY [--limit N]",
                "read": "read PATH [--max-chars N]",
                "create": "create PATH (--content TEXT | --file LOCAL) "
                "[--content-type MIME] [--overwrite]",
                "append": "append PATH --content TEXT",
                "mkdir": "mkdir PATH",
                "delete": "delete PATH --yes",
                "move": "move SOURCE DEST [--overwrite]",
                "info": "info PATH",
            },
        }
    )


def cmd_list(args: argparse.Namespace, mcp: OcisMCP) -> None:
    path = norm_path(args.path)
    result = mcp.call("ocis_list_files", {"space_id": SPACE_ID, "path": path})
    all_files = list(result.get("files") or [])
    show_hidden = bool(getattr(args, "all", False))
    hidden = [item for item in all_files if str(item.get("name") or "").startswith(".")]
    files = (
        all_files
        if show_hidden
        else [item for item in all_files if not str(item.get("name") or "").startswith(".")]
    )
    files.sort(
        key=lambda item: (
            0 if str(item.get("type") or "").lower() in ("folder", "dir") else 1,
            str(item.get("name") or "").casefold(),
        )
    )
    shown = files[: max(0, int(args.limit))]
    entries = []
    for item in shown:
        item_type = str(item.get("type") or "")
        entry: dict[str, Any] = {
            "name": item.get("name"),
            "path": vault_path(
                item.get("path"),
                posixpath.join(path, str(item.get("name") or "")),
            ),
            "type": item_type,
        }
        if item_type.lower() not in ("folder", "dir"):
            entry["size"] = item.get("size")
            entry["last_modified"] = item.get("last_modified")
        entries.append(entry)
    payload: dict[str, Any] = {
        "ok": True,
        "path": path,
        "count": len(files),
        "files": entries,
    }
    if hidden and not show_hidden:
        payload["hidden_skipped"] = len(hidden)
    if len(files) > len(entries):
        payload["truncated"] = True
        payload["shown"] = len(entries)
    emit(payload)


def cmd_find(args: argparse.Namespace, mcp: OcisMCP) -> None:
    query = str(args.query or "").strip()
    if not query:
        raise OcisError("Suchbegriff fehlt")
    limit = max(1, int(args.limit))
    request_limit = min(200, max(limit * 3, 20))
    result = mcp.call(
        "ocis_search",
        {"pattern": query, "limit": request_limit, "offset": 0},
    )
    results = [
        item
        for item in (result.get("results") or [])
        if isinstance(item, dict) and in_obsidian_space(item)
    ]
    entries = []
    for item in results[:limit]:
        highlight = item.get("highlights")
        entries.append(
            {
                "name": item.get("name"),
                "path": vault_path(
                    item.get("path"), "/" + str(item.get("name") or "")
                ),
                "type": item.get("type"),
                "size": item.get("size"),
                "last_modified": item.get("last_modified"),
                "score": item.get("score"),
                "highlight": str(highlight)[:200] if highlight else None,
            }
        )
    emit(
        {
            "ok": True,
            "query": query,
            "total_count": len(results),
            "has_more": result.get("has_more") if len(results) >= limit else False,
            "results": entries,
        }
    )


def cmd_read(args: argparse.Namespace, mcp: OcisMCP) -> None:
    path = norm_path(args.path, require_file=True)
    result = mcp.call(
        "ocis_download_file", {"space_id": SPACE_ID, "path": path}
    )
    content = str(result.get("content") or "")
    payload: dict[str, Any] = {
        "ok": True,
        "path": path,
        "content_type": result.get("content_type"),
        "size": result.get("size"),
        "truncated": bool(result.get("truncated")) or len(content) > args.max_chars,
        "content": content[: max(0, int(args.max_chars))],
    }
    emit(payload)


def cmd_info(args: argparse.Namespace, mcp: OcisMCP) -> None:
    path = norm_path(args.path, require_file=True)
    result = mcp.call("ocis_get_file_info", {"space_id": SPACE_ID, "path": path})
    emit({"ok": True, "path": path, "info": compact_info(result, path)})


def cmd_create(args: argparse.Namespace, mcp: OcisMCP) -> None:
    path = norm_path(args.path, require_file=True)
    if not args.overwrite:
        existing = try_info(mcp, path)
        if existing is not None:
            emit(
                {
                    "ok": False,
                    "path": path,
                    "error": "Datei existiert bereits; nutze append oder --overwrite.",
                },
                2,
            )
    content, is_base64, content_type = content_payload(args, path)
    result = mcp.call(
        "ocis_upload_file",
        {
            "space_id": SPACE_ID,
            "path": path,
            "content": content,
            "content_type": content_type,
            "base64": is_base64,
        },
    )
    if not result.get("success"):
        emit({"ok": False, "path": path, "error": result.get("message") or "Upload fehlgeschlagen"}, 2)
    info = mcp.call("ocis_get_file_info", {"space_id": SPACE_ID, "path": path})
    emit(
        {
            "ok": True,
            "path": path,
            "message": result.get("message"),
            "verified": compact_info(info, path),
        }
    )


def cmd_append(args: argparse.Namespace, mcp: OcisMCP) -> None:
    path = norm_path(args.path, require_file=True)
    existing = mcp.call(
        "ocis_download_file", {"space_id": SPACE_ID, "path": path}
    )
    if existing.get("truncated"):
        raise OcisError("Datei ist für sicheres Anhängen zu groß (Download truncated).")
    old = str(existing.get("content") or "")
    addition = str(args.content)
    if old and not old.endswith("\n"):
        old += "\n"
    if addition and not addition.endswith("\n"):
        addition += "\n"
    result = mcp.call(
        "ocis_upload_file",
        {
            "space_id": SPACE_ID,
            "path": path,
            "content": old + addition,
            "content_type": existing.get("content_type") or "text/markdown",
            "base64": False,
        },
    )
    if not result.get("success"):
        emit({"ok": False, "path": path, "error": result.get("message") or "Anhängen fehlgeschlagen"}, 2)
    info = mcp.call("ocis_get_file_info", {"space_id": SPACE_ID, "path": path})
    emit(
        {
            "ok": True,
            "path": path,
            "message": result.get("message"),
            "verified": compact_info(info, path),
        }
    )


def cmd_mkdir(args: argparse.Namespace, mcp: OcisMCP) -> None:
    path = norm_path(args.path, require_file=True)
    result = mcp.call("ocis_create_folder", {"space_id": SPACE_ID, "path": path})
    if not result.get("success"):
        emit({"ok": False, "path": path, "error": result.get("message") or "Ordner konnte nicht erstellt werden"}, 2)
    info = mcp.call("ocis_get_file_info", {"space_id": SPACE_ID, "path": path})
    emit({"ok": True, "path": path, "message": result.get("message"), "verified": compact_info(info, path)})


def cmd_delete(args: argparse.Namespace, mcp: OcisMCP) -> None:
    path = norm_path(args.path, require_file=True)
    if not args.yes:
        emit(
            {
                "ok": False,
                "path": path,
                "error": "Löschen erfordert --yes nach ausdrücklicher Bestätigung.",
            },
            3,
        )
    result = mcp.call(
        "ocis_delete_file",
        {"space_id": SPACE_ID, "path": path, "confirm": True},
    )
    if not result.get("success"):
        emit({"ok": False, "path": path, "error": result.get("message") or "Löschen fehlgeschlagen"}, 2)
    emit({"ok": True, "path": path, "message": result.get("message")})


def cmd_move(args: argparse.Namespace, mcp: OcisMCP) -> None:
    source = norm_path(args.source, require_file=True)
    dest = norm_path(args.dest, require_file=True)
    result = mcp.call(
        "ocis_move_file",
        {
            "space_id": SPACE_ID,
            "source_path": source,
            "dest_path": dest,
            "overwrite": bool(args.overwrite),
        },
    )
    if not result.get("success"):
        emit({"ok": False, "source": source, "dest": dest, "error": result.get("message") or "Verschieben fehlgeschlagen"}, 2)
    emit({"ok": True, "source": source, "dest": dest, "message": result.get("message")})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Narrow Obsidian/oCIS fast path")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("schema")
    p.set_defaults(func=cmd_schema, needs_mcp=False)

    p = sub.add_parser("list")
    p.add_argument("path", nargs="?", default="/")
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--all", action="store_true")
    p.set_defaults(func=cmd_list, needs_mcp=True)

    p = sub.add_parser("find")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(func=cmd_find, needs_mcp=True)

    p = sub.add_parser("read")
    p.add_argument("path")
    p.add_argument("--max-chars", type=int, default=8000)
    p.set_defaults(func=cmd_read, needs_mcp=True)

    p = sub.add_parser("create")
    p.add_argument("path")
    p.add_argument("--content", default=None)
    p.add_argument("--file", default=None)
    p.add_argument("--content-type", default=None)
    p.add_argument("--overwrite", action="store_true")
    p.set_defaults(func=cmd_create, needs_mcp=True)

    p = sub.add_parser("append")
    p.add_argument("path")
    p.add_argument("--content", required=True)
    p.set_defaults(func=cmd_append, needs_mcp=True)

    p = sub.add_parser("mkdir")
    p.add_argument("path")
    p.set_defaults(func=cmd_mkdir, needs_mcp=True)

    p = sub.add_parser("delete")
    p.add_argument("path")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_delete, needs_mcp=True)

    p = sub.add_parser("move")
    p.add_argument("source")
    p.add_argument("dest")
    p.add_argument("--overwrite", action="store_true")
    p.set_defaults(func=cmd_move, needs_mcp=True)

    p = sub.add_parser("info")
    p.add_argument("path")
    p.set_defaults(func=cmd_info, needs_mcp=True)

    return parser


def main() -> None:
    args = build_parser().parse_args()
    if not getattr(args, "needs_mcp", False):
        args.func(args)
        return
    mcp: OcisMCP | None = None
    try:
        mcp = OcisMCP()
        args.func(args, mcp)
    except OcisError as exc:
        emit({"ok": False, "error": str(exc)}, 2)
    except FileNotFoundError as exc:
        emit({"ok": False, "error": f"Datei nicht gefunden: {exc}"}, 2)
    except KeyboardInterrupt:
        emit({"ok": False, "error": "Abgebrochen"}, 130)
    finally:
        if mcp is not None:
            mcp.close()


if __name__ == "__main__":
    main()
