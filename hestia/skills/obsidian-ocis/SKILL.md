---
name: obsidian-ocis
description: Read, search, create and edit notes in the private Obsidian vault on OpenCloud (oCIS) via the ocis-private MCP tools. Use for every Obsidian/Ideen/notes task; never use the local filesystem.
version: 3.0.0
author: hermes
license: MIT
metadata:
  hermes:
    tags: [obsidian, notes, markdown, ocis, opencloud, mcp]
    related_skills: []
---

# Obsidian — private vault on OpenCloud (oCIS)

The vault is **not** on the local filesystem. Use the oCIS MCP tools of the
server `ocis-private` (`mcp__ocis-private__ocis_*`). Do **not** use `read_file`,
`write_file`, `patch`, `search_files`, `terminal` or `cat` for vault content —
those hit the container filesystem, not oCIS.

## Vault

- Server: `https://opencloud.mueller-nas.de`
- Space: **Obsidian** (mountpoint), MCP server `ocis-private`
- `space_id` (always pass this, every file tool needs it):
  `a0ca6a90-a365-4782-871e-d44447bbc668$a0ca6a90-a365-4782-871e-d44447bbc668!8da8246b-f611-47ef-a8c7-492c9da42ec6:f9768933-2aea-4be1-8c7d-a8503720ff8f:96000317-21eb-4d83-a194-eb4be145af45`
- Paths are absolute from the vault root, e.g. `/01-Gesundheit/Blutwerte.md`,
  `/Ideen.md`.

## Tools

If the tools are not in context, find them with `tool_search` (query
`ocis_upload_file`, `ocis_download_file`, ...).

| Tool | Purpose |
|------|---------|
| `ocis_list_files` | list a directory |
| `ocis_download_file` | read a file (returns `content`, `truncated`) |
| `ocis_upload_file` | create **or overwrite** a file |
| `ocis_create_folder` | create a directory |
| `ocis_move_file` / `ocis_copy_file` | move, rename, copy |
| `ocis_delete_file` | delete (requires `confirm: true`) |
| `ocis_get_file_info` | metadata |
| `ocis_search` | search inside the space |

## Reading

`ocis_download_file {space_id, path}` returns the text in `content`. Check
`truncated`: if `true`, the note is larger than the returned window — do not
rewrite it from that partial text.

## Creating a note

`ocis_upload_file {space_id, path, content, content_type: "text/markdown"}`

## Editing an existing note (read → modify → write)

There is **no separate update or append tool**. `ocis_upload_file` overwrites an
existing path (verified 2026-10-25), so editing is:

1. `ocis_download_file` → full content (`truncated` must be `false`)
2. change the text in context (append or replace)
3. `ocis_upload_file` with the **complete** new content

Never upload only the delta — that truncates the note. When in doubt, read the
file again after writing and verify the result.

## Ideen.md (root note)

`/Ideen.md` is the user's idea list. If the user says "füg … in die Ideen
hinzu" / "add … to the ideas": read the **whole** file, append the new section
at the end, upload the complete result. Never replace the existing content with
only the new part.

## Rules

- Read a file before overwriting it; check `truncated` first.
- Markdown uploads use `content_type: "text/markdown"`.
- `delete` only with `confirm: true` and after explicit user confirmation
  (moves to the oCIS trashbin, not permanent).
- Ignore `ocis_*` tools for users, groups, roles, shares and spaces; they are
  not needed for notes.
- If a call fails with "space not found", run `ocis_list_spaces` and use the
  Obsidian mountpoint.

## Reference

- `references/full-mcp-workflow.md` — same workflow as a compact cheat sheet
