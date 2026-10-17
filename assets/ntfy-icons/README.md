# ntfy-Icons

Kleine PNG-Icons (256×256) für die ntfy-Meldungen aus OpenCode. Sie werden als
Attachment mitgeschickt, damit auf dem Handy (S25 Ultra) und am Windows-PC
sofort erkennbar ist, **woher** eine Meldung kommt — je Workspace ein eigenes
**Bildsymbol** in der Workspace-Farbe.

Die Motive sind bewusst **Piktogramme**, keine Buchstaben oder Kürzel: „SM",
„HA" usw. sind nicht selbsterklärend, ein Werkzeugkasten oder ein
Kommandozeilen-Fenster schon. Sechs der acht Symbole sind aus einfachen
geometrischen Formen aufgebaut (nur Pillow, kein Netz) und nehmen rund zwei
Drittel der Fläche ein, damit sie auch in der Handy-Vorschau erkennbar
bleiben. Zwei Icons tragen das **echte Logo** des jeweiligen Projekts
(Proxmox, Home Assistant): Diese Quellbilder liegen im Repo unter `src/` und
werden vom Generator eingepasst — auch dafür braucht er kein Netz.

`uebersicht.png` ist **keine** Ausgabe des Generators, sondern nur eine
selbst gebaute Übersicht (je Workspace 160 px und 56 px mit Namen) zum
Vergleichen der Motive; sie wird zusammen mit den Icons aktualisiert.

## Aufbau

| Eigenschaft | Wert |
|-------------|------|
| Format | PNG, RGBA, 256×256 px, < 40 KB je Datei |
| Motiv | abgerundetes Quadrat in der Workspace-Farbe, darin das weiße Bildsymbol — bei den beiden Logos stattdessen das Logo auf transparentem Grund |
| Details | dunkle Farbtöne der Workspace-Farbe (Rillen, Fensterknöpfe, Fugen, Nadel-Loch) |
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
| `cleve.png` | **Kisten-Stapel mit Standortnadel** | drei versetzt gestapelte Kisten mit je vier senkrechten Rillen, oben rechts eine Standortnadel (Pin mit dunklem Loch) für den Auswärtsstandort |
| `general.png` | **Kommandozeilen-Fenster** | Fenster mit Titelleiste und drei Fensterknöpfen, im Inhalt ein Chevron `>` und ein Cursor-Balken |
| `minijob.png` | **Werkzeugkasten** | Korpus, Bügel oben, dunkle Deckelfuge mit Schloss in der Mitte |
| `uni.png` | **Doktorhut** | liegende Raute auf Käppchen, Quaste mit Kugel rechts |
| `svelte.png` | **Browserfenster** | dasselbe Fenster wie `general`, im Inhalt aber eine Erdkugel (Kreis mit Meridian und Äquator) |
| `hermes.png` | **Geflügelter Stab** | senkrechter Stab mit Kugel oben, links und rechts je ein Flügel mit drei Federspitzen (Hermes’ Kerykeion ohne Schlangen) |

Die beiden Fenster-Motive `general` und `svelte` teilen sich Rahmen,
Titelleiste und Fensterknöpfe; unterschieden werden sie allein durch den
Inhalt — Prompt-Zeile (klein, links unten) gegen Erdkugel (füllt den
Innenraum). Der Kistenstapel trägt als einziges Motiv ein Zusatzzeichen (die
Nadel), der Werkzeugkasten ist an Bügel und Schloss zu erkennen, der
Flügelstab ist das einzige senkrechte Motiv. Doktorhut und Flügelstab sind
schon an der Silhouette eindeutig; die beiden Logos tragen ihre eigenen Farben
und sind damit ohnehin unverwechselbar. Auch in Graustufen und auf Handygröße
(64 px) bleiben alle acht unterscheidbar.

Der geflügelte **Helm** wurde für `hermes` verworfen, obwohl er das bekanntere
Boten-Attribut ist: Bei 64 px verschmelzen Kuppel, Krempe und Flügel zu einer
einzigen Fläche, während Stab, Kugel und Federspitzen getrennte Formen
bleiben.

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

## Übersichtsbild

`uebersicht.png` ist **nicht** Teil des Generators, sondern eine Sichtprüfung:
je Workspace das Icon in 160 px und 56 px nebeneinander, darunter der Name, auf
hellem Grund (`#f5f5f7`), vier Workspaces pro Zeile und zwei Zeilen. Nach jeder
Motiv-Änderung einmal neu erzeugen und **ansehen** — auf 56 px muss das Motiv
noch erkennbar und von den übrigen unterscheidbar sein.

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
| `cleve` | Kisten-Stapel mit Standortnadel | `#64748b` | https://raw.githubusercontent.com/DasCarstel/opencode-coder-env/master/assets/ntfy-icons/cleve.png |
| `general` | Kommandozeilen-Fenster | `#334155` | https://raw.githubusercontent.com/DasCarstel/opencode-coder-env/master/assets/ntfy-icons/general.png |
| `minijob` | Werkzeugkasten | `#0d9488` | https://raw.githubusercontent.com/DasCarstel/opencode-coder-env/master/assets/ntfy-icons/minijob.png |
| `uni` | Doktorhut | `#7c3aed` | https://raw.githubusercontent.com/DasCarstel/opencode-coder-env/master/assets/ntfy-icons/uni.png |
| `svelte` | Browserfenster mit Erdkugel | `#ea580c` | https://raw.githubusercontent.com/DasCarstel/opencode-coder-env/master/assets/ntfy-icons/svelte.png |
| `hermes` | Geflügelter Stab | `#ca8a04` | https://raw.githubusercontent.com/DasCarstel/opencode-coder-env/master/assets/ntfy-icons/hermes.png |

Die beiden Schiefertöne sind bewusst getrennt: `cleve` liegt als
Außenstandort in mittlerem Blau-grau (`#64748b`), das Kommandozeilen-Fenster
von `general` in dunklem Schiefer (`#334155`), damit sich die beiden
Fenster-Motive auch farblich unterscheiden.

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
