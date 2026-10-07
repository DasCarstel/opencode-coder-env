---
name: obsidian
description: Read and write notes in the private Obsidian vault on OpenCloud (oCIS). Load before any Obsidian file operation in this workspace.
license: MIT
---

# Obsidian — private vault (OpenCloud / oCIS)

## Vault

- Server: `https://opencloud.mueller-nas.de` (oCIS 7.2.4)
- Space: **Obsidian** (mountpoint)
- `space_id`:
  `a0ca6a90-a365-4782-871e-d44447bbc668$a0ca6a90-a365-4782-871e-d44447bbc668!8da8246b-f611-47ef-a8c7-492c9da42ec6:f9768933-2aea-4be1-8c7d-a8503720ff8f:96000317-21eb-4d83-a194-eb4be145af45`

This workspace's token is scoped to this one space — no other vault is reachable.

## Tools (MCP server `ocis`)

| Tool | Purpose |
|------|---------|
| `ocis_list_files` | list a directory |
| `ocis_download_file` | read a file |
| `ocis_upload_file` | create/overwrite a file |
| `ocis_create_folder` | create a directory |
| `ocis_move_file` / `ocis_copy_file` | move/rename/copy |
| `ocis_delete_file` | delete (requires `confirm: true`) |
| `ocis_get_file_info` | file/folder metadata |
| `ocis_search` | search inside the space |

## Rules

- **Always pass `space_id`** (above) — every file tool requires it.
- Paths are absolute from the vault root, e.g. `/01-Gesundheit/Blutwerte.md`.
- Markdown uploads: `content_type: "text/markdown"`; otherwise the correct MIME type or `application/octet-stream`.
- Read a file before overwriting it. Never delete without explicit user confirmation.
- Ignore all other `ocis_*` tools (users, groups, roles, shares, spaces) — not needed here.
- If a call fails with "space not found", run `ocis_list_spaces` and use the single non-virtual space.
