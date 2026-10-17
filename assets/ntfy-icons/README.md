# ntfy-Icons

Kleine PNG-Icons (256×256) für die ntfy-Meldungen aus OpenCode. Sie werden als
Attachment mitgeschickt, damit auf dem Handy (S25 Ultra) und am Windows-PC
sofort erkennbar ist, **woher** eine Meldung kommt — je Workspace ein eigenes
**Bildsymbol** in der Workspace-Farbe.

Die Motive sind bewusst **Piktogramme**, keine Buchstaben oder Kürzel: „SM",
„HA" usw. sind nicht selbsterklärend, ein Werkzeugkasten oder eine Sprechblase
schon. Sechs der acht Symbole sind aus einfachen geometrischen Formen
aufgebaut (nur Pillow, kein Netz) und nehmen rund zwei Drittel der Fläche ein,
damit sie auch in der Handy-Vorschau erkennbar bleiben. Zwei Icons tragen das
**echte Logo** des jeweiligen Projekts (Proxmox, Home Assistant): Diese
Quellbilder liegen im Repo unter `src/` und werden vom Generator eingepasst —
auch dafür braucht er kein Netz.

## Aufbau

| Eigenschaft | Wert |
|-------------|------|
| Format | PNG, RGBA, 256×256 px, < 40 KB je Datei |
| Motiv | abgerundetes Quadrat in der Workspace-Farbe, darin das weiße Bildsymbol — bei den beiden Logos stattdessen das Logo auf transparentem Grund |
| Details | dunkle Farbtöne der Workspace-Farbe (Rillen, Punkte, Fugen) |
| Erzeuger | `tools/make-ntfy-icons.py` (Python 3 + Pillow) |
| Quellbilder | `assets/ntfy-icons/src/<datei>.png` — nur für die beiden Logos, fest im Repo |
| Ablage | `assets/ntfy-icons/<name>.png` |
| Cache | die Roh-URL wird im Plugin um `?v=<ICON_VERSION>` ergänzt; nach einer Icon-Änderung die Konstante hochzählen, sonst zeigen ntfy und die Apps bis zu 24 h das alte Bild |

Die Dateien sind **generiert**, aber eingecheckt: ntfy lädt sie über eine
öffentliche Roh-URL, dafür muss die Datei im `master`-Branch liegen.

## Motive

| Datei | Motiv | Erkennungsmerkmale |
|-------|-------|--------------------|
| `server-management.png` | **Proxmox-Zeichen** (Logo) | echtes Projektlogo, aus `src/proxmox.png` eingepasst — kein Piktogramm |
| `homeassistant.png` | **Home-Assistant-Zeichen** (Logo) | echtes Projektlogo, aus `src/home-assistant.png` eingepasst — kein Piktogramm |
| `cleve.png` | **Container-Stapel** | drei versetzt gestapelte Kisten mit je vier senkrechten Rillen |
| `general.png` | **Werkzeugkasten** | Korpus, Bügel oben, dunkle Deckelfuge mit Schloss in der Mitte |
| `minijob.png` | **Münzenstapel** | vier gestapelte Ellipsen mit dunklem Rand, oberste mit Innenring |
| `uni.png` | **Doktorhut** | liegende Raute auf Käppchen, Quaste mit Kugel rechts |
| `svelte.png` | **Blitz** | gezacktes Polygon (Tempo/Build) |
| `hermes.png` | **Sprechblase** | Blase mit Schwanz unten links und drei Punkten (Bote/Nachricht) |

Die drei „Balken“-Motive (Container, Werkzeugkasten, Sprechblase) sind über ihr
Zusatzmerkmal getrennt — Rillen plus Treppenversatz, Bügel oben, Schwanz unten
links. Blitz, Münzenstapel und Doktorhut sind schon an der Silhouette
eindeutig; die beiden Logos tragen ihre eigenen Farben und sind damit ohnehin
unverwechselbar. Auch in Graustufen und auf Handygröße (64 px) bleiben alle acht
unterscheidbar.

## Herkunft der Quellbilder

