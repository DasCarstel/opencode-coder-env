---
name: fernuni-sync
description: Moodle-Kurse der FernUni als Obsidian-Notizen + Material in den FernUni-oCIS-Space spiegeln (fernuni-sync im Hintergrund-Loop). Load bei Sync-, Moodle- oder Material-Fragen im uni-Workspace.
license: MIT
---

# fernuni-sync (Moodle → FernUni-oCIS)

Der Sync liegt im Repo unter `~/opencode-coder-env/fernuni-sync/` und läuft im
datascience-Workspace als **Hintergrund-Loop**: einmal beim Workspace-Start,
danach alle 6 Stunden. Log: `~/uni/logs/sync.log`.

## Zustand (Restart-Sicherheit)

| Datei | Zweck |
|-------|-------|
| `~/uni/state/state.json` | Sync-Zustand (Größe/ETag je Material) – liegt im **persistenten** Volume, deshalb meldet ein Neustart des Workspace `0 Downloads` |
| `~/uni/state/fernuni-sync.config.json` | Kopie der `config.json` mit `state_file` auf `~/uni/state/state.json` |
| `~/uni/state/fernuni-sync.env` | `KEY=VALUE`-Datei (Modus 600) für `--env-file`; Variablen kommen aus OpenBao |

## Umgebungsvariablen

Aus OpenBao (`secret/data/uni/fernuni-ocis`, `secret/data/uni/moodle`) und als
Env im OpenCode-Service gesetzt:

`MD_USER`, `MD_PW`, `MD_BASE`, `FERNUNI_OCIS_USER`, `FERNUNI_OCIS_TOKEN`,
`FERNUNI_OCIS_PW`, `FERNUNI_SPACE_ID`, `FERNUNI_OCIS_BASE`

> Werte mit `$` (Passwort, Space-ID) niemals durch die Shell expandieren lassen:
> `--env-file` benutzen (macht der Loop) oder in der Datei einfach quoten.

## Manuell prüfen / starten

```bash
cd ~/opencode-coder-env/fernuni-sync
python3 sync.py --env-file ~/uni/state/fernuni-sync.env \
                --config   ~/uni/state/fernuni-sync.config.json \
                --dry-run --verbose          # nur lesen, kein Schreibzugriff
python3 sync.py --env-file ~/uni/state/fernuni-sync.env \
                --config   ~/uni/state/fernuni-sync.config.json \
                --only 6556 --limit-sections 1   # kleiner echter Lauf
```

Ein zweiter Lauf über denselben Stand meldet `0 Downloads` (Idempotenz über
`state.json` + ETag). Fehlende Pflichtvariable → Exit 2. `--strict` macht
Warnungen zu Exit 1.
