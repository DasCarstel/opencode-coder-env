# ntfy-Icons

Kleine PNG-Icons (256×256) für die ntfy-Meldungen aus OpenCode. Sie werden als
Attachment mitgeschickt, damit auf dem Handy (S25 Ultra) und am Windows-PC
sofort erkennbar ist, **woher** eine Meldung kommt — je Workspace ein eigenes
**Bildsymbol** in der Workspace-Farbe.

Die Motive sind bewusst **Piktogramme**, keine Buchstaben oder Kürzel: „SM",
„HA" usw. sind nicht selbsterklärend, ein Werkzeugkasten oder ein
Kommandozeilen-Fenster schon. **Sieben** der acht Symbole sind aus einfachen
geometrischen Formen aufgebaut (nur Pillow, kein Netz) und nehmen rund zwei
Drittel der Fläche ein, damit sie auch in der Handy-Vorschau erkennbar bleiben;
`server-management` füllt als einziges Motiv mehr (rund 85 %). Das achte,
`homeassistant`, zeigt das **echte Home-Assistant-Zeichen in Weiß** auf
Home-Assistant-Blau — die Form dafür stammt aus dem Quellbild unter `src/`,
der Generator braucht also auch dafür kein Netz.

`uebersicht.png` ist **kein** Symbol für Meldungen, sondern die Sichtprüfung
zum Vergleichen der Motive (je Workspace 160 px und 56 px mit Namen) und wird
mit `--uebersicht` zusammen mit den Icons neu erzeugt.

## Aufbau

| Eigenschaft | Wert |
|-------------|------|
| Format | PNG, RGBA, 256×256 px, < 40 KB je Datei |
| Motiv | abgerundetes Quadrat in der Workspace-Farbe, darin das weiße Bildsymbol — bei `homeassistant` das echte HA-Zeichen in Weiß auf HA-Blau, bei `server-management` der große Kistenstapel |
| Details | dunkle Farbtöne der Workspace-Farbe (Rillen, Fensterknöpfe, Fugen, Hutband) |
| Erzeuger | `tools/make-ntfy-icons.py` (Python 3 + Pillow) |
| Quellbilder | `assets/ntfy-icons/src/<datei>.png` — für `homeassistant` (Form des Zeichens), fest im Repo |
| Ablage | `assets/ntfy-icons/<name>.png` |
| Cache | die Roh-URL wird im Plugin um `?v=<ICON_VERSION>` ergänzt; nach einer Icon-Änderung die Konstante hochzählen, sonst zeigen ntfy und die Apps bis zu 24 h das alte Bild |

Die Dateien sind **generiert**, aber eingecheckt: ntfy lädt sie über eine
öffentliche Roh-URL, dafür muss die Datei im `master`-Branch liegen.

## Motive

| Datei | Motiv | Erkennungsmerkmale |
|-------|-------|--------------------|
| `server-management.png` | **Kisten-Stapel ohne Standortnadel** | derselbe Container-Stapel wie `cleve` (drei Kisten mit je vier senkrechten Rillen, treppenförmig versetzt), aber **ohne** Nadel und deutlich **größer** (rund 85 % der Fläche) — „Innenstandort" |
| `homeassistant.png` | **Home-Assistant-Zeichen in Weiß** | echtes Projektzeichen, aus den hellen Partien von `src/home-assistant.png` als weiße Form gewonnen, auf Home-Assistant-Blau (`#18bcf2`) mit hellem Rand — dieselbe Machart wie alle übrigen Motive |
| `cleve.png` | **Kisten-Stapel mit Standortnadel** | drei versetzt gestapelte Kisten mit je vier senkrechten Rillen, oben rechts eine Standortnadel (Pin mit dunklem Loch) für den Auswärtsstandort |
| `general.png` | **Kommandozeilen-Fenster** | Fenster mit Titelleiste und drei Fensterknöpfen, im Inhalt ein Chevron `>` und ein Cursor-Balken |
| `minijob.png` | **Werkzeugkasten** | Korpus, Bügel oben, dunkle Deckelfuge mit Schloss in der Mitte |
| `uni.png` | **Doktorhut** | liegende Raute auf Käppchen, Quaste mit Kugel rechts |
| `svelte.png` | **Browserfenster** | dasselbe Fenster wie `general`, im Inhalt aber eine Erdkugel (Kreis mit Meridian und Äquator) |
| `hermes.png` | **Geflügelter Helm** | Kuppel mit dunklem Hutband, breite Krempe, links und rechts je ein Flügel mit drei Federspitzen (Petasos des Hermes) |

