# ntfy-Icons

Kleine PNG-Icons (256×256) für die ntfy-Meldungen aus OpenCode. Sie werden als
Attachment mitgeschickt, damit auf dem Handy (S25 Ultra) und am Windows-PC
sofort erkennbar ist, **woher** eine Meldung kommt — je Workspace ein eigenes
Kürzel in der Workspace-Farbe.

## Aufbau

| Eigenschaft | Wert |
|-------------|------|
| Format | PNG, RGBA, 256×256 px, < 40 KB je Datei |
| Motiv | abgerundetes Quadrat in der Workspace-Farbe, darin das weiße Kürzel |
| Erzeuger | `tools/make-ntfy-icons.py` (Python 3 + Pillow) |
| Ablage | `assets/ntfy-icons/<name>.png` |

Die Dateien sind **generiert**, aber eingecheckt: ntfy lädt sie über eine
öffentliche Roh-URL, dafür muss die Datei im `master`-Branch liegen.

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

| Name | Kürzel | Farbe | Roh-URL |
|------|--------|-------|---------|
| `server-management` | SM | `#2563eb` | https://raw.githubusercontent.com/DasCarstel/opencode-coder-env/master/assets/ntfy-icons/server-management.png |
| `homeassistant` | HA | `#f59e0b` | https://raw.githubusercontent.com/DasCarstel/opencode-coder-env/master/assets/ntfy-icons/homeassistant.png |
| `cleve` | CL | `#16a34a` | https://raw.githubusercontent.com/DasCarstel/opencode-coder-env/master/assets/ntfy-icons/cleve.png |
| `general` | GE | `#64748b` | https://raw.githubusercontent.com/DasCarstel/opencode-coder-env/master/assets/ntfy-icons/general.png |
| `minijob` | MJ | `#0d9488` | https://raw.githubusercontent.com/DasCarstel/opencode-coder-env/master/assets/ntfy-icons/minijob.png |
| `uni` | UN | `#7c3aed` | https://raw.githubusercontent.com/DasCarstel/opencode-coder-env/master/assets/ntfy-icons/uni.png |
| `svelte` | SV | `#ea580c` | https://raw.githubusercontent.com/DasCarstel/opencode-coder-env/master/assets/ntfy-icons/svelte.png |
| `hermes` | HE | `#ca8a04` | https://raw.githubusercontent.com/DasCarstel/opencode-coder-env/master/assets/ntfy-icons/hermes.png |

## Neuen Workspace ergänzen

1. In `tools/make-ntfy-icons.py` in der Tabelle `ICONS` eine Zeile
   `("<name>", "<#rrggbb>", "<KUERZEL>")` ergänzen.
2. Skript ausführen (`python3 tools/make-ntfy-icons.py`).
3. Hier in der Tabelle die neue Roh-URL eintragen.
4. Datei committen und pushen — erst danach ist die Roh-URL erreichbar.

Das Kürzel sollte 1–2 Großbuchstaben haben (mehr passen optisch nicht), die
Farbe sich an der Workspace-Farbe orientieren und sich von den bereits
vergebenen deutlich unterscheiden.