Die beiden Logos wurden einmalig aus der Sammlung
[homarr-labs/dashboard-icons](https://github.com/homarr-labs/dashboard-icons)
(PNG-Ausgabe) geladen und liegen unverändert als Quellbild im Repo — der
Generator liest sie von dort und braucht kein Netz:

| Datei | Motiv | Quelle (URL) | Stand |
|-------|-------|--------------|-------|
| `src/proxmox.png` | Proxmox-Zeichen | https://raw.githubusercontent.com/homarr-labs/dashboard-icons/main/png/proxmox.png | Download 17.10.2026 |
| `src/home-assistant.png` | Home-Assistant-Zeichen | https://raw.githubusercontent.com/homarr-labs/dashboard-icons/main/png/home-assistant.png | Download 17.10.2026 |

Das Home-Assistant-Zeichen und das Proxmox-Zeichen sind Marken der jeweiligen
Rechteinhaber; sie dienen hier ausschließlich der privaten Kennzeichnung eigener
Benachrichtigungen und stehen in keiner Verbindung zu den Projekten.

## Neu erzeugen

```bash
# aus dem Repo-Wurzelverzeichnis
python3 tools/make-ntfy-icons.py
```

Das Skript ist **deterministisch** — weder Zeitstempel noch Zufallswerte gehen
in das Bild ein, ein erneuter Lauf erzeugt also byte-identische Dateien
(nachprüfbar mit `md5sum assets/ntfy-icons/*.png` vor und nach dem Lauf). Das
gilt auch für die beiden Logos, deren Quellbilder im Repo liegen.
Abweichender Zielordner ist optional als Argument möglich:

```bash
python3 tools/make-ntfy-icons.py /tmp/opencode/ntfy-icons
```

## Roh-URLs

Basis: `https://raw.githubusercontent.com/DasCarstel/opencode-coder-env/master/assets/ntfy-icons/`

Das Plugin hängt `?v=<ICON_VERSION>` an (derzeit `?v=2`): ntfy und die Apps
halten ein einmal geladenes Icon lokal im Zwischenspeicher (laut Doku bis zu 24
Stunden, teils bis die Meldung gelöscht wird) und würden ein geändertes Bild
sonst weiter aus dem Cache zeigen.

| Name | Motiv | Farbe | Roh-URL |
|------|-------|-------|---------|
| `server-management` | Proxmox-Zeichen | `#2563eb` | https://raw.githubusercontent.com/DasCarstel/opencode-coder-env/master/assets/ntfy-icons/server-management.png |
| `homeassistant` | Home-Assistant-Zeichen | `#f59e0b` | https://raw.githubusercontent.com/DasCarstel/opencode-coder-env/master/assets/ntfy-icons/homeassistant.png |
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
   "<Beschreibung>", None)` ergänzen. Das fünfte Feld ist das Quellbild: Für ein
   echtes Logo dort `"<datei>.png"` eintragen, das Bild unter `src/` ablegen und
   als Motiv `None` schreiben — dann wird es eingepasst statt gezeichnet (Farbe
   ist dann nur noch Dokumentation). Die gezeichneten Motive `motiv_server` und
   `motiv_haus` bleiben als Rückfall erhalten.
3. Skript ausführen (`python3 tools/make-ntfy-icons.py`) und die Bilder
   **ansehen** — auch verkleinert: Motiv klar erkennbar? Von den bestehenden
   Motiven unterscheidbar?
4. Hier in den Tabellen Motiv und neue Roh-URL eintragen; bei geänderten Bildern
   in `ntfy-notify.js` die Konstante `ICON_VERSION` hochzählen.
5. Datei committen und pushen — erst danach ist die Roh-URL erreichbar.

**Keine** Buchstaben, Ziffern oder Sonderzeichen als Motiv (nicht
selbsterklärend, in der Handy-Vorschau kaum lesbar). Weißes Symbol auf
Workspace-Farbe, Details in einem dunklen Ton derselben Farbe, Farbe deutlich
anders als die bereits vergebenen.
