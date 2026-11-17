---
name: bons-kassenbon
version: 1.0
author: carsten
license: MIT
metadata:
  hermes:
    tags: [bons, receipts, kassenbon, notion]
    related_skills: [notion-database]
description: Use when der Nutzer Kassenbons oder Belege erwähnt.
---

## When to Use

"Kassenbon", "Kassenrezept" (Diktat) oder generell Beleg-/Rechnungsstatus = dieser Skill.

# Bons / Kassenbon (Receipt Wrangler)

"Kassenbon"/"Kassenrezept" (Diktat) = MCP `mcp__bons__*`.

## Lesen

1. `mcp__bons__list_groups` -> group_id (Gruppen "All" (isAllGroup=true) und "My Receipts").
2. `mcp__bons__list_receipts(group_id, page, page_size)` -> id, name, amount, date, status.
   Datumsfilter ueber start_date/end_date moeglich.
3. `mcp__bons__get_receipt(receipt_id)` -> Positionen (receiptItems), Kommentare,
   customFields und imageFiles[].id.
4. `mcp__bons__get_receipt_image(image_id)` -> liefert MEDIA:/pfad auf eine lokal
   gespeicherte Datei. Danach `vision_analyze(image_url=<pfad>, question=...)` setzen,
   um Positionen/Summe zu lesen. PDF-Bons geben keinen Bildblock, sondern einen
   Download-Hinweis.

Bonus-Erkennung: Bei dm-Eigenmarken auf Produktzeile achten (z. B. "Toipa" =
Toilettenpapier, keine Taschentuecher). Im Zweifel per vision_analyze nachfragen,
statt zu raten - der Nutzer korrigiert gern konkret.

## Status setzen

`mcp__bons__set_receipt_status(receipt_ids=[id], status=RESOLVED)`.
Erlaubt: OPEN, NEEDS_ATTENTION, RESOLVED, DRAFT, DECLINED. Ueblich fuer Alltagsbetrieb:
NEEDS_ATTENTION und RESOLVED. resolvedDate setzt der Server.

WICHTIG: Nicht mit dem Notion-Schulden-Eintrag verwechseln. "Markier den Bon als
erledigt" = RESOLVED im Bons-System. Das Notion-Eintrag-Status (`offen` vs
`geschlossen`) aendert sich dadurch NICHT automatisch; umgekehrt auch nicht. Im Zweifel
nachfragen, was genau "erledigt" meinen soll (Nutzer-Korrektur 2026-11-10: Bon RESOLVED,
Notion-Eintrag blieb `offen`).

## Aufteilung (Wohngemeinschaft Carsten + Goerkem)

- Haushaltswaren und Pfand: geteilt, Teiler 2, exakter Betrag (Cent) in Notion.
- Kopierservice oder sonstige Einzelanteile mit klarem Besitzer: nur fuer den im
  Gespraech genannten Schuldner, nicht teilen.
- Koerperpflege/Mundpflege/Selbstversorgung (z. B. Zahncreme, Sonnencreme): nur fuer
  Carsten, nicht geteilt.
- Notion-Eintrag: Titel = Ladename + Datums-Mention, Seiteninhalt = Rechnung plus
  Aufstellung plus hergeleiteter Endbetrag. Details siehe Skill notion-database.
