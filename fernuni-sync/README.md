# fernuni-sync

Spiegelt Moodle-Kurse der FernUni Hagen als Obsidian-Notizen **plus** Material in einen
oCIS-(OpenCloud-)Space. Ein Lauf liest die Kursseiten, erzeugt pro Abschnitt eine Notiz,
lädt `resource`/`folder`-Dateien herunter und hängt einen Block an das Sync-Log an.
Nur Python-Standardbibliothek (`urllib`, `http.cookiejar`, `xml.etree`, `re`, `json`).

---

## Status (Stand 2026-10-15) – live abgenommen

Offline: **108 Prüfungen grün** (`python3 fernuni-sync/selftest.py`).
Live gegen Moodle und oCIS verifiziert:

| Schritt | Ergebnis |
|---|---|
| `sync.py --only 6556 --dry-run --verbose` | Exit **0**, keine Schreibzugriffe; Abschnitt 1 „Data Engineering für Data Science" mit **2 Aktivitäten**: `helixmedia` „Intro (Video)" und `resource` „Folien-Intro.pdf" (1.7 MB) |
| `sync.py --only 6556 --limit-sections 1` (echt) | 1 Download (1.7 MB), Notiz + Index geschrieben, Sync-Log +376 B |
| derselbe Lauf ein zweites Mal | **0 Downloads**, „= unveraendert, kein Download", `created: 2026-10-15` unverändert, PDF-ETag identisch |
| WebDAV (PROPFIND/HEAD) | `…/Moodle/01-Data-Engineering-fuer-Data-Science.md` 2083 B, `…/Moodle/_Moodle-Index.md` 1009 B, `…/Moodle/Material/01-Data-Engineering-fuer-Data-Science/Folien-Intro.pdf` 1815433 B |
| `90-Meta/01-Sync-Log.md` | wird angehängt (2 Läufe → 2 `fernuni-sync`-Blöcke, 1715 B) |

**Wichtig – die frühere „SSO-Zwang"-Diagnose war falsch:** Ursache war die
**Shell-Expansion von `$`** in den env-Dateien. `MD_PW` (`…$0ur9FvihFw6`) und
`FERNUNI_SPACE_ID` (`…$de5001bf…`) wurden beim `set -a; . datei` verstümmelt; mit dem
verkürzten Passwort leitet Moodle in den SSO-Fluss und antwortet mit einer IdP-Seite –
das sah wie ein erzwungener SAML-Login aus. Mit korrekt gequoteten Werten
(einfache Quotes in der env-Datei oder `--env-file`) funktioniert das **lokale**
Loginformular direkt.

---

## Installation

Keine. Es wird ausschließlich die Standardbibliothek verwendet (Python ≥ 3.8).
```bash
cd fernuni-sync
python3 sync.py --help
```

## Umgebungsvariablen

Secrets **nur** aus der Umgebung. Fehlt eine Pflichtvariable → klare Meldung + Exit 2.
Nichts wird in Dateien, Logs oder das Repo geschrieben.

| Variable | Pflicht | Bedeutung |
|---|---|---|
| `MD_USER` | ja | Moodle-Benutzername (z. B. `q1572644`) |
| `MD_PW` | ja | Moodle-Passwort |
| `MD_BASE` | ja | Moodle-Basis-URL, z. B. `https://moodle.fernuni-hagen.de` |
| `FERNUNI_OCIS_USER` | ja | oCIS-Benutzer |
| `FERNUNI_OCIS_TOKEN` | ja | oCIS-Passwort/App-Token (Basic-Auth) |
| `FERNUNI_SPACE_ID` | ja | Space-ID (Graph `me/drives` → `id`) |
| `FERNUNI_OCIS_BASE` | nein | oCIS-Basis-URL (Default `https://opencloud.mueller-nas.de`) |
| `MD_DEBUG` | nein | `1` = Login-Schritte nach stderr protokollieren (ohne Passwort) |

