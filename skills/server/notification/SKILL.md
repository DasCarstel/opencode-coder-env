---
name: notification
description: Nachricht an den ntfy-Hub (Phone/Windows-PC) senden — für lange Läufe, fertige Aufträge oder wenn eine Rückfrage offen bleibt. Load when a task should proactively notify the user.
license: MIT
---

# Benachrichtigungen senden (ntfy)

Der Hub läuft selbst gehostet unter `https://ntfy.mueller-nas.de`; Phone und
Windows-PC abonnieren die Topics. Details, Prioritäten und Troubleshooting:
[`docs/notifications.md`](../../../docs/notifications.md).

## Wann benachrichtigen

- **Ja:** lange Aufträge sind fertig, ein Ergebnis braucht die Aufmerksamkeit
  des Nutzers, eine Automation scheitert wiederholt, eine Rückfrage bleibt offen.
- **Nein:** Zwischenstände, alles was der Nutzer ohnehin gerade am Bildschirm
  sieht, mehr als eine Nachricht pro Auftrag.

Prioritäten: `3` normal, `4` Rückfrage/Freigabe, `5` Fehler. Für Routine nur `2`.

## Senden

```bash
. /etc/opencode.env 2>/dev/null    # NTFY_TOKEN (vom Workspace-Startup gesetzt)

curl -sS -X POST "https://ntfy.mueller-nas.de" \
  -H "Authorization: Bearer $NTFY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"topic":"ai","title":"✅ Auftrag fertig","message":"Kurz, was passiert ist.","priority":3,"tags":["white_check_mark"]}'
```

- `topic`: `ai` (Agenten), `infra` (Infrastruktur/Deploys), `crit` (Störungen),
  `home`, `print`, `digest` (Sammeln).
- Immer JSON mit `Content-Type: application/json` senden — Emojis in Titel und
  Text überstehen so die Übertragung.
- Optional: `click` (URL, die beim Antippen geöffnet wird), `actions`
  (Buttons), `tags` (Emoji-Kurznamen wie `warning`, `lock`, `test_tube`).
- `$NTFY_TOKEN` darf **nie** in Dateien, Commits oder Ausgaben landen.

## Ohne Token

Ist `NTFY_TOKEN` leer, ist der Hub für diesen Workspace nicht freigeschaltet
(OpenBao `secret/data/mcp/notify` fehlt bzw. der Workspace darf es nicht lesen).
Dann **nicht** improvisieren, sondern dem Nutzer sagen, dass die Freischaltung
fehlt.
