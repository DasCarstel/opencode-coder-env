---
name: obsidian-ocis
description: Create, find, read, list or edit notes in the private Obsidian vault via the fast oCIS CLI.
version: 2.0.0
author: hermes
license: MIT
metadata:
  hermes:
    tags: [obsidian, notes, markdown, ocis, opencloud, fast-path]
    related_skills: []
---

# Obsidian — private vault on OpenCloud (oCIS)

Use `/opt/data/bin/obsidian_fast.py` through the terminal tool. It starts the
existing `ocis-private` MCP server with only the eight file tools and injects
the Obsidian `space_id`; the 75 oCIS tool schemas are never loaded. Do **not**
call `tool_search` or any `mcp__ocis-private__*` tool on the API/voice surface.

The vault is **not** on the local filesystem. Never use `read_file`,
`write_file`, `patch`, `search_files` or plain `cat` for vault content.

## Commands

```bash
/opt/data/bin/obsidian_fast.py list /
/opt/data/bin/obsidian_fast.py list /01-Gesundheit --limit 30
/opt/data/bin/obsidian_fast.py list / --all          # include dotfolders
/opt/data/bin/obsidian_fast.py find "Blutwerte"
/opt/data/bin/obsidian_fast.py read "/01-Gesundheit/Blutwerte.md" --max-chars 6000
/opt/data/bin/obsidian_fast.py info "/01-Gesundheit/Blutwerte.md"

/opt/data/bin/obsidian_fast.py create "/00-System/Neue Notiz.md" \
  --content "Erster Absatz"

/opt/data/bin/obsidian_fast.py append "/00-System/Neue Notiz.md" \
  --content "Nachtrag"

/opt/data/bin/obsidian_fast.py mkdir "/00-System/Neuer Ordner"
/opt/data/bin/obsidian_fast.py move "/alt.md" "/00-System/neu.md"
/opt/data/bin/obsidian_fast.py delete "/00-System/neu.md" --yes
/opt/data/bin/obsidian_fast.py schema
```

Every command prints compact JSON with vault-relative paths such as
`/01-Gesundheit/Blutwerte.md`.

## Rules

- Paths are absolute from the vault root. The script injects the `space_id`;
  never pass a space ID yourself.
- `create` refuses to overwrite an existing file. Read it first, then use
  `append`, or `create --overwrite` only when the user explicitly wants a
  replacement.
- `create --file LOCAL` is for larger or binary files; otherwise pass
  `--content`. Markdown files automatically use `text/markdown`.
- `find` searches the oCIS full-text index globally and filters results to the
  Obsidian space. The MCP `space_id` search filter is broken (returns 0), so
  the script omits it deliberately.
- `list` hides dotfolders such as `.obsidian` by default; use `--all` only when
  the user asks for them.
- `delete` only works with `--yes`. Ask the user explicitly first, show the
  path, then run it. Deletion moves to the oCIS trash, not permanent.
- `move` renames or moves; it refuses an existing destination unless
  `--overwrite` is set.
- `read` returns text up to `--max-chars` (default 8000). Check the
  `truncated` flag before appending to a very large note.
- Ignore users, groups, roles, shares, spaces, app tokens and admin tools; they
  are not needed for the vault.

## Scope

This API/voice surface intentionally does not load the full oCIS MCP toolset.
For shares, spaces, permissions, versions or other admin work, use the Hermes
CLI/PC; the full workflow is kept in
`references/full-mcp-workflow.md` for that surface.

## Reference

- `references/full-mcp-workflow.md` — full 75-tool oCIS workflow for CLI/PC only
