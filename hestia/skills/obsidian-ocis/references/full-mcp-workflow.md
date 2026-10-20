---
name: obsidian-ocis
description: Read, search, create, and edit notes in the private Obsidian vault on OpenCloud (oCIS). Use for every Obsidian task; never use the local filesystem.
version: 1.0.0
author: hermes
license: MIT
metadata:
  hermes:
    tags: [obsidian, notes, markdown, ocis, opencloud]
    related_skills: []
---

# Obsidian — private vault on OpenCloud (oCIS)

Use this skill for all Obsidian work. The vault is **not** on the local filesystem;
the built-in `obsidian` skill is disabled.

## Vault

- Server: `https://opencloud.mueller-nas.de` (oCIS 7.2.4)
- Space: **Obsidian** (mountpoint)
- `space_id`:
  `a0ca6a90-a365-4782-871e-d44447bbc668$a0ca6a90-a365-4782-871e-d44447bbc668!8da8246b-f611-47ef-a8c7-492c9da42ec6:f9768933-2aea-4be1-8c7d-a8503720ff8f:96000317-21eb-4d83-a194-eb4be145af45`
- MCP server: `ocis-private`

## Tools

Use the oCIS MCP tools. If they are not in context, find them with `tool_search`
(query `ocis_list_files`, `ocis_download_file`, ...).

| Tool | Purpose |
|------|---------|
| `ocis_list_files` | list a directory |
| `ocis_download_file` | read a file |
| `ocis_upload_file` | create/overwrite a file |
| `ocis_create_folder` | create a directory |
| `ocis_move_file` / `ocis_copy_file` | move/rename/copy |
| `ocis_delete_file` | delete (requires `confirm: true`) |
| `ocis_get_file_info` | metadata |
| `ocis_search` | search inside the space |

## Rules

- **Always pass `space_id`** — every file tool requires it.
- Paths are absolute from the vault root, e.g. `/01-Gesundheit/Blutwerte.md`.
- Markdown uploads: `content_type: "text/markdown"`; other files use the correct MIME type or `application/octet-stream`.
- Read a file before overwriting it. Never delete without explicit user confirmation.
- Ignore `ocis_*` tools for users, groups, roles, shares, and spaces; they are not needed here.
- If a call fails with "space not found", run `ocis_list_spaces` and use the single non-virtual space.
- Do not use `read_file`, `write_file`, `patch`, `search_files`, or `terminal` for vault content — those hit the local filesystem, not oCIS.
