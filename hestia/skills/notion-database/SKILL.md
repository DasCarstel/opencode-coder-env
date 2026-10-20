---
name: notion-database
description: Find, read, add, edit or delete Schuldenliste rows via the fast Notion CLI.
version: 2.0.0
author: hermes
license: MIT
metadata:
  hermes:
    tags: [notion, database, crud, fast-path]
    related_skills: []
---

# Notion Schuldenliste — Fast Path

Use `/opt/data/bin/notion_fast.py` through the terminal tool. It calls the
existing Notion MCP connection directly with fixed schemas, so the 65 Notion
tool schemas are never loaded. Do **not** call `tool_search` or any
`mcp__notion__*` tool on the API/voice surface.

## Commands

```bash
/opt/data/bin/notion_fast.py list --limit 5
/opt/data/bin/notion_fast.py list --status offen
/opt/data/bin/notion_fast.py list --schuldner Carsten
/opt/data/bin/notion_fast.py find "Picnic"
/opt/data/bin/notion_fast.py find "Schulden Liste" --schuldenliste
/opt/data/bin/notion_fast.py get "Picnic"
/opt/data/bin/notion_fast.py read <id>
/opt/data/bin/notion_fast.py schema

/opt/data/bin/notion_fast.py add --schuldner Carsten --glaeubiger Hao \
  --betrag 12.50 --objekt "Steak" --datum 2026-10-20

/opt/data/bin/notion_fast.py edit <id> --status geschlossen
/opt/data/bin/notion_fast.py edit <id> --betrag 15 --begleichung "bar bezahlt"

/opt/data/bin/notion_fast.py delete <id> --yes
/opt/data/bin/notion_fast.py restore <id>
```

Every command prints compact JSON. Read it and summarize it for the user.

## Rules

- `add` needs `--schuldner`, `--glaeubiger` and `--objekt`. `--datum` defaults
  to today. Pass the purchase date when the user names one.
- If the amount is still open ("Betrag ist noch offen"), omit `--betrag`; do
  not write 0 or a guess.
- `add` and `edit` fetch the page afterwards and return a `verified` block.
  Report the rendered title/link from it, not the raw request.
- The script validates names, status values, amounts and dates. If a name is
  unknown it returns the allowed options; ask the user instead of inventing a
  new select option.
- `list` uses the Notion query allowance. If it returns the "used up its Query
  Data Source allowance" error, tell the user the allowance resets within
  60 minutes and use `find`/`read` meanwhile. Do not retry in a loop.
- `delete` only works with `--yes`. Ask the user explicitly first, show the
  entry, then run it. If the user means "paid"/"settled", prefer
  `edit --status geschlossen` over deleting.
- For "find and read the top hit", use `get` — it does search + fetch in one
  call. Use `find` when the user wants to choose from several hits, and `read`
  when you already have an ID.
- `find`, `get` and `read` also work for arbitrary Notion pages, not only the
  Schuldenliste.

## Scope

This API/voice surface intentionally does not load the full Notion MCP toolset.
For complex page content, other databases, schema changes or comments, use the
Hermes CLI/PC; the full workflow is kept in
`references/full-mcp-workflow.md` for that surface.

## References

- `references/schuldenliste-schema.md` — property names, select options, status groups
- `references/full-mcp-workflow.md` — full MCP workflow for CLI/PC only
