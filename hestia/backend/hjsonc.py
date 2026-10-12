#!/usr/bin/env python3
# hjsonc.py — JSONC-Unterstützung für Hestia
#
# Parse/Serialize für JSONC (// und /* */ Kommentare, Strings korrekt
# geschützt). Passt zum Overrides-File, das opencode-model-router liest
# (deep-merge über gebündelte Defaults).
"""JSONC helpers for Hestia (parse/serialize with comment awareness)."""
import json
import re
from typing import Any

HEADER = """// opencode-model-router Overrides — Single Source of Truth der Tier-Anbindung
//
// Dieses File wird vom Plugin `opencode-model-router` gelesen (deep-merge über
// die gebündelten Defaults): Das Plugin durchsucht den Working directory nach
// oben bis zur Repo-Root, sodass jeder OpenCode-Workspaces-Container im Repo
// automatisch dieselbe Konfiguration zieht — kein Symlink, kein Cache-Eingriff.
//
// Verwaltet vom Hestia-Panel (hestia/): Änderungen laufen über
//   POST /api/apply  → schreibe dieses File → git push → CI/CD-Rollout
// Manuelle Edits sind weiterhin möglich (JSONC: // und /* */ erlaubt).
"""


def strip_jsonc(text: str) -> str:
    """Entfernt // und /* */ Kommentare außerhalb von String-Literalen."""
    out = []
    i = 0
    n = len(text)
    in_string = False
    while i < n:
        c = text[i]
        if in_string:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                in_string = False
            i += 1
            continue
        if c == '"':
            in_string = True
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def parse(text: str) -> Any:
    """JSONC-Text → Python-Objekt (kommas-/kommentar-tolerant)."""
    cleaned = strip_jsonc(text)
    cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)  # trailing commas
    return json.loads(cleaned)


def serialize(obj: Any, header: str = HEADER) -> str:
    """Objekt → JSONC mit Hestia-Header-Kommentar."""
    body = json.dumps(obj, indent=2, ensure_ascii=False)
    return header + body + "\n"
