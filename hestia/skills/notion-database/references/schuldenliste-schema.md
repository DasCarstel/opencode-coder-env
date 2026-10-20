# Schuldenliste Database Schema

**Database ID**: `a23e1158-0498-468f-920f-bf106325cefc`
**Data Source (collection://)**: `collection://aaa12de9-7dd4-457f-9277-e7f57758096e`
**URL**: https://app.notion.com/p/a23e11580498468f920fbf106325cefc

## Properties

| Property | Type | Notes |
|----------|------|-------|
| Objekt | title | Page title |
| Schuldner | select | Options: Carsten, Albin, Papa, Nico, Görkem, Hao, Phillip, Ebay, Elias, Valentin, Marvin, Iris, Vanessa, Jasmin, Alex, Online Handel, Lukas, Thorsten, Hiller, Paul, Wolfgang |
| Gläubiger | select | Options: Hao, Carsten, Görkem, Phillip, Iris, Lukas, Marvin, Nico, Vanessa, Hiller, Elias, Wolfgang |
| Betrag | number | Euro format |
| Status | status | Groups: To-do (`offen`), In Progress (`ingnoriert`), Done (`Schenkung`, `geschlossen`) |
| Begleichung | text | Free text |
| Datum | date | Use `date:Datum:start` format |
| Dateien und Medien | file | File uploads |

## Status Groups

- **To-do**: `offen`
- **In Progress**: `ingnoriert`
- **Done**: `Schenkung`, `geschlossen`

Wahl der Option:
- Neuer Eintrag → `offen`.
- "Erledigt" / "bezahlt" / "ausgeglichen" → `geschlossen`.
- `Schenkung` nur, wenn die Schuld wirklich erlassen bzw. verschenkt wurde.
- Die gewählte Option im Reply nennen, damit der Nutzer sie korrigieren kann.

## Titel-Konvention (Objekt)

Immer: `<Objektname> <mention-date start="YYYY-MM-DD"/>` — also Name, ein Leerzeichen, dann das
Einkaufsdatum als **Notion-Datums-Mention** (nicht als Klartext!).

| Richtig | Falsch |
|---------|--------|
| `Steak <mention-date start="2026-10-09"/>` | `Steak Add 9.10` |
| `Go Asia <mention-date start="2026-09-10"/>` | `Go Asia 10.09.2026` |

- Anzeige in Notion: `Steak @Letzten Freitag` (relative Darstellung der Mention) — das ist korrekt und
  identisch mit allen Bestandseinträgen.
- Der Nutzer diktiert die Mention als `@`; Spracherkennung schreibt oft `Add` (z. B. „Steak Add 9.10" =
  „Steak @9.10"). Das `Add` gehört NICHT in den Titel — es ist der Platzhalter für die Datums-Mention.
- Als Wert des Titel-Properties die Markdown-Form als String übergeben:
  `{"Objekt": "Steak <mention-date start=\"2026-10-09\"/>"}` über `notion_update_page` (command
  `update_properties`) bzw. analog beim Anlegen.
- Datum der Mention = Kaufdatum, in der Regel dasselbe ISO-Datum wie die `Datum`-Property (`date:Datum:start`).

## Betrag vs. Item-Liste im Seiteninhalt

`Betrag` ist immer der **Anteil des Schuldners** (nicht zwingend die Itemsumme):

- **Betrag noch offen / unberechnet**: Property ganz weglassen (nicht 0, nicht raten) — leerer Betrag
  ist ein normaler Zustand in dieser DB (Bestand: „Einschreiben" mit leerem Betrag). Im Reply nennen,
  dass er offen geblieben ist, damit er später nachgetragen wird.

- Items im Inhalt einzeln aufführen; Summe und angewandte Teilung mit in den Inhalt schreiben
  (Bestand: `Zusammen 12,3` + `Betrag 6,15`, oder `- Waschpulver 3,35/2` + `Süßes 16,44` → `Betrag 18.12`).
- Ohne Teilung: `Betrag` = Itemsumme (Edeka 25.09.: 21.14).
- Teilung: geteilt wird die **Gesamtsumme** aller Items, Teiler = Zahl der beteiligten Personen, wenn
  der Nutzer "für beide/uns" sagt (2 Beteiligte → ÷2). Exakten Wert speichern (z. B. `6.375`), auch wenn
  die Euro-Anzeige auf Cent rundet.
- Korrigiert der Nutzer etwas am Eintrag (falscher Teiler, Laden doch genannt), Betrag **und** Titel in
  einem Zug nachziehen und erneut fetchen — Teilungszeile im Inhalt nicht vergessen.
- Teiler aus dem Diktat wörtlich übernehmen, auch wenn er zur genannten Personenzahl nicht passt —
  Betrag und Rechnung im Reply offenlegen, damit der Nutzer korrigieren kann.

## Objektname

Läden (Edeka, Dm, Picnic) sind die Regel, aber nur wenn genannt. Ohne genannten Laden **nicht raten**,
neutralen Descriptor verwenden (z. B. `Einkauf <mention-date start="…"/>`) und im Reply flaggen.

## Name Mapping

User may refer to people by nicknames. Known mappings:
- "Girkin" → Görkem
- "Görkim" → Görkem

Always use the canonical option name from the schema, not the nickname.