# Benachrichtigungen (ntfy)

Zentraler, selbst gehosteter Notification-Hub für das Home-Lab: alle Quellen
(Home Assistant, OpenCode, Hestia, Hermes, Monitoring, Skripte) publizieren nach
**ntfy**; Phone (S25 Ultra, ntfy-App) und Windows-PC (ntfy als PWA) abonnieren
nur die Topics, die sie interessieren.

ntfy wurde Gotify vorgezogen: Antwort-Buttons/`actions`, Attachments und
verzögerte Zustellung sind eingebaut, und pro Quelle lässt sich ein eigener
Nutzer mit Topic-ACL anlegen (Forward-Auth über Authentik hätte die nativen
Clients der Apps gebrochen).

## Bausteine

| Baustein | Ort |
|----------|-----|
| Stack (Compose) | `/opt/docker-compose/ntfy/docker-compose.yml` — Docker-Host CT 100 |
| Daten + Credentials | `/etc/docker/ntfy/` (`credentials.env`, Mode 0600, **nicht** im Repo) |
| Publish-Helfer | `/etc/docker/ntfy/publish.sh` |
| URL | `https://ntfy.mueller-nas.de` (Traefik + Cloudflare-Wildcard, **kein** Authentik-Forward-Auth) |
| OpenCode-Plugin | `coder-templates/shared/plugins/ntfy-notify.js` |
| Plugin-Tests | `node coder-templates/shared/plugins/ntfy-notify.test.mjs` |

Image: `binwiederhier/ntfy:v2.31.0`, `NTFY_AUTH_DEFAULT_ACCESS=deny-all`
(anonymes Lesen/Schreiben ist verboten), Auth-DB `/var/lib/ntfy/auth.db`,
Cache 24 h, Attachment-Limit 1 GB (Disk auf CT 100 ist knapp).

## Topics und Scopes

| Topic | Zweck | Schreibberechtigt | Empfehlung `min_priority` |
|-------|-------|-------------------|---------------------------|
| `crit` | Ausfälle/Störungen (Monitoring, HA-Alarme) | `src-monitoring`, `src-ha`, `carsten` | 1 |
| `ai` | Agenten (OpenCode fertig/Frage/Freigabe/Fehler) | `src-opencode`, `carsten` | 3 |
| `infra` | Infrastruktur (Hestia, Backups, Deploys) | `src-hestia`, `src-monitoring`, `carsten` | 3 |
| `home` | Smart Home (HA-Zustände, Routinen) | `src-ha`, `carsten` | 2 |
| `print` | 3D-Druck (Job fertig/Fehler) | `src-ha`, `carsten` | 3 |
| `digest` | Sammelnachrichten (Tages-/Wochenüberblick) | `carsten` | 2 |

Nutzer: `carsten` (Admin, lesen auf allen Topics) plus je Quelle ein eigener
Nutzer mit Schreibrecht nur auf die eigenen Topics. **ntfy-Tokens sind nicht
granular** — ein Token hat immer die Rechte seines Nutzers, deshalb ein Nutzer
pro Quelle statt eines Sammel-Tokens.

Topics entstehen beim ersten Publizieren automatisch; es gibt keine
`topic allow`-Pflege. Der Schutz kommt aus Nutzer + ACL + `deny-all`.

## Publizieren

### Skript oder `curl`

```bash
# auf dem Docker-Host (Token kommt aus /etc/docker/ntfy/credentials.env)
/etc/docker/ntfy/publish.sh src-opencode ai "Deploy fertig" 3 "Deploy" white_check_mark

# von überall mit JSON (empfohlen für Emojis in Titel/Text)
. /etc/opencode.env            # NTFY_TOKEN (nur nach Aktivierung, s. u.)
curl -sS -X POST "https://ntfy.mueller-nas.de" \
  -H "Authorization: Bearer $NTFY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"topic":"ai","title":"✅ Fertig","message":"Text","priority":3,"tags":["white_check_mark"]}'
```

Prioritäten: `1` min · `2` low · `3` default · `4` high · `5` max/urgent.
`click` (URL beim Antippen) und `actions` (Buttons) sind Felder der
JSON-Nutzlast — das Plugin nutzt `click` für den Sprung in die Coder-Session.

