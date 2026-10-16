#!/usr/bin/env python3
"""fernuni-gate.py – On-Demand-Tor für den FernUni-Compose-Stack (datascience).

Warum es das gibt
-----------------
Der FernUni-Stack (Jupyter/PySpark auf Port 8888, gestartet per `deds candle`)
läuft als Vordergrundprozess im Workspace und bleibt an, bis jemand ihn
stoppt – ~2 GiB RAM in einem 8-GiB-Workspace, den ganzen Tag. Das Tor dreht
das um: Es startet den Stack erst, wenn tatsächlich jemand die Coder-App
öffnet, und stoppt ihn wieder, wenn über eine einstellbare Frist (Standard
3 h) niemand mehr zugreift.

Was es bewusst NICHT ist
------------------------
Kein Container-Orchestrator, kein HTTP-Server, kein Jupyter-Plugin. Es ist ein
roher TCP-Byte-Proxy: jede Verbindung, die auf 127.0.0.1:8891 eingeht, wird auf
127.0.0.1:8888 durchgepumpt. Dadurch versteht es auch die JupyterLab-
WebSockets, ohne etwas über WebSocket oder HTTP zu wissen.

Der FernUni-Stack selbst bleibt UNVERÄNDERT auf Port 8888 – kein
Port-Remapping, `deds candle`, `docker-compose.yml` und die Kursanleitung
funktionieren weiter genau wie gedruckt. Geändert hat sich nur, wohin die
Coder-App `fernuni-jupyter` zeigt: jetzt auf das Tor (8891).

Ablauf einer Anfrage
--------------------
1. Verbindung auf 8891 → Zeitstempel `letzter_zugriff` setzen.
2. Lauscht 8888?  → sofort durchpumpen (Tor ist dann reiner Proxy).
3. Lauscht 8888 nicht?
   - Läuft schon ein Start? → nur die 503-Seite ausliefern (Sperre gegen
     Mehrfachstart, sonst würde jede der ~50 Browser-Anfragen einer
     JupyterLab-Seite einen eigenen `docker compose up` anstoßen).
   - Sonst: Startkommando anstoßen (genau eines gleichzeitig) und die
     503-Seite mit `<meta http-equiv="refresh" content="5">` ausliefern,
     damit der Nutzer eine Erklärung sieht und nicht einen leeren Fehler.
4. Hintergrund-Task: `now - letzter_zugriff > idle_seconds` → stoppen, aber
   NUR wenn der Stack vom Tor selbst gestartet wurde (`selbst_gestartet`).

Hat der Nutzer den Stack selbst per `deds candle` im Terminal laufen, ist
`selbst_gestartet` False – das Tor proxyt dann nur und fasst ihn nie an.

`letzter_zugriff` wird bei jeder neuen Verbindung UND bei jedem tatsächlich
fließenden Byte-Block aktualisiert. Die Byte-Aktualisierung ist wichtig: eine
aktiv benutzte JupyterLab-Seite kann über eine einzige, offene
Keep-Alive-/WebSocket-Verbindung laufen – würde nur die Verbindung zählen,
fiele dem Nutzer der Stack nach 3 h Weiterarbeit unter den Händen weg. Mit
Byte-Zählung heißt es: 3 h ohne jeden Datenverkehr. Eine offene, aber
ungelesene Browser-Registerkarte, in der JupyterLab weiter pollt, hält den
Stack also am Leben; wer den Stack wirklich aus will, schließt die Seite (oder
beendet das Tor, siehe unten).

Start/Stop/Log
--------------
Nur Python-Standardbibliothek (asyncio), kein pip. Alle Pfade und Kommandos
sind per CLI überschreibbar, damit man das Tor ohne Eingriff in den echten
Stack testen kann (siehe --help).

Beenden: SIGTERM/SIGINT → Logzeile, Socket zu. Das Tor stoppt dabei NICHTS;
wer den Stack mit abschalten will, stoppt ihn vorher oder benutzt `deds down`.

Wird von coder-templates/datascience/uni-bootstrap.sh idempotent gestartet
und übersteht damit einen Workspace-Neustart.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ── Vorgaben ───────────────────────────────────────────────────────────────
# Owner/UNI_DIR kommen aus derselben Umgebung wie entrypoint.sh/uni-bootstrap.sh
# (WORKSPACE_OWNER), damit die Defaults auch in einem anders benannten
# Workspace stimmen und nicht fest verdrahtet sind.
OWNER = os.environ.get("WORKSPACE_OWNER") or os.environ.get("WS_OWNER") or "carstenmueller2002"
UNI_DIR = os.environ.get("UNI_DIR") or f"/home/{OWNER}/uni"

DEFAULT_LISTEN = "127.0.0.1:8891"
DEFAULT_TARGET = "127.0.0.1:8888"
DEFAULT_REPO = f"{UNI_DIR}/data-engineering-infrastructure"
DEFAULT_START = "env UID=1000 GID=1000 docker compose -f docker-compose.yml up -d client-node"
DEFAULT_STOP = "env UID=1000 GID=1000 docker compose -f docker-compose.yml stop client-node"
DEFAULT_IDLE = 10800.0  # 3 h
DEFAULT_LOG = f"{UNI_DIR}/logs/fernuni-gate.log"

# Die 503-Seite: deutscher Text + Auto-Refresh, damit beim ersten Start
# (Images ziehen kann 1–2 min dauern) kein leerer Browserfehler erscheint.
HTML_503 = """<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="5">
<title>FernUni-Stack wird gestartet</title>
<style>
  body { font-family: system-ui, sans-serif; margin: 3rem auto; max-width: 42rem;
         background: #101418; color: #e6e6e6; line-height: 1.5; }
  h1 { font-size: 1.4rem; }
  code { background: #1d2329; padding: .1rem .3rem; border-radius: .2rem; }
  .klein { color: #9aa4ad; font-size: .9rem; }
</style>
</head>
<body>
<h1>Der FernUni-Stack wird gestartet …</h1>
<p>Diese Seite wurde vom On-Demand-Tor ausgeliefert. Auf Port <code>8888</code>
lauscht noch nichts, deshalb wurde der Stack gerade gestartet
(<code>docker compose up -d client-node</code>).</p>
<p><strong>Beim ersten Mal dauert es ein bis zwei Minuten</strong> – die Images
müssen unter Umständen erst gezogen und Spark/Hadoop hochgefahren werden.
Danach lädt diese Seite automatisch neu und zeigt Jupyter.</p>
<p class="klein">Automatischer Neuversuch alle 5 Sekunden. Den echten Stack
selbst gestartet? Dann läuft er auf Port 8888 wie bisher
(<code>deds candle</code>) – das Tor proxyt ihn dann nur.</p>
</body>
</html>
"""


def _env_float(name: str, default: float) -> float:
    roh = os.environ.get(name)
    if not roh:
        return default
    try:
        return float(roh)
    except ValueError:
        return default


def parse_addr(text: str) -> tuple[str, int]:
    """'host:port' oder nur 'port' → (host, port). Ohne Host: 127.0.0.1."""
    host, _, port = text.strip().rpartition(":")
    if not port:
        raise ValueError(f"Adresse ohne Port: {text!r}")
    if not host:
        host = "127.0.0.1"
    return host.strip("[]"), int(port)


def antwort_503() -> bytes:
    body = HTML_503.encode("utf-8")
    kopf = (
        "HTTP/1.1 503 Service Unavailable\r\n"
        "Retry-After: 5\r\n"
        "Content-Type: text/html; charset=utf-8\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Cache-Control: no-store\r\n"
        "Connection: close\r\n"
        "\r\n"
    )
    return kopf.encode("ascii") + body


ANTWORT_503 = antwort_503()


class FernuniTor:
    def __init__(self, args: argparse.Namespace) -> None:
        self.listen_host, self.listen_port = parse_addr(args.listen)
        self.target_host, self.target_port = parse_addr(args.target)
        self.repo = args.repo
        self.start_cmd = args.start_cmd
        self.stop_cmd = args.stop_cmd
        self.idle_seconds = args.idle_seconds
        self.start_timeout = args.start_timeout
        self.cmd_timeout = args.cmd_timeout
        self.log_path = Path(args.log)

        # Zustand. `letzter_zugriff` = 0.0 heißt „seit dem Start des Tores noch
        # kein Zugriff“ – dann läuft auch nichts, was gestoppt werden müsste.
        self.letzter_zugriff = 0.0
        self.selbst_gestartet = False
        self.start_laeuft = False
        self.stop_laeuft = False
        self.aktiv = 0
        self.server: asyncio.AbstractServer | None = None

    # ── Log ────────────────────────────────────────────────────────────────
    def log(self, ereignis: str) -> None:
        """Eine Zeile je Ereignis – nie pro Byte."""
        zeile = f"{datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')} {ereignis}\n"
        try:
            with open(self.log_path, "a", encoding="utf-8") as fh:
                fh.write(zeile)
        except OSError as exc:  # Log darf das Tor nie umbringen
            print(f"# Log nicht schreibbar ({exc})", file=sys.stderr, flush=True)
        if sys.stdout.isatty():
            # Interaktiv im Terminal sichtbar; im Bootstrap ist stdout kein TTY,
            # sonst stünde jede Zeile doppelt in ~/uni/logs/fernuni-gate.log
            # (Bootstrap leitet stdout in dieselbe Datei um).
            sys.stdout.write(zeile)
            sys.stdout.flush()

    # ── Kommandos ──────────────────────────────────────────────────────────
    async def kommando(self, cmd: str) -> tuple[int, str]:
        cwd = self.repo if os.path.isdir(self.repo) else None
        if cwd is None:
            self.log(f"WARNUNG Repo-Ordner fehlt ({self.repo}) – Kommando läuft in {os.getcwd()}")
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except OSError as exc:
            return 127, repr(exc)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=self.cmd_timeout)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            with contextlib.suppress(Exception):
                await proc.wait()
            return 124, f"Zeitüberschreitung nach {self.cmd_timeout:.0f} s"
        text = out.decode("utf-8", "replace").strip().replace("\n", " | ")
        return proc.returncode or 0, text[-500:]

    # ── Ziel-Erkennung ─────────────────────────────────────────────────────
    async def ziel_antwortet(self, timeout: float = 2.0) -> bool:
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(self.target_host, self.target_port), timeout
            )
        except (OSError, asyncio.TimeoutError):
            return False
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        return True

    # ── Start / Stop ───────────────────────────────────────────────────────
    async def starten_anfordern(self) -> bool:
        """Genau ein Start gleichzeitig. True = dieses Gespräch hat ihn ausgelöst."""
        if self.start_laeuft:
            return False
        self.start_laeuft = True  # synchron setzen: die Sperre greift noch vor dem Task
        asyncio.create_task(self.stack_starten())
        return True

    async def stack_starten(self) -> None:
        # Die Sperre `start_laeuft` hält `starten_anfordern`; hier nur freigeben.
        try:
            ziel = f"{self.target_host}:{self.target_port}"
            self.log(f"Start angefordert (auf {ziel} lauscht nichts) – {self.start_cmd}")
            t0 = time.monotonic()
            rc, ausgabe = await self.kommando(self.start_cmd)
            if rc != 0:
                self.log(f"FEHLER Startkommando beendet mit Exit {rc}: {ausgabe}")

            while time.monotonic() - t0 < self.start_timeout:
                if await self.ziel_antwortet():
                    dauer = time.monotonic() - t0
                    self.selbst_gestartet = True
                    self.letzter_zugriff = time.time()
                    self.log(f"Stack oben (nach {dauer:.0f} s, Ziel {ziel}) – Leerlauf-Stopp nach {self.idle_seconds:.0f} s")
                    return
                await asyncio.sleep(1.0)

            self.log(
                f"FEHLER {ziel} antwortet auch nach {self.start_timeout:.0f} s nicht – "
                f"Start abgebrochen (Images fehlen? Log: {self.repo}/…)"
            )
        except Exception as exc:  # Tor darf an einem misslungenen Start nicht sterben
            self.log(f"FEHLER beim Starten: {exc!r}")
        finally:
            self.start_laeuft = False

    async def stack_stoppen(self) -> None:
        self.log(
            f"Leerlauf-Stopp ({self.idle_seconds:.0f} s ohne Zugriff auf "
            f"{self.listen_host}:{self.listen_port}) – {self.stop_cmd}"
        )
        rc, ausgabe = await self.kommando(self.stop_cmd)
        if rc == 0:
            self.selbst_gestartet = False
            self.log("Stack gestoppt (nur der vom Tor gestartete Client-Node)")
        else:
            # Nicht in eine Endlosschleife laufen: erst nach einer weiteren
            # vollen Leerlauf-Frist erneut versuchen.
            self.letzter_zugriff = time.time()
            self.log(f"FEHLER Stoppkommando beendet mit Exit {rc}: {ausgabe}")

    async def leerlauf_waechter(self) -> None:
        if self.idle_seconds <= 0:
            self.log("Leerlauf-Stopp deaktiviert (--idle-seconds <= 0)")
            return
        intervall = max(1.0, min(10.0, self.idle_seconds / 6.0))
        while True:
            await asyncio.sleep(intervall)
            if not self.selbst_gestartet or self.start_laeuft or self.stop_laeuft:
                continue
            if not self.letzter_zugriff:
                continue
            if time.time() - self.letzter_zugriff <= self.idle_seconds:
                continue
            self.stop_laeuft = True
            try:
                await self.stack_stoppen()
            except Exception as exc:
                self.log(f"FEHLER beim Stoppen: {exc!r}")
            finally:
                self.stop_laeuft = False

    # ── Proxy ──────────────────────────────────────────────────────────────
    async def sende_503(self, writer: asyncio.StreamWriter) -> None:
        # Die Anfrage wird bewusst NICHT gelesen: der Browser hat sie schon
        # geschickt, wir antworten direkt und schließen.
        with contextlib.suppress(Exception):
            writer.write(ANTWORT_503)
            await writer.drain()

    async def pumpe(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                daten = await reader.read(65536)
                if not daten:
                    break
                self.letzter_zugriff = time.time()
                writer.write(daten)
                await writer.drain()
        except (
            ConnectionResetError,
            ConnectionAbortedError,
            BrokenPipeError,
            asyncio.IncompleteReadError,
            TimeoutError,
        ):
            pass
        finally:
            with contextlib.suppress(Exception):
                if writer.can_write_eof():
                    writer.write_eof()
                    await writer.drain()
                else:
                    writer.close()

    async def handle_client(
        self, creader: asyncio.StreamReader, cwriter: asyncio.StreamWriter
    ) -> None:
        self.letzter_zugriff = time.time()
        self.aktiv += 1
        peer = "-"
        with contextlib.suppress(Exception):
            info = cwriter.get_extra_info("peername")
            if info:
                peer = f"{info[0]}:{info[1]}"
        ziel = f"{self.target_host}:{self.target_port}"

        try:
            treader: asyncio.StreamReader | None = None
            twriter: asyncio.StreamWriter | None = None
            try:
                treader, twriter = await asyncio.wait_for(
                    asyncio.open_connection(self.target_host, self.target_port), 5.0
                )
            except (OSError, asyncio.TimeoutError):
                treader = twriter = None

            if twriter is None or treader is None:
                ausgeloest = await self.starten_anfordern()
                status = "Start angefordert" if ausgeloest else "Start läuft bereits"
                self.log(f"Zugriff von {peer} – {ziel} tot, {status} → 503")
                await self.sende_503(cwriter)
                return

            self.log(f"Zugriff von {peer} – proxy → {ziel}")
            try:
                await asyncio.gather(
                    self.pumpe(creader, twriter),
                    self.pumpe(treader, cwriter),
                    return_exceptions=True,
                )
            finally:
                for w in (twriter, cwriter):
                    with contextlib.suppress(Exception):
                        w.close()
        except Exception as exc:
            self.log(f"FEHLER Verbindung von {peer}: {exc!r}")
        finally:
            self.aktiv -= 1
            with contextlib.suppress(Exception):
                cwriter.close()
            with contextlib.suppress(Exception):
                await cwriter.wait_closed()

    # ── Hauptschleife ──────────────────────────────────────────────────────
    async def laufen(self) -> None:
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            print(f"# Log-Ordner {self.log_path.parent} nicht anlegbar: {exc}", file=sys.stderr)

        loop = asyncio.get_running_loop()
        stop = asyncio.Event()

        def beenden(signame: str) -> None:
            self.log(f"Signal {signame} – Tor beendet (aktive Verbindungen: {self.aktiv})")
            stop.set()

        for sig in (signal.SIGTERM, signal.SIGINT):
            with contextlib.suppress(NotImplementedError, AttributeError):
                loop.add_signal_handler(sig, beenden, sig.name)

        try:
            self.server = await asyncio.start_server(
                self.handle_client, self.listen_host, self.listen_port, limit=2**20
            )
        except OSError as exc:
            self.log(f"FEHLER Tor kann {self.listen_host}:{self.listen_port} nicht binden: {exc}")
            return

        sock = self.server.sockets[0].getsockname() if self.server.sockets else ()
        self.log(
            f"Tor lauscht auf {self.listen_host}:{self.listen_port} (socket={sock[0]}:{sock[1]}) "
            f"→ {self.target_host}:{self.target_port} | Leerlauf-Stopp {self.idle_seconds:.0f} s "
            f"| Repo {self.repo} | Log {self.log_path}"
        )

        waechter = asyncio.create_task(self.leerlauf_waechter())
        try:
            async with self.server:
                await stop.wait()
        finally:
            # Einen laufenden Leerlauf-Stopp nicht mitten in `docker compose
            # stop` abschneiden – sonst bliebe der Stack halb gestoppt stehen.
            for _ in range(20):
                if not self.stop_laeuft:
                    break
                await asyncio.sleep(0.5)
            waechter.cancel()
            # CancelledError ist BaseException – `suppress(Exception)` reicht nicht,
            # sonst schreibt jeder SIGTERM einen Traceback ins Log.
            with contextlib.suppress(asyncio.CancelledError, KeyboardInterrupt, Exception):
                await waechter
            self.server.close()
            with contextlib.suppress(Exception):
                await self.server.wait_closed()
            self.log("Tor ist aus")


def argumente() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "On-Demand-Tor für den FernUni-Compose-Stack: startet den Stack beim "
            "ersten Zugriff, proxyt TCP 1:1 und stoppt nach Leerlauf, aber nur "
            "was es selbst gestartet hat."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--listen", default=DEFAULT_LISTEN, help="Lauschadresse host:port")
    p.add_argument("--target", default=DEFAULT_TARGET, help="Ziel des Proxys host:port")
    p.add_argument("--repo", default=DEFAULT_REPO, help="Arbeitsverzeichnis für Start-/Stoppkommando")
    p.add_argument("--start-cmd", default=DEFAULT_START, help="Kommando, das den Stack startet")
    p.add_argument("--stop-cmd", default=DEFAULT_STOP, help="Kommando, das den Stack stoppt")
    p.add_argument(
        "--idle-seconds",
        type=float,
        default=_env_float("FERNUNI_GATE_IDLE_SECONDS", DEFAULT_IDLE),
        help="Leerlauf bis zum Stopp in Sekunden (0 = nie stoppen)",
    )
    p.add_argument("--start-timeout", type=float, default=300.0, help="Wartezeit auf den Start in Sekunden")
    p.add_argument("--cmd-timeout", type=float, default=180.0, help="Zeitlimit je Start-/Stoppkommando in Sekunden")
    p.add_argument("--log", default=DEFAULT_LOG, help="Logdatei (eine Zeile je Ereignis)")
    return p.parse_args()


def main() -> int:
    args = argumente()
    try:
        asyncio.run(FernuniTor(args).laufen())
    except (KeyboardInterrupt, asyncio.CancelledError):
        # SIGINT/SIGTERM mitten im Herunterfahren: kein Traceback, kein Fehler.
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