Die beiden Fenster-Motive `general` und `svelte` teilen sich Rahmen,
Titelleiste und Fensterknöpfe; unterschieden werden sie allein durch den
Inhalt — Prompt-Zeile (klein, links unten) gegen Erdkugel (füllt den
Innenraum). Die beiden Kisten-Motive unterscheiden sich doppelt: `cleve` trägt
die Standortnadel und zeigt den kleinen Stapel, `server-management` den großen
Stapel ohne Nadel. Der Werkzeugkasten ist an Bügel und Schloss zu erkennen, der
Doktorhut an Raute und Quaste, der Helm an Hutband und Flügeln; das weiße
Home-Assistant-Zeichen hat als einziges eine eigene Farbe (helles Cyan) und
eine ganz eigene Silhouette. Auch in Graustufen und auf Handygröße (64 px)
bleiben alle acht unterscheidbar.

Das **dunkle Hutband** am Helm ist kein Zierrat, sondern der Kunstgriff für
64 px: Kuppel und Krempe sind beide weiß und verschmelzen ohne das Band zu
einer einzigen Fläche — das Motiv liest sich dann als Pilz. Mit dem Band
bleiben Kuppel, Band und Krempe als drei Streifen sichtbar, die Flügel als
seitliche Zacken. Der geflügelte **Stab** (Kerykeion ohne Schlangen) bleibt als
`motiv_hermes_stab` im Generator gezeichnet, ist aber nicht mehr im Einsatz.

## Herkunft der Quellbilder

