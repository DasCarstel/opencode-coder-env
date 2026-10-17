# Benachrichtigungen (ntfy)

Zentraler, selbst gehosteter Notification-Hub für das Home-Lab: alle Quellen
(Home Assistant, OpenCode, Hestia, Hermes, Monitoring, Skripte) publizieren nach
**ntfy**; Phone (S25 Ultra, ntfy-App) und Windows-PC (ntfy als PWA) abonnieren
nur die Topics, die sie interessieren.

ntfy wurde Gotify vorgezogen: `actions`-Buttons, Attachments und
verzögerte Zustellung sind eingebaut, und pro Quelle lässt sich ein eigener
Nutzer mit Topic-ACL anlegen (Forward-Auth über Authentik hätte die nativen
Clients der Apps gebrochen).

## ntfy ist ausschließlich Meldeweg

Seit **17.10.2026** ist ntfy nur noch **Meldeweg**: Das Plugin
(`coder-templates/shared/plugins/ntfy-notify.js`) **veröffentlicht** Meldungen
und **liest nichts** aus dem Topic. Aufs Handy geht

- die Benachrichtigung mit dem Titel `[<workspace>] …` und dem **Icon** des
  Arbeitsbereichs (`NTFY_ICON_BASE`), und
- der Knopf/Deep-Link **„Session öffnen"** — er springt direkt in die
  OpenCode-Session (`NTFY_LINK_STYLE=session`, Klickziel
  `<basis>/server/<schlüssel>/session/<id>`).

**Geantwortet wird in der OpenCode-Session** — im Chat, im Fragenfenster oder im
Freigabe-Dialog. Es gibt keinen Weg zurück: keine Brücke, kein Einspeisen von
Prompts, keine Kopplung, keine Formularantworten, keinen Zustandsspeicher und
kein Antwortfenster. Fragen und Freigaben werden unverändert **gemeldet**; es
fehlt nur der Antwortweg.

### Fehlermeldungen

Meldungen zu **echten** Fehlern bleiben (`⚠️ Fehler — <Projekt>`, Priorität `5`,
Tag `warning`). Zwei Dämpfungen verhindern Fehlalarm:

- **Kein Fehler beim eigenen Abbruch oder Dienst-Neustart.** `session.step.failed`
  trifft **vor** dem Abbruch ein, deshalb hält das Plugin die Meldung kurz
  zurück (rund 3 s) und verwirft sie, wenn in der Zwischenzeit
  `session.execution.interrupted` (der Turn wurde im Chat selbst abgebrochen)
  oder `location.shutdown` (der Dienst startet neu) eintrifft. Ein
  Abbruch-Merkmal im Ereignis verwirft sofort.
- **Eine Meldung je Session statt je Schritt.** Weitere fehlgeschlagene Schritte
  derselben Session im selben Fenster landen in **derselben** Meldung; ab dem
  zweiten Schritt steht der Zusatz `(n Fehlschläge zusammengefasst)`.

Bewusst **nicht** verschickt wird also „⚠️ Fehler" für einen Turn, den man selbst
abgebrochen hat, und für einen Neustart des Dienstes. Im Diagnose-Log
(`NTFY_REPLY_LOG`) stehen solche Fälle als `fehler-verworfen`
(`abbruch` / `dienst-neustart`).

### Warum der Antwortweg entfernt wurde (17.10.2026)

Wer in ein ntfy-Topic schreiben kann, hätte über die frühere Brücke **Prompts in
Sessions mit MCP-Rechten** einspeisen können — in diesem Container bedeutet das
SSH-Zugriff inklusive `sudo` und Authentik. Ein Topic ist kein vertrauenswürdiger
Kanal: Die Zuordnung „Nachricht → Frage/Session" war schon ohne Angreifer
unzuverlässig, und ein Schalter (`NTFY_REPLY_ENABLED`) hätte genau diesen Weg
wieder geöffnet. Deshalb **kompletter Rückbau statt Schalter** — der Antwortweg
ist aus dem Plugin entfernt, nicht bloß abgeschaltet.

