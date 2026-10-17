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

## ntfy-Berechtigungen (ACL)

ntfy prüft den Zugriff **je Topic**: ein Token darf nur die Topics lesen und
beschreiben, für die sein Nutzer freigeschaltet ist. Der Publish-/Lese-Token der
Agenten gehört zum ntfy-Nutzer **`src-opencode`**, und sein Zugriff muss für
**jedes** Topic gesetzt sein, das ein Workspace benutzt. Fehlt die Freigabe,
schlägt der Publish mit `HTTP 403` fehl **und** die Antwort-Brücke kann nicht
streamen (`fehler: strom: HTTP 403`): das Diagnose-Log füllt sich mit 403ern,
und auf dem Handy kommt **gar nichts** mehr an.

Benötigt sind aktuell sechs Topics: `ai` (Rückfall-Topic, solange kein
`NTFY_TOPIC_NAME` gesetzt ist), `oc-infrastructure`, `oc-general`, `oc-minijob`,
`oc-datascience` und `hermes`.

```bash
# auf dem Docker-Host: je Topic einmal setzen (<topic> ersetzen)
docker exec ntfy ntfy access src-opencode <topic> rw

# Kontrolle: alle Nutzer mit ihren Topic-Rechten
docker exec ntfy ntfy access
```

Die Rechte landen in der ntfy-Nutzerdatenbank (`auth.db`) und überleben einen
Neustart des Containers.

**Regel:** Beim Anlegen eines neuen Workspace-Topics gehört die Rechtevergabe
dazu — **erst die ACL setzen, dann das Topic in `startup.sh`/`/etc/opencode.env`
scharf schalten** (sonst läuft der Publisher in die 403er von oben).

**Ein Topic-Wechsel besteht aus zwei Teilen:** (1) die Plugin-Datei
`/root/.config/opencode/plugins/ntfy-notify.js` aus
`coder-templates/shared/plugins/` nachziehen — ein Container-Start erledigt das
automatisch (`startup.sh`), im laufenden Betrieb muss die Datei von Hand
nachgezogen werden; (2) die ACL für das neue Topic setzen.

## Topics und Icons

Das OpenCode-Plugin (`coder-templates/shared/plugins/ntfy-notify.js`) kann je
Arbeitsbereich ein eigenes Topic bespielen und die Meldung mit dem Icon des
Bereichs versehen. Beides ist **opt-in** — ohne die neuen Variablen bleibt alles
wie bisher (`NTFY_TOPIC`, Default `ai`), damit keine Meldung in ein Topic
rutscht, das niemand abonniert hat.

| Einstellung | Wirkung |
|-------------|---------|
| `NTFY_TOPIC_PREFIX` | Gesetzt **und** Arbeitsbereich bekannt → Topic `<prefix>-<workspace>`, z. B. `oc` + `server-management` → **`oc-server-management`**. Gilt einheitlich für den Versand **und** die Antwort-Brücke (die hört auf demselben Topic). Ohne Prefix oder ohne bekannten Arbeitsbereich bleibt es bei `NTFY_TOPIC`. |
| `NTFY_TOPIC_NAME` | Arbeitsbereichs-Teil des Topics, wenn das Verzeichnis keinen hergibt: der Dienst läuft im Repo-Wurzelverzeichnis, dessen Pfad enthält kein `/workspaces/`. Nur zusammen mit `NTFY_TOPIC_PREFIX`, z. B. `oc` + `infrastructure` → **`oc-infrastructure`**. Wirkt **nur** aufs Topic — `icon` und Titel-Kennung `[<name>] ` bleiben am Sitzungs-Verzeichnis. |
| `NTFY_ICON_BASE` | Gesetzt **und** Arbeitsbereich bekannt → jede Meldung trägt das ntfy-Feld `icon` = `<basis>/<workspace>.png` (JSON-Feld `icon`, als Header `Icon`; Android/iOS zeigen das Bild als Benachrichtigungssymbol). Ohne Basis entfällt das Feld. |

Der Arbeitsbereich kommt aus `NTFY_WORKSPACE`, sonst aus dem letzten Pfadstück
des Sitzungs-Verzeichnisses (`…/workspaces/infrastructure/server-management` →
`server-management`) — dieselbe Kennung, die schon als `[<name>] …` im Titel
steht. Beide Variablen werden wie alle `NTFY_*`-Werte von `startup.sh` in
`/etc/opencode.env` abgelegt; die Prozess-Umgebung hat Vorrang. Die
Diagnosezeile beim Start der Brücke und die Zeile `[ntfy-notify] aktiv (topic …)`
nennen immer das tatsächlich verwendete Topic.