Das Home-Assistant-Zeichen wurde einmalig aus der Sammlung
[homarr-labs/dashboard-icons](https://github.com/homarr-labs/dashboard-icons)
(PNG-Ausgabe) geladen und liegt unverändert als Quellbild im Repo — der
Generator liest es von dort und braucht kein Netz:

| Datei | Motiv | Quelle (URL) | Stand |
|-------|-------|--------------|-------|
| `src/home-assistant.png` | Home-Assistant-Kachel (blaues Quadrat `#18bcf2` mit weißem Zeichen) | https://raw.githubusercontent.com/homarr-labs/dashboard-icons/main/png/home-assistant.png | Download 17.10.2026 |
| `src/proxmox.png` | Proxmox-Zeichen | https://raw.githubusercontent.com/homarr-labs/dashboard-icons/main/png/proxmox.png | Download 17.10.2026 — **nicht mehr verwendet** |

Zur Kachel: **nicht** der Alphakanal liefert das Zeichen — der umreißt nur das
abgerundete Quadrat der Kachel. Als Form dienen die **hellen Partien** (kleinster
Farbkanal je Pixel: Grund 24, Zeichen 242, siehe `form_aus_bild`). `src/proxmox.png`
liegt weiterhin im Repo, wird aber von keinem Icon mehr genutzt; der Weg, ein
Logo unverändert einzupassen, bleibt als `logo_erzeugen` erhalten.

Das Home-Assistant-Zeichen und das Proxmox-Zeichen sind Marken der jeweiligen
Rechteinhaber; sie dienen hier ausschließlich der privaten Kennzeichnung eigener
Benachrichtigungen und stehen in keiner Verbindung zu den Projekten.

## Neu erzeugen

```bash
# aus dem Repo-Wurzelverzeichnis
python3 tools/make-ntfy-icons.py

# zusätzlich die Sichtprüfung uebersicht.png neu schreiben
python3 tools/make-ntfy-icons.py --uebersicht
```

Das Skript ist **deterministisch** — weder Zeitstempel noch Zufallswerte gehen
in das Bild ein, ein erneuter Lauf erzeugt also byte-identische Dateien
(nachprüfbar mit `md5sum assets/ntfy-icons/*.png` vor und nach dem Lauf). Das
gilt auch für das Home-Assistant-Zeichen, dessen Quellbild im Repo liegt.
Abweichender Zielordner ist optional als Argument möglich:

```bash
python3 tools/make-ntfy-icons.py /tmp/opencode/ntfy-icons --uebersicht
```

## Übersichtsbild

`uebersicht.png` entsteht mit `--uebersicht` und ist **kein** Symbol für
Meldungen, sondern eine Sichtprüfung: heller Grund, oben die Titelzeile, je
Workspace das Icon in 160 px und darunter dasselbe in 56 px (Handy-Vorschau),
unten der Name — vier Spalten und zwei Zeilen, alle Bilder vollständig sichtbar.
Nach jeder Motiv-Änderung einmal neu erzeugen und **ansehen**: auf 56 px muss
das Motiv noch erkennbar und von den übrigen unterscheidbar sein.

## Roh-URLs

Basis: `https://raw.githubusercontent.com/DasCarstel/opencode-coder-env/master/assets/ntfy-icons/`

Das Plugin hängt `?v=<ICON_VERSION>` an (derzeit `?v=4`): ntfy und die Apps
halten ein einmal geladenes Icon lokal im Zwischenspeicher (laut Doku bis zu 24
Stunden, teils bis die Meldung gelöscht wird) und würden ein geändertes Bild
sonst weiter aus dem Cache zeigen.

| Name | Motiv | Farbe | Roh-URL |
|------|-------|-------|---------|
| `server-management` | Kisten-Stapel ohne Standortnadel (groß) | `#2563eb` | https://raw.githubusercontent.com/DasCarstel/opencode-coder-env/master/assets/ntfy-icons/server-management.png |
| `homeassistant` | Home-Assistant-Zeichen in Weiß | `#18bcf2` | https://raw.githubusercontent.com/DasCarstel/opencode-coder-env/master/assets/ntfy-icons/homeassistant.png |
| `cleve` | Kisten-Stapel mit Standortnadel | `#64748b` | https://raw.githubusercontent.com/DasCarstel/opencode-coder-env/master/assets/ntfy-icons/cleve.png |
| `general` | Kommandozeilen-Fenster | `#334155` | https://raw.githubusercontent.com/DasCarstel/opencode-coder-env/master/assets/ntfy-icons/general.png |
| `minijob` | Werkzeugkasten | `#0d9488` | https://raw.githubusercontent.com/DasCarstel/opencode-coder-env/master/assets/ntfy-icons/minijob.png |
| `uni` | Doktorhut | `#7c3aed` | https://raw.githubusercontent.com/DasCarstel/opencode-coder-env/master/assets/ntfy-icons/uni.png |
| `svelte` | Browserfenster mit Erdkugel | `#ea580c` | https://raw.githubusercontent.com/DasCarstel/opencode-coder-env/master/assets/ntfy-icons/svelte.png |
| `hermes` | Geflügelter Helm | `#ca8a04` | https://raw.githubusercontent.com/DasCarstel/opencode-coder-env/master/assets/ntfy-icons/hermes.png |

Die beiden Schiefertöne sind bewusst getrennt: `cleve` liegt als
Außenstandort in mittlerem Blau-grau (`#64748b`), das Kommandozeilen-Fenster
von `general` in dunklem Schiefer (`#334155`), damit sich die beiden
Fenster-Motive auch farblich unterscheiden.

Die beiden Blautöne sind sich ähnlich: `server-management` trägt ein kräftiges
Blau (`#2563eb`), `homeassistant` das helle Cyan des HA-Zeichens (`#18bcf2`).
Auseinandergehalten werden sie über das Motiv und die Helligkeit — Kistenstapel
gegen Haus-Zeichen.

## Neuen Workspace ergänzen

1. In `tools/make-ntfy-icons.py` eine Motiv-Funktion `motiv_<name>(zeichner,
   rgb, kante, strich)` anlegen; sie zeichnet das weiße Piktogramm in den
   Zeichenpuffer. Alle Koordinaten über `px(anteil, kante)` bzw.
   `px(anteil, basis)` angeben, damit das Motiv mitskaliert.
2. In der Tabelle `ICONS` eine Zeile `("<name>", "<#rrggbb>", motiv_<name>,
   "<Beschreibung>", None)` ergänzen. Das fünfte Feld ist das Quellbild:
   * `None` — das Motiv wird gezeichnet (Normalfall).
   * `Quellbild("<datei>.png", als_form=True)` — die **hellen Partien** des
     Quellbilds werden als weiße Form auf den farbigen Rahmen gesetzt (so
     entsteht `homeassistant`); die Farbe ist dann die des Rahmens.
   * `Quellbild("<datei>.png")` — das Bild wird unverändert eingepasst
     (`logo_erzeugen`), etwa für ein Logo, das so bleiben soll; die Farbe ist
     dann nur Dokumentation.
   Die gezeichneten Motive `motiv_server` und `motiv_haus` bleiben als Rückfall
   erhalten.
3. Skript ausführen (`python3 tools/make-ntfy-icons.py --uebersicht`) und die
   Bilder **ansehen** — auch verkleinert: Motiv klar erkennbar? Von den
   bestehenden Motiven unterscheidbar?
4. Hier in den Tabellen Motiv und neue Roh-URL eintragen; bei geänderten Bildern
   in `ntfy-notify.js` die Konstante `ICON_VERSION` hochzählen.
5. Datei committen und pushen — erst danach ist die Roh-URL erreichbar.

**Keine** Buchstaben, Ziffern oder Sonderzeichen als Motiv (nicht
selbsterklärend, in der Handy-Vorschau kaum lesbar). Weißes Symbol auf
Workspace-Farbe, Details in einem dunklen Ton derselben Farbe, Farbe deutlich
anders als die bereits vergebenen.