Der frühere Stand (Brücke, Kopplung, Formularantworten, Zustandsdatei,
Antwortfenster, `NTFY_REPLY_ENABLED`/`NTFY_FORM_REPLY`) ist über die
**Git-Historie** und den Tag **`ntfy-antwortweg-vor-rueckbau`** auffindbar.

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
| `crit` | HA-Alarme (Wasserleck) + Regen bei offenen Fenstern | `src-ha`, `carsten` | 4 |
| `home` | HA Routine/Real (Roborock, Temp/Feuchte, Zimmer/Fenster) | `src-ha`, `carsten` | 3 |
| `doorbell` | Türklingel (mit „Tür öffnen"-Button) | `src-ha`, `carsten` | 4 |
| `print` | 3D-Druck (Job fertig/Fehler) | `src-ha`, `carsten` | 3 |
| `logs` | Uninteressante HA-Meldungen (Watchdog, CCU, GUARD, Fenster-Sensor) | `src-ha`, `carsten` | 1 |
| `gorkem` | Görkem-spezifisch (Toilette, Roborock-Standort) | `src-ha`, `carsten`, `gorkem` | 3 |
| `oc-infrastructure` | OpenCode server-management | `src-opencode`, `carsten` | 3 |
| `oc-general` | OpenCode general | `src-opencode`, `carsten` | 3 |
| `oc-minijob` | OpenCode minijob | `src-opencode`, `carsten` | 3 |
| `oc-datascience` | OpenCode uni/datascience | `src-opencode`, `carsten` | 3 |
| `hermes` | Hermes-Agent (Notifications) | `src-opencode` (Hermes), `carsten` | 3 |
| `ai` | Rückfall-Topic (nur wenn kein `NTFY_TOPIC_NAME` gesetzt) | `src-opencode`, `carsten` | 3 |

Entfallen: `infra` (Hestia/Backups/Deploys — nicht mehr genutzt), `digest`
(Sammelnachrichten — nicht mehr genutzt). Beide können auf dem Handy gelöscht werden.

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
schlägt der Publish mit `HTTP 403` fehl: im Diagnose-Log (`NTFY_REPLY_LOG`)
stehen dann die Fehlschläge, und auf dem Handy kommt **gar nichts** mehr an.

Benötigt sind aktuell zwölf Topics (siehe Tabelle oben): `crit`, `home`,
`doorbell`, `print`, `logs`, `gorkem`, `oc-infrastructure`, `oc-general`,
`oc-minijob`, `oc-datascience`, `hermes` und `ai` (Rückfall-Topic).
`infra` und `digest` entfallen und können auf dem Handy gelöscht werden.

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
| `NTFY_TOPIC_PREFIX` | Gesetzt **und** Arbeitsbereich bekannt → Topic `<prefix>-<workspace>`, z. B. `oc` + `server-management` → **`oc-server-management`**. Gilt für den Versand. Ohne Prefix oder ohne bekannten Arbeitsbereich bleibt es bei `NTFY_TOPIC`. |
| `NTFY_TOPIC_NAME` | Arbeitsbereichs-Teil des Topics, wenn das Verzeichnis keinen hergibt: der Dienst läuft im Repo-Wurzelverzeichnis, dessen Pfad enthält kein `/workspaces/`. Nur zusammen mit `NTFY_TOPIC_PREFIX`, z. B. `oc` + `infrastructure` → **`oc-infrastructure`**. Wirkt **nur** aufs Topic — `icon` und Titel-Kennung `[<name>] ` bleiben am Sitzungs-Verzeichnis. |
| `NTFY_ICON_BASE` | Gesetzt **und** Arbeitsbereich bekannt → jede Meldung trägt das ntfy-Feld `icon` = `<basis>/<workspace>.png` (JSON-Feld `icon`, als Header `Icon`; Android/iOS zeigen das Bild als Benachrichtigungssymbol). Ohne Basis entfällt das Feld. |

Der Arbeitsbereich kommt aus `NTFY_WORKSPACE`, sonst aus dem letzten Pfadstück
des Sitzungs-Verzeichnisses (`…/workspaces/infrastructure/server-management` →
`server-management`) — dieselbe Kennung, die schon als `[<name>] …` im Titel
steht. Beide Variablen werden wie alle `NTFY_*`-Werte von `startup.sh` in
`/etc/opencode.env` abgelegt; die Prozess-Umgebung hat Vorrang. Die Zeile
`[ntfy-notify] aktiv (topic …)` beim Laden des Plugins nennt immer das
tatsächlich verwendete Topic.

**Topic und Icon sind getrennt:** `NTFY_TOPIC_NAME` setzt ausschließlich den
Topic-Namen (zusammen mit `NTFY_TOPIC_PREFIX`) und wird gebraucht, weil der
OpenCode-Dienst im Repo-Wurzelverzeichnis läuft — dessen Pfad enthält kein
`/workspaces/`, aus dem Verzeichnis käme also kein Arbeitsbereich. `icon` und
die Titel-Kennung `[<name>] …` hängen weiterhin am Sitzungs-Verzeichnis bzw. an
`NTFY_WORKSPACE`; `NTFY_TOPIC_NAME` ändert daran nichts. Das Topic wird **je
Dienst-Instanz** (also je Container) abgeleitet und gilt für alle Sessions
dieses Containers — Unterordner wie `homeassistant` oder `svelte` ändern es
nicht.

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

#### Umgebungsvariablen

| Variable | Wirkung |
|----------|---------|
| `NTFY_URL` | Basis-URL des Hubs (Default `https://ntfy.mueller-nas.de`). |
| `NTFY_TOKEN` | Bearer-Token des ntfy-Nutzers `src-opencode`; ohne Token bleibt das Plugin still. |
| `NTFY_TOPIC_NAME` | Arbeitsbereichs-Teil des Topics (nur zusammen mit `NTFY_TOPIC_PREFIX`), z. B. `oc` + `infrastructure` → `oc-infrastructure`. Lässt Titel und Icon unberührt. |
| `NTFY_TOPIC_PREFIX` | Opt-in Topic je Arbeitsbereich: `<prefix>-<workspace>`; ohne Prefix bleibt es beim festen Topic (Default `ai`). |
| `NTFY_ICON_BASE` | Basis-URL der PNG-Symbole; jede Meldung bekommt `icon = <basis>/<workspace>.png?v=4`. |
| `NTFY_LINK_STYLE` | `session` (Default) = Klickziel ist der Session-Deep-Link („Session öffnen"); `base` = nur die Startseite der Oberfläche. |
| `NTFY_IGNORE_TITLES` | Kommagetrennte Titel-Teile, die stumm bleiben (z. B. `Ausfall-Probe`). |
| `NTFY_REPLY_LOG` | Pfad des Diagnose-Logs (z. B. `/tmp/opencode/ntfy-diag.log`); leer = aus. |

Alle Werte liest das Plugin zusätzlich aus der Env-Datei `/etc/opencode.env`
(Pfad über `NTFY_ENV_FILE` änderbar); die Prozess-Umgebung hat Vorrang.
`NTFY_WORKSPACE` (kurzer Name des Arbeitsbereichs, z. B. `server-management`)
steht als `[<name>] ` vor jedem Meldungstitel — ohne die Variable wird der Name
aus dem letzten Pfadstück des Sitzungs-Verzeichnisses abgeleitet, sonst bleibt
der Titel unverändert. Nur für die Diagnose: `NTFY_DEBUG=1` (Debug ins
OpenCode-Log), `NTFY_SELFTEST=1` (sendet beim Laden genau eine Testnachricht und
registriert keine Hooks).

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
  Phone bekommt dann z. B. von `home` nur Wichtiges, `logs` bleibt stumm (Prio 1).
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
  `https://ntfy.mueller-nas.de/crit` mit Header
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
- [x] ~~Phase 5: eigener `notify-gateway` (Antworten A/B/C)~~ — **entfallen**
      (17.10.2026, siehe „ntfy ist ausschließlich Meldeweg"). Offen bleiben:
      Windows-Desktop-Client und Stream-Deck-Plugin.