### Home Assistant

`rest_command.ntfy` in `/etc/docker/homeassistant/configuration.yaml` (Token als
`!secret ntfy_ha_token`), Aufruf z. B. aus Automationen/Skripten:

```yaml
action: rest_command.ntfy
data:
  topic: home
  title: "Waschmaschine fertig"
  message: "Bitte Wäsche umhängen"
  priority: 3
  tags: washing_machine
```

### OpenCode (automatisch, V2-Plugin)

Das Plugin meldet ohne Zutun der Agenten:

| Event | Nachricht | Priorität | Tags |
|-------|-----------|-----------|------|
| `session.idle` (nach echter Arbeit) | `✅ Fertig — <Projekt>` + TLDR der letzten Antwort | 3 | `white_check_mark` |
| `question.asked` | `❓ Frage — <Projekt>` + Frage und Optionen | 4 | `question` |
| `permission.asked` | `🔐 Freigabe — <Projekt>` + Grund/Befehl | 4 | `lock` |
| `session.error` | `⚠️ Fehler — <Projekt>` | 5 | `warning` |

Eigenschaften:

- Topic default `ai`, Klick-URL zeigt auf die Coder-App der Workspace
  (`https://herdr--<workspace>--<owner>-coder.mueller-nas.de`).
- Subagent-Sessions (`parentID`) werden geschluckt — nur die Eltern-Session meldet.
- Leere Sessions lösen nichts aus (gemeldet wird nur, wenn die Session vorher
  wirklich gearbeitet hat).
- Ohne Token bleibt das Plugin still, es bricht nichts.
- Dedup-Fenster 15 s je Session + Art.

Umgebungsvariablen: `NTFY_URL`, `NTFY_TOKEN`, `NTFY_TOPIC`,
`NTFY_DEBUG=1` (Debug ins OpenCode-Log), `NTFY_SELFTEST=1` (sendet beim Laden
genau eine Testnachricht und registriert keine Hooks — für Diagnose).

#### Aktivieren

1. OpenBao: `secret/data/mcp/notify` mit `url` und `opencode_token`
   (Token des Nutzers `src-opencode`) anlegen — siehe
   [`secrets.md`](secrets.md); die AppRole braucht Lesezugriff auf den Pfad.
2. Container bzw. OpenCode-Service neu starten: `startup.sh` installiert das
   Plugin nach `~/.config/opencode/plugins/ntfy-notify.js` und setzt
   `NTFY_TOKEN`/`NTFY_URL` als Service-Env.
3. Prüfen: in einer Session eine Aufgabe laufen lassen — beim Fertigwerden kommt
   die Push-Nachricht. Ohne Neustart passiert nichts, weil die Service-Umgebung
   nur beim Start gelesen wird.

Schnelle Diagnose ohne Neustart (im Container, mit lokalem HTTP-Sink statt ntfy):

```bash
node -e 'require("http").createServer((q,s)=>{let b="";q.on("data",c=>b+=c);q.on("end",()=>{console.error(b);s.end("{}")})}).listen(8789,"127.0.0.1")' &
NTFY_SELFTEST=1 NTFY_URL=http://127.0.0.1:8789 NTFY_TOKEN=tk_dummy \
  timeout 20 opencode serve --port 49399
# erwartet: im Sink erscheint die JSON-Nutzlast
```

## V2-Plugin-Fallstricke (wichtig für Erweiterungen)

Empirisch im Container (OpenCode 2.0.29) geprüft:

