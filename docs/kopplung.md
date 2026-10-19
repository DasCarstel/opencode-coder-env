# Kopplungslink für die OpenCode-Web-UI

Die OpenCode-Web-UI (Port-App je Workspace) liefert ihre statischen Dateien
ohne Anmeldung aus, verlangt aber für **jede** `/api/*`-Anfrage HTTP-Basic.
Ohne Zugangsdaten bleibt die Oberfläche leer bzw. hängt bei „Connect to
Server" — im Server-Log pollt der Client dann alle 10 s `/api/info` und
erhält `401`.

Lösung: **Kopplungslink**. Einmalig pro Gerät einen kurzlebigen Kopplungscode
erzeugen und im Browser einlösen — das Backend setzt ein 30-Tage-Cookie. Das
ersetzt kein Login: Der Zugang zur Port-App läuft weiterhin über
Coder/Authentik, das Basic-Auth-Passwort kommt aus OpenBao.

## Port-App-URL-Muster

| Bestandteil | Wert |
|-------------|------|
| Muster | `https://<port>--<agent>--<workspace>--<owner>-<coder-host>` |
| Hier | Port `49374`, Agent `main` |
| `<coder-host>` | Domain-Teil aus `CODER_AGENT_URL`, Default `coder.mueller-nas.de` |
| Zugang | Über Coder/Authentik — den Kopplungslink erst **nach dem Login** öffnen |

## Kopplungscode erzeugen

`POST /api/pair` (mit Basic-Auth am OpenCode-Server) liefert einen kurzlebigen,
einmaligen Code:

```bash
# gegen die Port-App (im Container: http://localhost:49374/api/pair)
curl -s -u "opencode:$OPENCODE_SERVER_PASSWORD" -X POST https://<port-app>/api/pair \
  -H "Accept: application/json"
# → {"code":"...","expires_in":300}
```

`expires_in: 300` — der Code lebt **5 Minuten** und wird mit der ersten
Einlösung verbraucht.

**Passwort:** `OPENCODE_SERVER_PASSWORD` liegt in OpenBao unter
`secret/data/mcp/hermes-opencode-service` (Feld `password`). In Befehlen nur
als `$OPENCODE_SERVER_PASSWORD` referenzieren, niemals einen Wert ausschreiben.

## Kopplungscode einlösen

Im Browser (nach dem Authentik-Login) die Kopplungs-URL öffnen:

```
GET https://<port-app>/auth/connect/<code>
```

| Aufruf | Antwort |
|--------|---------|
| Browser | `302 Location: /` plus Cookie `opencode_session_49374` — `Max-Age=2592000` (30 Tage), `HttpOnly`, `SameSite=Lax` |
| mit `Accept: application/json` | `{"token":"..."}` — der Token ist als Passwort verwendbar |

## Kopplungslink per ntfy verschicken

```bash
. /etc/opencode.env            # NTFY_TOKEN (Nutzer src-opencode)
LINK="https://<port-app>/auth/connect/<code>"
curl -sS -X POST "https://ntfy.mueller-nas.de/oc-<workspace>" \
  -H "Authorization: Bearer $NTFY_TOKEN" \
  -H "Title: 🔑 OpenCode koppeln" \
  -H "Tags: key" \
  -H "Priority: high" \
  -H "Click: $LINK" \
  -H "Actions: view, Verbinden, $LINK, clear=true" \
  -H "X-Expires: 10m" \
  -d "$LINK"
```

Der Link steht im Textkörper, `Click` und `Actions` öffnen ihn beim Antippen.
`X-Expires: 10m` räumt die Meldung weg, wenn der 5-Minuten-Code ohnehin
abgelaufen ist. Topics und ACL: siehe `docs/notifications.md`.

## Hinweise

- **Pro Gerät/Browser:** die Kopplung gilt je Gerät — auf einem neuen Gerät
  oder nach Ablauf (30 Tage) einfach einen frischen Code erzeugen und einlösen.
- **Einmalig:** ein zweiter Aufruf mit demselben Code schlägt fehl.
- Der Code lebt nur 5 Minuten — erst erzeugen, wenn das Zielgerät bereit ist.

## Troubleshooting

| Symptom | Ursache/Prüfung |
|---------|-----------------|
| Oberfläche leer bzw. „Connect to Server" | Kopplung fehlt — Gegenprobe im Server-Log `/root/.local/share/opencode/log/opencode.log` (`http.url=/api/info http.status=401`, alle 10 s). Frischen Code erzeugen und einlösen (oben). |
| Einlösen schlägt fehl | Code abgelaufen (5 Minuten) oder bereits verbraucht — er ist einmalig. Neuen Code erzeugen. |
| Kopplungsseite nicht erreichbar | Zuerst Coder/Authentik-Login — die Port-App ist erst danach erreichbar. |