> **`$` in Werten schützen!** Enthält ein Wert ein `$` (Passwort, Space-ID), muss es vor
> der Shell geschützt sein – sonst zerstört die Expansion die Zugangsdaten und der Login
> landet im SSO-Fluss:
> ```bash
> # env-Datei: entweder  MD_PW='…$0ur9FvihFw6'   (einfache Quotes)
> #            oder      MD_PW="…\$0ur9FvihFw6"  (\$ maskieren)
> set -a; . /pfad/moodle.env; . /pfad/fernuni-obsidian.env; set +a
> python3 sync.py --only 6556 --dry-run --verbose
> ```
> Alternativ `--env-file` verwenden: `sync.py` liest die Datei dann selbst (Werte aus der
> Datei haben Vorrang). Eine Warnung erscheint, wenn `FERNUNI_SPACE_ID` kein `$` enthält.

## CLI

| Flag | Bedeutung |
|---|---|
| `--config PATH` | Pfad zur `config.json` (Default: neben `sync.py`) |
| `--only <kursId>` | nur diesen Kurs verarbeiten, mehrfach angebbar; unbekannte ID → Exit 2 |
| `--dry-run` | **keine Schreibzugriffe** (kein PUT/MKCOL, kein Log-Eintrag); liest aber Notiz-/Dateistände, damit der Report ehrlich ist |
| `--limit-sections N` | nur die ersten `N` Abschnitte **mit Aktivitäten** je Kurs (leere Abschnitte wie „Allgemein" werden beim Limit übersprungen – so trifft `--limit-sections 1` bei Kurs 6556 den Abschnitt mit den 2 Aktivitäten) |
| `--no-material` | nur Notizen, keine Datei-Downloads |
| `--strict` | Exit 1, wenn Fehler/Warnungen auftraten |
| `--verbose` | jede Aktivität (Typ, Name, Moodle-Detail) und jeden Download anzeigen |
| `--env-file PATH` | optional `KEY=VALUE`-Datei laden; Werte aus der Datei haben Vorrang (schützt das `$`). Nur für manuelle Tests, nie ins Repo |

**Exit-Codes:** `0` ok (auch „teilweise erfolgreich mit Fehlern“ ohne `--strict`),
`1` Fehler mit `--strict`, `2` Konfiguration/Secrets/Auth (fehlende Variablen,
nicht angemeldete Session, unbekannte Kurs-ID).

## State-Datei

Standard `~/.local/state/fernuni-sync/state.json` (überschreibbar über `state_file` in
`config.json`), wird **atomar** geschrieben (`tmp` + `os.replace`).

```json
{"version": 1,
 "material": {"<vault-pfad>": {"source": "<moodle-url-ohne-query>", "moodle_size": 1782579,
                               "etag": "\"…\"", "sha256": "…", "updated": "2026-10-15T16:25:50"}},
 "notes": {"<vault-pfad>": {"created": "2026-10-15", "updated": "2026-10-15T16:26:09"}}}
```

* **Löschen** (`rm ~/.local/state/fernuni-sync/state.json`) → beim nächsten Lauf wird alles
  erneut geprüft; unveränderte Dateien werden trotzdem nicht erneut geladen, weil
  zusätzlich die Zielgröße per `HEAD` geprüft wird. Eine Datei, die im Vault **ohne**
  State-Eintrag existiert, wird nicht überschrieben (siehe unten).
* Der State enthält **keine** Secrets.

## Idempotenz & Restart-Sicherheit

* Eine Datei gilt als unverändert, wenn Größe und (falls vorhanden) das `ETag` des
  Moodle-`pluginfile`-Responses zum State passen und die Zieldatei per `HEAD` existiert.
  Live gemessen: zweiter Lauf = **0 Downloads**, PDF-ETag unverändert.
* Notizen werden nur geschrieben, wenn sie `generated: true` im Frontmatter tragen.
  Existiert eine Zieldatei **ohne** dieses Flag, bleibt sie unangetastet; die neue Notiz
  landet als `<name>.generated-2.md` (Material analog als `<name>-generated-2.<endung>`),
  plus Warnung im Log.
* `created` wird aus der bestehenden Notiz übernommen; `updated` wird bei jedem Lauf neu
  gesetzt. Zwischen zwei Läufen ändern sich damit nur `updated` und das Sync-Log.
* Ein abgebrochener Lauf ist unkritisch: Downloads landen in temporären Dateien, der State
  wird **nach jedem Kurs** geschrieben; der nächste Lauf setzt dort fort.
* Fehler einzelner Abschnitte/Aktivitäten brechen den Lauf nicht ab (3 Versuche je Request
  mit Backoff 1s/3s/9s, Sammelausgabe am Ende, Exit 1 nur mit `--strict`).

## Layout im Vault

```
<base>/<slug>/Moodle/_Moodle-Index.md          Index: Abschnitte, Aktivitäten, Dateien
<base>/<slug>/Moodle/<SS>-<abschnitts-slug>.md Abschnittsnotiz
<base>/<slug>/Moodle/Material/<SS>-<slug>/…    heruntergeladene Dateien
<base>/<slug>/Moodle/Material/_sonstiges/…     reserviert für Aktivitäten ohne Abschnitt
```
`<SS>` = zweistellige Abschnittsnummer; Slugs: Umlaute `äöüÄÖÜß` → `ae/oe/ue/ae/oe/ue/ss`,
alles außer `[A-Za-z0-9._-]` → `-`, Mehrfach-`-` zusammengefasst, ≤ 60 Zeichen, keine
führenden/abschließenden `-`; leere Namen → `abschnitt-<SS>`. Vault-Pfade in Links werden
mit `urllib.parse.quote` kodiert (Leerzeichen `%20`, Umlaute `%C3%9C` …).

Notizzeilen: Icon + Aktivitätsname; der Typ wird **nur** über das Icon transportiert.
Nur bei uneindeutigem Icon steht ein kurzes deutsches Wort in Klammern hinter dem Namen
(`✏️ **Übungsblatt 3** *(Quiz)*`; ebenso Aufgabe/Forum/Link/Text/Textseite/Buch) –
`resource`/`resource_pdf`/`folder`/`helixmedia` bleiben ohne jeden Zusatz.
Icons: `📄` resource/folder-Dateien, `🎬` helixmedia (Video, nur Link), `✏️` quiz/assign,
`💬` forum, `🔗` url, `📝` label/page/book, `❓` unbekannt.
Nicht geladene Dateien (zu groß, Video, Fehler) erscheinen als normaler Moodle-Link.

Titel: Abschnittsname == Kursname → nur der Abschnittsname (Kurs 6556:
`Data Engineering für Data Science`); sonst `<modul> – <Abschnittsname>` (z. B.
`64401 – Überwachtes Lernen`), ohne `modul` in `config.json` mit dem Kursnamen.
Die Zusammenfassung steht als **eingeklappter** Callout
(`> [!quote]- Zusammenfassung aus Moodle`); Absätze bleiben als `>`-Leerzeilen erhalten.

Drei instanzspezifische Eigenheiten sind eingebaut:

* Die Instanz schreibt Zugänglichkeits-Labels als
  `<span class="accesshide " > MEDIAL</span>` – mit Leerzeichen **vor** dem `>`. Der
  Parser entfernt sie über `<span class="accesshide[^"]*"\s*>.*?</span>`
  (`moodle_parse.clean`); ohne das `\s*` bliebe „MEDIAL"/„Datei"/„Kurstext, PDF" im
  Aktivitätsnamen hängen. `obsidian.strip_activity_label` bleibt als Sicherheitsnetz.
* Bei `resource` liefert `/mod/resource/view.php?id=…` **keinen Redirect**, sondern HTML;
  der echte `pluginfile.php`-Link wird aus dieser Seite gezogen. Erst danach werden Größe
  (`Content-Length` = 1.7 MB) und ETag bestimmt.

Am Ende jedes echten Laufs wird ein Block an `90-Meta/01-Sync-Log.md` **angehängt**
(Tabelle je Kurs + Gesamtdauer + Fehlerliste). Die Datei wird nie überschrieben;
`--dry-run` schreibt dort nichts.

## Betrieb im Coder-Workspace (Startup/Cron/Timer)

Secrets außerhalb des Repos ablegen (z. B. `/etc/fernuni-sync/env`, `chmod 600`, `$` gequotet)
und per Wrapper starten:

```bash
#!/bin/sh
# /opt/fernuni-sync/run.sh
set -a; . /etc/fernuni-sync/env; set +a
exec python3 /pfad/fernuni-sync/sync.py --config /etc/fernuni-sync/config.json \
     >> /var/log/fernuni-sync.log 2>&1
```

**Cron (täglich 06:30):**
```cron
30 6 * * * /opt/fernuni-sync/run.sh
```

**systemd-Timer:**
```ini
# /etc/systemd/system/fernuni-sync.service
[Service]
Type=oneshot
EnvironmentFile=/etc/fernuni-sync/env
ExecStart=/usr/bin/python3 /pfad/fernuni-sync/sync.py
User=fernuni
```
```ini
# /etc/systemd/system/fernuni-sync.timer
[Timer]
OnCalendar=*-*-* 06:30:00
Persistent=true
[Install]
WantedBy=timers.target
```
`systemctl enable --now fernuni-sync.timer`. Beim ersten Lauf eines neuen Kurses mit
`--limit-sections 1 --verbose` starten, danach ohne Limit.
Hinweis: `systemd` `EnvironmentFile` wertet `$` nicht expandierend aus – die Shell-Expansion
betrifft nur `. ` / `source`-Aufrufe.

## Troubleshooting

| Symptom | Ursache / Abhilfe |
|---|---|
| Login landet auf `login.fernuni-hagen.de` („Identity Provider …"), Exit 2 | **`$` im Passwort wurde von der Shell expandiert**, dadurch ist `MD_PW` verstümmelt. Wert in einfache Quotes setzen oder `\$` maskieren; Prüfung: `python3 -c "import os;print(len(os.environ['MD_PW']))"` (muss die echte Länge zeigen). |
| `Moodle liefert die SAML-Anmeldeseite …`, Exit 2 | dieselbe Ursache wie oben, alternativ falsche Zugangsdaten. Es wird nichts geschrieben. |
| Exit 2: „fehlende Umgebungsvariablen“ | `set -a; . env; set +a` vergessen oder Variable leer. |
| Cloudflare **Error 1010** beim oCIS | fehlender Browser-User-Agent. `dav.py` setzt immer den Chrome-UA. |
| HTTP 429 / 503 / Zeitüberschreitungen | Retry 1s/3s/9s ist eingebaut; bei Rate-Limit Lauf mit `--limit-sections` verkleinern und später wiederholen. |
| `PROPFIND /dav/spaces/…` → 404 | fast immer ein zerstörtes `$` in `FERNUNI_SPACE_ID`: `--env-file` nutzen oder `\$` maskieren. Sonst: `Dav` fällt automatisch auf `/dav/files/<user>/` zurück; ID per `GET /graph/v1.0/me/drives` prüfen. |
| Datei fehlt im Vault | `max_file_mb` (Default 100) überschritten → nur Link in der Notiz + Eintrag im Log; Wert in `config.json` erhöhen. |
| `.generated-2.md` taucht auf | die Zieldatei hatte kein `generated: true`; die eigene Notiz bleibt unangetastet (gewollt). |
| Alles wirkt doppelt hochgeladen | State gelöscht oder andere `state_file`; nach einem Lauf ist wieder alles markiert. |

## Dateien

| Datei | Inhalt |
|---|---|
| `sync.py` | Einstiegspunkt: CLI, Moodle-Zugriff mit Retry, Orchestrierung, State, Sync-Log, Exit-Codes |
| `dav.py` | WebDAV-Client (PUT/GET/MKCOL/PROPFIND/HEAD/DELETE), URL-Kodierung, Basis-Pfad-Auflösung |
| `obsidian.py` | Slugs, Dateinamen, Typ-Label-Bereinigung, Frontmatter, Notiz-/Index-Rendering, Icons |
| `moodle_session.py` | Login über das lokale Formular (`logintoken`); SSO-Formularfluss nur als Fallback |
| `moodle_parse.py` | HTML-Parser beider Kursformate (`format_onetopic`, `format_tiles`), Ordnerlisten |
| `selftest.py` | Offline-Tests (Fixtures, Attrappen) – `python3 selftest.py` |
| `config.json` | Kurs→Ordner-Mapping, Limits, State-Pfad |
| `requirements.txt` | nur Kommentare – keine Abhängigkeiten |