**Topic und Icon sind getrennt:** `NTFY_TOPIC_NAME` setzt ausschließlich den
Topic-Namen (zusammen mit `NTFY_TOPIC_PREFIX`) und wird gebraucht, weil der
OpenCode-Dienst im Repo-Wurzelverzeichnis läuft — dessen Pfad enthält kein
`/workspaces/`, aus dem Verzeichnis käme also kein Arbeitsbereich. `icon` und
die Titel-Kennung `[<name>] …` hängen weiterhin am Sitzungs-Verzeichnis bzw. an
`NTFY_WORKSPACE`; `NTFY_TOPIC_NAME` ändert daran nichts. Versand und
Antwort-Brücke leiten das Topic über dieselbe Funktion ab und bleiben damit auf
demselben Topic.

**Topics:** Schema `oc-<workspace>` je Arbeitsbereich (`oc-server-management`,
später z. B. `oc-cleve`); läuft der Dienst im Repo-Wurzelverzeichnis, gibt
`NTFY_TOPIC_NAME` den Namen vor (z. B. `oc-infrastructure`). Hermes hat ein
**eigenes Topic `hermes`** und läuft
**nicht** über dieses Plugin (eigene Quelle, eigenes Topic in ntfy). Neue
Topics entstehen beim ersten Publizieren, brauchen aber vorher Nutzer/ACL —
siehe „Topics und Scopes“.

**Icons:** die PNGs liegen im Repo unter `assets/ntfy-icons/<name>.png`
(256×256, generiert von `tools/make-ntfy-icons.py`, Details in
`assets/ntfy-icons/README.md`). ntfy lädt sie über die öffentliche Roh-URL,
deshalb als Basis:

```
NTFY_ICON_BASE=https://raw.githubusercontent.com/DasCarstel/opencode-coder-env/master/assets/ntfy-icons
```

Damit wird z. B. `…/assets/ntfy-icons/server-management.png` als `icon`
mitgeschickt (der Name muss dem Arbeitsbereich entsprechen; fehlt die Datei,
zeigt die App kein Symbol).

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

Ergänzend: `NTFY_WORKSPACE` (kurzer Name des Arbeitsbereichs, z. B.
`server-management`) steht als `[<name>] ` vor jedem Meldungstitel — ohne die
Variable wird der Name aus dem letzten Pfadstück des Sitzungs-Verzeichnisses
abgeleitet, sonst bleibt der Titel unverändert. Den **Topic-Namen** setzt
`NTFY_TOPIC_NAME` (nur zusammen mit `NTFY_TOPIC_PREFIX`); die Variable lässt
Titel und Icon unberührt. Meldungen **ohne** Session
(Antwort-Bestätigungen, „Antwort ohne Ziel-Session“) nutzen für den
Kopplungslink zuerst die zuletzt abgeleitete Workspace-Basis (`lastBase` in
`NTFY_STATE_FILE`, überlebt einen Dienst-Neustart) und erst danach
`NTFY_FALLBACK_URL` — nur auf der App-Adresse des Workspace gilt das
Kopplungs-Cookie.

#### Antwort vom Handy (Antwort-Brücke)

Der Text, den man in der ntfy-App als Nachricht ins Topic schickt, geht an die
Session, die zuletzt etwas gemeldet hat (`lastSessionId`, überlebt einen
Dienst-Neustart in `NTFY_STATE_FILE`; Fallback: die zuletzt gesehene Session).

**Offene Frage:** Steht im Agenten eine Frage offen, beantwortet der Text genau
diese Frage — der blockierte Turn läuft weiter, als hätte man im Fragenfenster
geantwortet.

- Die Frage-Meldung trägt je Frage eine Kopfzeile `Frage i von n` und eine
  eigene Optionsliste (`1) …`).
- Bei mehreren offenen Fragen wird **der Reihe nach** beantwortet: immer die
  **älteste** offene Frage, die übrigen bleiben offen — es wird nichts mehr
  abgebrochen. Die Bestätigung nennt, welche Frage beantwortet wurde und welche
  noch offen ist: `Antwort gespeichert (Frage 1 von 3). Offen: Frage 2 von 3.`
- Passt der Text auf **keine** Option, gilt er als **Freitext-Antwort** auf diese
  Frage — abgebrochen wird deswegen nichts.
- Erst wenn die API die Antwort ablehnt (z. B. HTTP 409 „schon beantwortet“),
  geht der Text zusätzlich als normale Nachricht an den Agenten; die Frage
  bleibt offen.
- Eine leere Nachricht löst nur einen Hinweis aus.

**Ohne offene Frage:** Der Text gilt als **neue Anweisung** an die Session der
letzten Meldung — aber nur, wenn beides stimmt:

1. die Nachricht ist **nach** unserer letzten eigenen Meldung im Topic
   eingegangen (deren Sendezeit steht als `lastSentTime` in `NTFY_STATE_FILE`), und
