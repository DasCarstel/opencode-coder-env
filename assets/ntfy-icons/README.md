# ntfy-Icons

Kleine PNG-Icons (256×256) für die ntfy-Meldungen aus OpenCode. Sie werden als
Attachment mitgeschickt, damit auf dem Handy (S25 Ultra) und am Windows-PC
sofort erkennbar ist, **woher** eine Meldung kommt — je Workspace ein eigenes
**Bildsymbol** in der Workspace-Farbe.

Die Motive sind bewusst **Piktogramme**, keine Buchstaben oder Kürzel: „SM",
„HA" usw. sind nicht selbsterklärend, ein Server-Rack oder ein Haus schon.
Jedes Symbol ist aus einfachen geometrischen Formen aufgebaut (nur Pillow,
keine externen Dateien, kein Netz) und nimmt rund zwei Drittel der Fläche ein,
damit es auch in der Handy-Vorschau erkennbar bleibt.

## Aufbau

| Eigenschaft | Wert |
|-------------|------|
| Format | PNG, RGBA, 256×256 px, < 40 KB je Datei |
| Motiv | abgerundetes Quadrat in der Workspace-Farbe, darin das weiße Bildsymbol |
| Details | dunkle Farbtöne der Workspace-Farbe (LEDs, Rillen, Punkte, Fugen) |
| Erzeuger | `tools/make-ntfy-icons.py` (Python 3 + Pillow) |
| Ablage | `assets/ntfy-icons/<name>.png` |

Die Dateien sind **generiert**, aber eingecheckt: ntfy lädt sie über eine
öffentliche Roh-URL, dafür muss die Datei im `master`-Branch liegen.

## Motive

| Datei | Motiv | Erkennungsmerkmale |
|-------|-------|--------------------|
| `server-management.png` | **Server-Rack (3 HE)** | drei waagerechte Blenden, je ein dunkler Lüftungsschlitz links und zwei LED-Punkte rechts |
| `homeassistant.png` | **Haus** | Dachdreieck mit Überstand, Korpus, dunkle Bogentür |
| `cleve.png` | **Container-Stapel** | drei versetzt gestapelte Kisten mit je vier senkrechten Rillen |
| `general.png` | **Werkzeugkasten** | Korpus, Bügel oben, dunkle Deckelfuge mit Schloss in der Mitte |
| `minijob.png` | **Münzenstapel** | vier gestapelte Ellipsen mit dunklem Rand, oberste mit Innenring |
| `uni.png` | **Doktorhut** | liegende Raute auf Käppchen, Quaste mit Kugel rechts |
| `svelte.png` | **Blitz** | gezacktes Polygon (Tempo/Build) |
| `hermes.png` | **Sprechblase** | Blase mit Schwanz unten links und drei Punkten (Bote/Nachricht) |

Die vier „Balken“-Motive (Rack, Container, Werkzeugkasten, Sprechblase) sind
über ihr Zusatzmerkmal getrennt — Punkte in jeder Reihe, Rillen plus
Treppenversatz, Bügel oben, Schwanz unten links. Haus, Blitz, Münzenstapel und
Doktorhut sind schon an der Silhouette eindeutig. Auch in Graustufen und auf
Handygröße (64 px) bleiben alle acht unterscheidbar.

## Neu erzeugen

```bash
# aus dem Repo-Wurzelverzeichnis
python3 tools/make-ntfy-icons.py
```

Das Skript ist **deterministisch** — weder Zeitstempel noch Zufallswerte gehen
in das Bild ein, ein erneuter Lauf erzeugt also byte-identische Dateien
(nachprüfbar mit `md5sum assets/ntfy-icons/*.png` vor und nach dem Lauf).
Abweichender Zielordner ist optional als Argument möglich:

```bash
python3 tools/make-ntfy-icons.py /tmp/opencode/ntfy-icons
```

## Roh-URLs

Basis: `https://raw.githubusercontent.com/DasCarstel/opencode-coder-env/master/assets/ntfy-icons/`

| Name | Motiv | Farbe | Roh-URL |
|------|-------|-------|---------|
| `server-management` | Server-Rack | `#2563eb` | https://raw.githubusercontent.com/DasCarstel/opencode-coder-env/master/assets/ntfy-icons/server-management.png |
| `homeassistant` | Haus | `#f59e0b` | https://raw.githubusercontent.com/DasCarstel/opencode-coder-env/master/assets/ntfy-icons/homeassistant.png |
| `cleve` | Container-Stapel | `#16a34a` | https://raw.githubusercontent.com/DasCarstel/opencode-coder-env/master/assets/ntfy-icons/cleve.png |
| `general` | Werkzeugkasten | `#64748b` | https://raw.githubusercontent.com/DasCarstel/opencode-coder-env/master/assets/ntfy-icons/general.png |
| `minijob` | Münzenstapel | `#0d9488` | https://raw.githubusercontent.com/DasCarstel/opencode-coder-env/master/assets/ntfy-icons/minijob.png |
| `uni` | Doktorhut | `#7c3aed` | https://raw.githubusercontent.com/DasCarstel/opencode-coder-env/master/assets/ntfy-icons/uni.png |
| `svelte` | Blitz | `#ea580c` | https://raw.githubusercontent.com/DasCarstel/opencode-coder-env/master/assets/ntfy-icons/svelte.png |
| `hermes` | Sprechblase | `#ca8a04` | https://raw.githubusercontent.com/DasCarstel/opencode-coder-env/master/assets/ntfy-icons/hermes.png |

## Neuen Workspace ergänzen

1. In `tools/make-ntfy-icons.py` eine Motiv-Funktion `motiv_<name>(zeichner,
   rgb, kante, strich)` anlegen; sie zeichnet das weiße Piktogramm in den
   Zeichenpuffer. Alle Koordinaten über `px(anteil, kante)` bzw.
   `px(anteil, basis)` angeben, damit das Motiv mitskaliert.
2. In der Tabelle `ICONS` eine Zeile `("<name>", "<#rrggbb>", motiv_<name>,
   "<Beschreibung>")` ergänzen.
3. Skript ausführen (`python3 tools/make-ntfy-icons.py`) und die Bilder
   **ansehen** — auch verkleinert: Motiv klar erkennbar? Von den bestehenden
   Motiven unterscheidbar?
4. Hier in den Tabellen Motiv und neue Roh-URL eintragen.
5. Datei committen und pushen — erst danach ist die Roh-URL erreichbar.

**Keine** Buchstaben, Ziffern oder Sonderzeichen als Motiv (nicht
selbsterklärend, in der Handy-Vorschau kaum lesbar). Weißes Symbol auf
Workspace-Farbe, Details in einem dunklen Ton derselben Farbe, Farbe deutlich
anders als die bereits vergebenen.
