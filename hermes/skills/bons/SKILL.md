---
name: bons
description: Kassenbons über Receipt Wrangler (bons.mueller-nas.de) per MCP-Server mcp-bons — Bons auflisten, lesen, Bonbilder ansehen und den Status setzen. Bei jeder Bon-Durchsicht zuerst NEEDS_ATTENTION abarbeiten; nach Erledigung (z. B. Eintrag in die Notion-Schuldenliste) den Bon auf RESOLVED setzen.
version: 1.1.0
author: hermes
license: MIT
metadata:
  hermes:
    tags: [bons, kassenbons, receipts, receipt-wrangler, mcp]
    related_skills: [bons-kassenbon, notion-database]
---

# Kassenbons — Receipt Wrangler (Prozess-Schicht)

MCP-Server `bons` (HTTP, `https://mcp-bons.mueller-nas.de/mcp`, Tools unter
`mcp__bons__*`).

**Diese Skill beschreibt nur den Ablauf und die Priorität.** Für Aufteilung
(Carsten/Goerkem), dm-Eigenmarken, PDF-Bons und die Abgrenzung zur
Notion-Schuldenliste gilt zusätzlich **`bons-kassenbon`** — bei Konflikt zwischen
beiden gewinnt `bons-kassenbon` in allen inhaltlichen Fragen (Beträge, Aufteilung,
Was-zählt-als-erledigt).

## Werkzeuge

| Tool | Zweck |
|------|-------|
| `list_groups` | Gruppen holen (`2` = „All" mit allen Bons, `1` = „My Receipts") |
| `list_receipts` | Bons einer Gruppe, paginiert; `status`-Filter möglich |
| `get_receipt` | Volle Details: Positionen, Kommentare, `imageFiles`, Custom-Felder |
| `search_receipts` | Volltextsuche über die Bons |
| `get_receipt_image` | Bonbild als Imageinhalt (Vision) — der Weg, den Inhalt zu lesen |
| `get_categories_and_tags` | Kategorien/Tags mit IDs |
| `set_receipt_status` | **Schreibt:** Status setzen, optional mit Kommentar (max. 50 IDs) |

Statuswerte: `OPEN`, `NEEDS_ATTENTION`, `RESOLVED`, `DRAFT`, `DECLINED`
(case-insensitive). `resolvedDate` setzt der Server selbst.

## Pflicht-Reihenfolge bei „schau dir meine Bons an"

1. `list_groups` → `group_id` (meist `2` = „All").
2. **Zuerst immer `list_receipts(group_id=2, status="NEEDS_ATTENTION")`** —
   das sind die Bons, die noch korrigiert werden müssen (Betrag, Datum,
   Händler, Kategorie, Zuordnung, fehlende Belege).
3. Danach `OPEN` (noch nicht abgearbeitet).
4. `DRAFT` / `DECLINED` nur, wenn der Nutzer ausdrücklich danach fragt.
5. `RESOLVED` nur zur Kontrolle oder auf Wunsch.

**Regel:** `NEEDS_ATTENTION` hat Vorrang vor allem anderen. Sagt der Nutzer
unspezifisch „schau dir die Bons an" oder „was liegt an?", beginne mit
`NEEDS_ATTENTION`, nenne je Bon kurz Händler, Betrag, Datum und was fehlt —
erst danach die übrigen offenen Bons.

## Bon abarbeiten

1. Bon prüfen: `get_receipt` für die Details, `get_receipt_image` +
   `vision_analyze` für den Inhalt (Betrag/Datum/Händler am Bild verifizieren,
   statt zu raten).
2. Bon wird gebucht, z. B. in die Notion-Schuldenliste
   (`/opt/data/bin/notion_fast.py add …`, Details und Aufteilungsregeln:
   Skill `bons-kassenbon` / `notion-database`).
3. **Nach erfolgreichem Eintrag den Bon auf `RESOLVED` setzen:**
   `set_receipt_status(receipt_ids=[<id>], status="RESOLVED",
   comment="Notion: <Gläubiger>/<Objekt>")`
   Das ist eine Ablaufregel und braucht keine Rückfrage — der Bon ist damit
   fachlich erledigt. Der Kommentar ist die Rückverfolgbarkeit.
4. Fehlt etwas oder ist etwas unklar: Status `NEEDS_ATTENTION` belassen bzw.
   setzen und im Kommentar festhalten, **was** fehlt.
5. `DECLINED` nur für Duplikate oder Fälle ohne Erstattung — und nur nach
   Rückfrage beim Nutzer.

## Zwei Systeme, zwei Status (nicht verwechseln)

| Aussage des Nutzers | Wirkung |
|---------------------|---------|
| „Bon / Beleg erledigt", „Bon auf RESOLVED" | nur `set_receipt_status(..., "RESOLVED")` |
| „Schuld beglichen / bezahlt" | nur Notion-Zeile `edit <id> --status geschlossen` — **Bon bleibt**, wie er ist |
| „Bon gebucht / in Notion eingetragen" | Notion-Eintrag anlegen **und** Bon `RESOLVED` (Schritt 3 oben) |

Der Bon-Status und der Notion-Status laufen **nie** automatisch mit: Ein
`RESOLVED` im Bon-System macht die Notion-Zeile nicht `geschlossen` — und
umgekehrt. Ist unklar, welches System gemeint ist, kurz nachfragen, statt
beides zu ändern.

## Grenzen

- Der MCP kann **nur den Status** ändern. Beträge, Daten, Kategorien, Tags oder
  Positionen ändern geht nur in der Weboberfläche (`https://bons.mueller-nas.de`).
- Nie IDs raten: immer aus `list_receipts` / `get_receipt` / `search_receipts`.
- Massenänderungen sparsam: `set_receipt_status` nimmt bis zu 50 IDs; bei mehr
  in Blöcken arbeiten und dem Nutzer die Anzahl vorher nennen.
- Inhaltliche Regeln (Aufteilung, dm-Eigenmarken, PDF-Bons) stehen in
  `bons-kassenbon` — hier nicht duplizieren.
