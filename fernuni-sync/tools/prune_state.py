#!/usr/bin/env python3
"""Tote -generated-2-Eintraege aus dem Sync-State entfernen (nur material).

Wartungs-Werkzeug fuer ``~/uni/state/state.json``: der oCIS-Sync legt fuer
manche Moodle-Dateien zusaetzliche ``-generated-2``-Varianten an, die im Vault
spaeter wieder verschwinden. Ohne Bereinigung bleiben sie im State als
"vorhanden" stehen, und der Sync laedt die (nicht mehr existierende) Quelle
bei jedem Lauf erneut zu pruefen.

Das Skript prueft per DAV-HEAD, welche dieser Eintraege im Vault wirklich
fehlen (HTTP 404) und entfernt **nur diese** aus dem State. Lebende Eintraege
bleiben unangetastet; vor jeder Aenderung wird ``state.json`` nach
``state.json.bak-<datum>`` kopiert. Ohne tote Eintraege passiert nichts
(Exitcode 0).

Aufruf im Workspace (``~/uni`` ist der Host-Bind-Mount /etc/coder/uni-data):

    python3 ~/opencode-coder-env/fernuni-sync/tools/prune_state.py

Pfade: ``$UNI_DIR`` schlaegt alles andere; sonst ``/home/$WORKSPACE_OWNER/uni``
(``WORKSPACE_OWNER`` setzt das Template als Container-Env), sonst ``~/uni``.
"""
import datetime
import os
import shutil
import sys

# fernuni-sync/ liegt eine Ebene ueber diesem Skript (dort liegen sync.py/dav.py).
HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HIER))

OWNER = os.environ.get("WORKSPACE_OWNER", "")
UNI_DIR = (
    os.environ.get("UNI_DIR")
    or ("/home/" + OWNER + "/uni" if OWNER else os.path.expanduser("~/uni"))
)
STATE = os.path.join(UNI_DIR, "state/state.json")
ENV = os.path.join(UNI_DIR, "state/fernuni-sync.env")


def lade_env(pfad):
    with open(pfad, encoding="utf-8") as fh:
        for zeile in fh:
            zeile = zeile.strip()
            if not zeile or zeile.startswith("#") or "=" not in zeile:
                continue
            key, _, val = zeile.partition("=")
            os.environ[key.strip()] = val.strip().strip("'").strip('"')


lade_env(ENV)
import sync as sy
from dav import Dav

dav = Dav()
daten = sy.load_state(STATE)
mat = daten.get("material", {})
notizen = daten.get("notes", {})

kand = sorted(k for k in mat if "-generated-2" in k)
print("material-Eintraege gesamt: " + str(len(mat)))
print("davon mit -generated-2: " + str(len(kand)))
print("notes-Eintraege mit -generated-2: " + str(sum(1 for k in notizen if "-generated-2" in k)))

tote, lebende, fehler = [], [], []
for k in kand:
    try:
        st, _, _ = dav.head(k)
    except Exception as e:
        fehler.append((k, str(e)))
        continue
    if st == 404:
        tote.append(k)
    else:
        lebende.append((k, st))

print("im Vault nicht mehr vorhanden (entfernbar): " + str(len(tote)))
print("im Vault noch vorhanden (bleiben): " + str(len(lebende)))
for k, st in lebende:
    print("  BEHALTEN (HTTP " + str(st) + "): " + k)
print("Pruef-Fehler: " + str(len(fehler)))
for k, e in fehler:
    print("  FEHLER: " + k + ": " + e)

if not tote:
    print("nichts zu entfernen")
    sys.exit(0)

bak = STATE + ".bak-" + datetime.date.today().isoformat()
shutil.copy2(STATE, bak)
print("Backup angelegt: " + bak + " (" + str(os.path.getsize(bak)) + " B)")

for k in tote:
    del mat[k]

sy.save_state(STATE, daten)
mat2 = sy.load_state(STATE).get("material", {})
print("entfernte Eintraege: " + str(len(tote)))
print("material-Eintraege danach: " + str(len(mat2)))
print("verbleibende -generated-2-Eintraege: " + str(sum(1 for k in mat2 if "-generated-2" in k)))