- **V1-Plugins laufen in V2 nicht mehr.** Die alte Form
  `export default { id, server: fn }` wird geladen, aber nie ausgeführt. In V2
  braucht es `export default { id, async setup(ctx) {} }`; Events kommen aus
  `ctx.event.subscribe({ signal })` (asynchroner Iterator), Prompt-Hooks aus
  `ctx.session.hook("prompt", …)`. Altcode prüfen mit
  [`migrate-v1`](https://opencode.ai/v2/docs/build/plugins/migrate-v1).
- **Plugins werden je Location instanziiert.** Im Infrastructure-Container sind
  das sechs (`/`, `/root` und je Workspace-Ordner). Jede Instanz abonniert den
  *globalen* Event-Stream und sieht dieselben Events — ohne gemeinsamen Zustand
  gäbe es sechs Benachrichtigungen pro Event. Das Plugin legt seinen Zustand
  deshalb an `globalThis` (`Symbol.for("das.ntfy-notify.state")`).
- **Plugins in `~/.config/opencode/plugins/*.js` werden automatisch geladen**
  (nicht nur unter `.opencode/plugins/`), aber erst **bei Aktivität** der
  Location, nicht zwingend beim Start des Servers.
- **Nur der Service meldet.** TUI-, `run`- und Web-Prozesse laden dasselbe
  Plugin; ohne Filter (`serve`-Erkennung über `process.argv`) käme jede Meldung
  mehrfach.
- **Plugin-Logs:** `~/.local/share/opencode/log/opencode.log`, gefiltert z. B.
  `grep -i -e ntfy-notify -e "loading plugin"`. Nur Lade-/Fehlerzeilen des
  Laufzeitsystems sind dort zuverlässig; eigene `console.*`-Ausgaben nicht —
  deshalb Diagnose über Selbsttest + Sink.
- Plugin-Datei ändern genügt: der laufende Service lädt sie bei der nächsten
  Aktivität neu (kein Neustart nötig) — **Änderungen an `NTFY_TOKEN` o. ä.
  brauchen aber einen Neustart**, weil die Umgebung beim Start gelesen wird.

## Filtern gegen Nachrichtenflut

- Pro Topic in der ntfy-App ein **`min_priority`** setzen (Tabelle oben): das
  Phone bekommt dann z. B. von `home` nur Wichtiges, `digest` bleibt stumm.
- `crit` bleibt ungefiltert (Ausfälle dürfen nie untergehen).
- Ein Topic pro Zweck statt eines Sammel-Topics — Filter und später „Zustellen
  nur zu diesen Zeiten“ (ntfy: *Do not disturb*) greifen pro Topic.
- Der Hub ersetzt nicht die HA-`mobile_app`-Benachrichtigungen: Doppelmeldungen
  sind zu vermeiden (siehe Phase 4 — Umstellung der 26 Routinenachrichten in
  `/etc/docker/homeassistant/automations.yaml`).

## Monitoring anbinden (UI-Schritte)

- **Uptime Kuma:** Benachrichtigungstyp *ntfy* → Server
  `https://ntfy.mueller-nas.de`, Topic `crit`, Token des Nutzers
  `src-monitoring` (Bearer).
- **Grafana:** Contact Point *webhook* →
  `https://ntfy.mueller-nas.de/infra` mit Header
  `Authorization: Bearer <Token src-monitoring>` (Grafana hat keine
  Provisioning-Dateien, daher im UI).

## Troubleshooting

| Symptom | Ursache/Prüfung |
|---------|-----------------|
| `404 page not found` | Traefik hat keinen Router gematcht (ntfy läuft dann trotzdem). Bei Route-Fragen `/etc/docker/traefik/rules/` prüfen. |
| `403` beim Publizieren | Token/Nutzer hat kein Schreibrecht auf das Topic (ACL) oder `NTFY_AUTH_DEFAULT_ACCESS` greift (Token fehlt). |
| Keine Push, Publish aber `200` | In der App das Topic abonniert? `min_priority` zu hoch? Do-Not-Disturb aktiv? |
| Plugin meldet nichts | `NTFY_TOKEN` im Service-Env? (`opencode service set env` bzw. `secret/data/mcp/notify` + Neustart). Sonst Selbsttest-Ablauf oben. |
| Meldung mehrfach | Zustand nicht geteilt (Instance je Location) — siehe V2-Fallstricke. |

## Noch offen

- [ ] OpenBao-Migration aller ntfy-Tokens nach `secret/data/mcp/notify`
      (braucht Admin-Token; heute liegt alles in `/etc/docker/ntfy/credentials.env`).
- [ ] Hermes-Webhook → ntfy (Hermes läuft auf dem Coder-Host).
- [ ] HA-Routinenmeldungen (26) auf ntfy umstellen und `mobile_app`-Duplikate entfernen.
- [ ] Phase 5: eigener `notify-gateway` (Antworten A/B/C), Windows-Desktop-Client,
      Stream-Deck-Plugin.
