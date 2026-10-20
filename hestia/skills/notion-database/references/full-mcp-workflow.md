---
name: notion-database
description: Add, find, or update rows in a Notion database.
version: 1.0.0
author: hermes
license: MIT
metadata:
  hermes:
    tags: [notion, database, crud]
    related_skills: []
---

# Notion Database Operations

## When to Use

Use this skill when the user asks to add, find, or update entries in a Notion database — e.g. adding a debt to the Schuldenliste, creating a task in a tracker, or querying inventory items.

## Workflow

1. **Locate the database.** If a `references/` file already records its `collection://` URL (e.g. `references/schuldenliste-schema.md`), skip the search and use that URL directly; otherwise search by keyword: `mcp__notion__notion_search` with a short query (e.g. "Schulden", "tasks", "inventory"). Step 2's fetch runs either way — it is what proves the stored URL and options are still valid.
2. **Fetch the database** to get its schema: `mcp__notion__notion_fetch` with the database ID. Extract the `collection://` data source URL and the property schema (types, options, status groups).
3. **Read the existing rows once** with `mcp__notion__notion_query_data_sources` in `sql` mode — `SELECT` title, party/key columns, amount and date of the last ~40 rows, `ORDER BY` date descending, table name = the `collection://` URL. Date columns expand to `date:<Name>:start`, whose embedded colons break SQLite's parser unless every such identifier is double-quoted (`SELECT "date:Datum:start" … ORDER BY "date:Datum:start" DESC`) — an unquoted occurrence returns a `validation_error` about an unexpected token. One call does two jobs: it proves the combination the user names (person pair, category) really recurs in this database, and it shows how comparable titles are worded, so the new title matches the list instead of inventing a phrasing. Cheap enough to run before every create.
4. **Map user input to schema values** — see Pitfalls below.
5. **Create the page**: `mcp__notion__notion_create_pages` with `parent` at the TOP LEVEL (not inside `pages[]`), using `data_source_id` (the `collection://` URL), NOT `database_id`. Send `allow_async: false` so the response carries the new page `id` immediately — step 7 needs it.
6. **Update later**: change only the property the user names — `mcp__notion__notion_update_page` with `command: "update_properties"` and e.g. `{"properties": {"Status": "geschlossen"}}`. Do not re-send unrelated properties. The response contains only `{"page_id": …}`, so it proves nothing about the new value.
7. **Verify with a fetch** of the created/updated page (the create response is not a check — see Pitfalls), then report to the user with a direct link.

## Pitfalls

- **Name variants**: Users may use nicknames or alternate spellings (e.g. "Girkin" for "Görkem"). Always match against the existing select options in the schema and use the canonical option name. If no match is found, ask the user. Dictation mangles **property names** as readily as person names ("der Gröböcker ist Carsten" = Gläubiger): when a dictated word has no schema match, test whether it resembles a property name or a role from the schema before concluding anything is missing.
- **A hedged name is a request for proof, not for invention**: when the user anticipates the name may be missing ("look at the others, it occurs often"), confirm the option in the live schema fetch AND the combination in the existing rows (workflow step 3) before creating. If it is genuinely absent, ask — never add a new select option or a guessed spelling to satisfy the hedge.
- **Describe deferred Notion tools one per `tool_describe` call.** `notion_query_data_sources` alone carries a filter schema of tens of KB; batched with siblings the dump overflows into a spill file you then have to page through by hand, and you end up re-describing them individually anyway.
- **`notion_query_data_sources` nests every parameter under one top-level `data` key.** Send `{"data": {"mode": "sql", "data_source_urls": [...], "query": "..."}}` — flat top-level `data_source_urls`/`query` are rejected with `missing required argument(s): data`, because the tool's schema is a set of `anyOf` mode variants wrapped in `data`, not a flat object. `notion_fetch` and `notion_create_pages`, by contrast, take flat top-level parameters.
- **Parent placement**: The `parent` object must be a top-level parameter of `notion_create_pages`, NOT nested inside the `pages[]` array. Nesting it there causes a validation error.
- **data_source_id vs database_id**: For databases with a single data source, always use the `collection://` URL as `data_source_id`. Using `database_id` fails when the database has more than one data source.
- **Date properties**: Use the expanded format `date:{property}:start` (and optionally `date:{property}:end`, `date:{property}:is_datetime`). Do NOT use a flat `Datum` key.
- **Status defaults and completion mapping**: New debt/task entries default to `offen`. When the user says an entry is settled ("erledigt", "bezahlt", "ausgeglichen"), set `geschlossen` — the Done-group option for a paid debt. Reserve `Schenkung` for a debt that was actually forgiven/gifted, and name the option you chose in the reply so the user can correct it. Read the status groups from a live fetch instead of any written list; option names move between groups over time.
- **Responses are not verification**: `notion_create_pages` echoes property values exactly as sent (a title comes back still showing the literal `<mention-date start="…"/>`), and `notion_update_page` returns only the page ID. Fetch the page to see what Notion actually stored — the fetch renders the converted mention (e.g. `@Letzten Donnerstag`), which is the only proof the date mention took.
- **Do not invent specifics the user hedged on**: when the user names a thing uncertainly ("wie Bim Bab oder so"), write a neutral descriptor (e.g. "Korean Bowl") instead of asserting the guessed name, and flag the choice in the reply so they can fix it.
- **Dictated names go in verbatim, then get flagged**: voice-to-text mangles proper nouns as readily as it turns `@` into "Add", and the dictated spelling may even be right. Write free-text names (page titles, store names) exactly as dictated and flag the likely intended wording in the reply ("wie diktiert; vermutlich gemeint war … — sag Bescheid") instead of silently normalizing them. Only names bound to a select option get canonicalized (see *Name variants*). **Exception — established store spelling**: if workflow step 3 shows the store already has a consistent spelling among existing rows (the Schuldenliste writes "Picnic", not the German "Picknick"), use the DB's spelling so the list stays sortable and uniform, and flag the dictated form in the reply. The verbatim rule applies only where the DB shows no established spelling to match.
- **Number properties**: Use JavaScript numbers (not strings) for number properties like "Betrag". When the user says the amount is still open / needs calculating ("Betrag ist noch offen", "muss noch berechnet werden"), omit the property entirely rather than writing 0 or a guess — an empty number is a normal state in this database — and say in the reply that it stayed open so it gets filled later.
- **Title property**: The title property (e.g. "Objekt") is set via `properties`, not as a separate `title` field.
- **Title format (Schuldenliste): always name + Notion date mention.** The `Objekt` title is `<Name> <mention-date start="YYYY-MM-DD"/>` — e.g. `Steak <mention-date start="2026-10-09"/>` (renders as "Steak @Letzten Freitag"). Never write the plain-text date into the title, and never append the date a second time. Users dictate the mention as `@` (voice-to-text often yields "Add" or "Add 9.10", meaning "@9.10"); the mention's date is the purchase date, normally the same ISO date as the `Datum` property. Pass the markdown form `<mention-date start="YYYY-MM-DD"/>` as the string value of the title property; the API stores a real date mention and Notion renders it relative.

## References

- `references/schuldenliste-schema.md` — Schema and option values for the user's Schuldenliste database