# openGym-Import-Tooling (16.–17.11.2026)

Migrations-Werkzeuge für den Umzug des Obsidian-Vaults `04-Training` nach openGym
(https://gym.mueller-nas.de, self-hosted v1.4.5). Der Umzug ist abgeschlossen;
diese Dateien sind die dokumentierte, reproduzierbare Grundlage.

## Dateien

| Datei | Herkunft / Zweck |
|---|---|
| `import_opengym.py` | Importer: liest `bundle.json`, matchet deutsche Übungsnamen auf openGym-Katalog-IDs (NAME_MAP hartkodiert), baut Workouts/Routinen/exWeights und schreibt per `PUT /api/token/data` (rev-gesichert). `--dry-run` und `--apply`. |
| `bundle.json` | Extrahierte Datenbasis aus dem Vault-Git (`/etc/docker/training-git/04-training`): 92 CSV-Sätze + 29 Frontmatter-Entries (Commits 2025-12-13/16, 2026-11-09). |
| `de.js` | Deutsche Übungsnamen aus openGym v1.4.5 (Katalog id→Name), Quelle: raw.githubusercontent.com/DuarteSantos8/openGym — zum Re-Fetchen, hier als Snapshot. |

## Ergebnisse (Stand 17.11.2026)

- 5 Workouts / 92 Sätze importiert; Routinen r_push/r_pull/r_legs, Woche: Push Mo · Beine und Bauch Mi · Pull Fr
- Maschinen-Remap: Beinbeuger→0586, Beinstrecker→1873 (FID5i-Aufsätze); Neuzugänge 0921 (Schulter-Außenrotation Band), 5607 (Face Pull Band)
- Equipment im State (User per App-UI): Scheiben 1,25/2,5/5×2 Paare + 10×1; Kurzhanteln 2,5–27,5 kg; Bänder 5/10/15 kg; Profil „FID5i-LC" (leg-extension, leg-curl)
- KI-Coach: Provider `compatible`, `gemma4:12b`, `http://ollama:11434/v1` (Hestia-Standard `_shared/ai.env`)

Token-Zugang: openGym-Scoped-Token liegt in OpenBao `secret/data/mcp/opengym`
(API_TOKEN). Keine Token in diesem Verzeichnis.