2. sie liegt höchstens **`NTFY_REPLY_WINDOW_MIN`** Minuten danach (Default `30`).

Sonst wird sie verworfen statt eingespeist. Unverändert scharf bleiben dabei die
Schutzregeln: eigene Nachrichten (Titel-Präfix `❓ ✅ ⚠️ 🔐 🔔 ↩️`, eigene
Message-IDs, eigener Wortlaut) werden ignoriert, der Wasserstand
`lastHandledTime` hält alten Verlauf draußen und die Drossel erlaubt höchstens
zwei Einspeisungen je Minute. Eine offene Frage hat immer Vorrang: solange eine
Frage offen ist, gilt ein Text als Antwort darauf und nie als Prompt.

Abgebrochene Fragen (`form.cancelled`, `question.cancelled`) räumen den Zustand
auf: `openQuestionId`, `openQuestionAt` und `openFormId` werden geleert — sonst
hielte die Brücke eine längst erledigte Frage für offen.

| Einstellung | Wirkung |
|-------------|---------|
| `NTFY_REPLY_WINDOW_MIN` | Antwortfenster in Minuten (Default `30`): so lange gilt eine Nachricht ohne offene Frage noch als neue Anweisung an die letzte meldende Session. `0` schaltet die Zeitprüfung ab — dann zählt jede Nachricht nach der eigenen letzten Meldung. |

Kürzel im Diagnose-Log der Brücke (`NTFY_REPLY_LOG`): `form-reihenfolge`
(mehrere offene Fragen — die älteste wird beantwortet), `form-reply`
(Optionsantwort), `form-reply-rohtext` (Freitext-Antwort), `form-ohne-treffer`
(konservativ nicht beantwortbar, der Text geht als Nachricht weiter),
`ohne-offene-frage` / `vor-eigener-meldung` / `antwortfenster` (ohne offene
Frage verworfen).

#### Aktivieren

1. OpenBao: `secret/data/mcp/notify` mit `url` und `opencode_token` (Token des
   Nutzers `src-opencode`) — **angelegt am 16.10.2026**; die AppRole `mcp-server`
   liest `secret/data/mcp/*` und deckt den Pfad ab. Weitere Felder für später:
   `hestia_token`, `ha_token`, `monitoring_token`.
2. `startup.sh` installiert das Plugin nach `~/.config/opencode/plugins/` und
   schreibt `NTFY_TOKEN`/`NTFY_URL` nach `/etc/opencode.env` (0600).
3. Prüfen: in einer Session eine Aufgabe laufen lassen — beim Fertigwerden kommt
   die Push-Nachricht.

**Reihenfolge der Token-Auflösung:** `NTFY_TOKEN`/`NTFY_URL` (Prozess-Env) →
sonst `/etc/opencode.env` (Pfad über `NTFY_ENV_FILE` änderbar) → Default-URL.

> **Fallstrick (empirisch):** `opencode service set env NTFY_TOKEN …` schreibt den
> Wert zwar in `service.json`, aber im laufenden `serve --service`-Prozess kam er
> **nicht** im environ an — deshalb liest das Plugin zusätzlich die Env-Datei.
> Der Token muss also nicht mehr im Service-Env stehen.

**Plugin-Datei ändern genügt:** der Service lädt Plugins bei Aktivität neu
(kein Neustart nötig); nur Änderungen an `/etc/opencode.env` bzw. am Secret
brauchen einen Neustart des Containers/Service, weil die Datei nur von
`startup.sh` geschrieben wird.

Diagnose im Container (Token aus der Env-Datei, echte Nachricht):

```bash
# 1. Beweis über den ntfy-Cache (Admin-Login carsten, auf dem Docker-Host)
PW=$(sed -n 's/^NTFY_PASSWORD_CARSTEN=//p' /etc/docker/ntfy/credentials.env)
curl -sS -u "carsten:$PW" "https://ntfy.mueller-nas.de/ai/json?poll=1&since=1h"

# 2. Selbsttest gegen einen lokalen Sink statt ntfy
NTFY_SELFTEST=1 NTFY_URL=http://127.0.0.1:8789 NTFY_TOKEN=tk_dummy \
  timeout 20 opencode serve --port 49399     # braucht Aktivität, sonst lädt kein Plugin
```

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

- [ ] Hermes-Webhook → ntfy (Hermes läuft auf dem Coder-Host).
- [ ] HA-Routinenmeldungen (26) auf ntfy umstellen und `mobile_app`-Duplikate entfernen.
- [ ] Phase 5: eigener `notify-gateway` (Antworten A/B/C), Windows-Desktop-Client,
      Stream-Deck-Plugin.
