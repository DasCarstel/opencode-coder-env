#!/usr/bin/env python3
"""telemetry_snapshot.py — O19-Seitenwagen: state.db-Snapshot für das Hestia-Panel.

Der Hermes-Container besitzt /opt/data/state.db (SQLite: sessions, messages,
session_model_usage). Das Hestia-Panel bekommt die Live-DB NICHT zu sehen —
dieser Seitewagen (docker-compose-Service "telemetry-snapshot", network_mode
none, read-only root) kopiert sie im Rollenintervall per sqlite3-Backup-API in
den freigegebenen Ordner /etc/hestia/hermes-telemetry, den das Panel nur
read-only mountet.

Absichten und Grenzen, bewusst:
  * Backup-API statt Dateikopie: die Snapshot-Datei ist immer eine konsistente
    SQLite-DB, nie ein halber Schreibstand (atomar via tmp + os.replace).
  * Die Quelle ist read-only gemountet und kann WAL führen — deshalb werden
    state.db + -wal + -shm zuerst ins tmpfs kopiert und die Kopie geöffnet
    (dort darf SQLite die WAL anwenden); die Quelle selbst bleibt unangetastet.
  * Fehlt die Quelle (Hermes startet gerade, Seitenwagen läuft zuerst), wird
    stillschweigend im nächsten Intervall erneut versucht.
  * Es wird nichts geloggt außer Statuszeilen — keine Nachrichteninhalte, keine
    Keys, keine Prompts.
"""
import os
import shutil
import sqlite3
import tempfile
import time

SOURCE = os.environ.get("HERMES_STATE_DB_SOURCE", "/source/state.db")
SNAPSHOT = os.environ.get("HERMES_STATE_DB_SNAPSHOT", "/snapshot/state.db")
INTERVAL = float(os.environ.get("HERMES_SNAPSHOT_INTERVAL_SECONDS", "60"))


def _log(msg: str):
    print(f"[hermes-telemetry] {time.strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)


def snapshot_once() -> bool:
    """EIN Snapshot-Lauf. True = Snapshot neu geschrieben, False = nichts getan."""
    if not os.path.isfile(SOURCE):
        return False   # Startverzögerung des Hermes-Containers ist normal — kein Fehler-Log
    tmpdir = tempfile.mkdtemp(prefix="hermes-snap-")
    try:
        # 1. Quelle (inkl. WAL/SHM) ins tmpfs stagen — der ro-Mount selbst darf
        #    von SQLite nicht beschrieben werden, die Kopie darf (WAL-Anwendung).
        stage = os.path.join(tmpdir, "state.db")
        shutil.copy2(SOURCE, stage)
        for suffix in ("-wal", "-shm"):
            side = SOURCE + suffix
            if os.path.isfile(side):
                shutil.copy2(side, stage + suffix)

        # 2. Backup-API von der Kopie auf eine frische DB (konsistent + kompakt).
        src = sqlite3.connect(stage)
        try:
            backup = os.path.join(tmpdir, "snapshot.db")
            dst = sqlite3.connect(backup)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()

        # 3. Atomar in den freigegebenen Ordner (gleiches Dateisystem → os.replace).
        os.makedirs(os.path.dirname(SNAPSHOT), exist_ok=True)
        tmp_final = SNAPSHOT + ".tmp"
        shutil.copyfile(backup, tmp_final)
        os.chmod(tmp_final, 0o644)
        os.replace(tmp_final, SNAPSHOT)
        return True
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def main():
    _log(f"Snapshot-Seitenwagen gestartet (Intervall {INTERVAL:.0f}s, "
         f"Ziel {os.path.dirname(SNAPSHOT)})")
    while True:
        try:
            if snapshot_once():
                _log("state.db-Snapshot aktualisiert")
        except Exception as e:  # noqa: BLE001 — im nächsten Intervall erneut versuchen
            _log(f"Snapshot fehlgeschlagen: {type(e).__name__}: {e}")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
