#!/usr/bin/env python3
"""oc-dispatch.py - idempotenter OpenCode-Dispatch fuer Hermes.

Problem (12.10.2026): Der Agent legte per curl eine OpenCode-Session an und
stuerzte danach ab (Antwort-Parsing). Er retryte und erzeugte dabei 3 Sessions,
2 davon leer. Ursache: "Session anlegen" war ein frei wiederholbarer Einzelaufruf
und damit nicht idempotent.

Loesung: Dieses Skript kapselt "Session anlegen + Prompt feuern" als EINE
retry-sichere Operation. Es wird NIE vom Modell selbst geschrieben, nur aufgerufen.

Garantien:
  * Pro (Verzeichnis, Auftragstext) existiert hoechstens EINE Session.
  * Die Session-ID wird sofort nach dem Anlegen persistiert - noch BEVOR der
    Prompt gefeuert wird. Bricht der Prozess danach ab, adoptiert der naechste
    Aufruf die vorhandene Session statt eine neue anzulegen.
  * Selbst wenn die Persistierung komplett verloren ging, sucht das Skript eine
    vorhandene Session mit gleichem Titel + Verzeichnis (Zeitfenster
    --recover-minutes) und adoptiert sie ("healing").
  * Ein zweiter Aufruf feuert den Prompt NICHT erneut (--force-prompt erzwingt).
    Er fragt dafuer den Server ab, ob die Session schon eine User-Nachricht hat -
    das greift auch, wenn die Registry dazwischen verloren ging.
  * --sid <sid> pinnt eine bestehende Session fuer die Fortsetzung. Damit wird
    unabhaengig von Titel, Zeitstempel und Registry garantiert genau diese
    Session verwendet.

Aufruf:
  /opt/hermes/.venv/bin/python /opt/data/bin/oc-dispatch.py \
      --dir /home/carstenmueller2002/opencode-coder-env/workspaces/infrastructure/server-management \
      --title "H: Kurztitel 20261013-1930" \
      --task-file /tmp/auftrag.txt

  ... oder --text "..." als Argument, oder Auftrag via stdin:
  printf '%s' "$AUFTRAG" | oc-dispatch.py --dir ... --title ...

Optionen:
  --agent fast|medium|heavy
                        Hestia-Tier, dessen Modell uebernommen wird (Default
                        medium). Das Modell wird beim Agenten gelesen, nicht hier
                        festgelegt - Panel-Aenderungen ziehen damit automatisch mit.
  --model provider/model[#variant]
                        Explizites Modell; ueberschreibt --agent.
  --force-prompt        Prompt erneut feuern (z.B. Fortsetzung), auch wenn erledigt
  --dry-run             Nur Status zeigen, nichts aendern
  --recover-minutes N   Ad-hoc-/Healing-Fenster in Minuten (Default 30, 0 = aus)
  --json                Ausgabe als JSON

Ausgabe (immer maschinenlesbar auf stdout):
  SID=<session-id>
  STATUS=new|adopted|reused
  PROMPT=sent|skipped|failed
  TITLE=<titel>
  DIRECTORY=<verzeichnis>

Wichtig: Ein erneuter Aufruf mit demselben Auftrag ist GEFAHRLOS. Er erzeugt
keine zweite Session.
"""

import argparse
import base64
import errno
import fcntl
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

ENV_FILE = "/opt/data/.env"
STATE_DIR = "/opt/data/state/oc-dispatch"
REGISTRY = os.path.join(STATE_DIR, "registry.json")
LOCKFILE = os.path.join(STATE_DIR, "lock")
PASSWORD_VAR = "OPENCODE_SERVER_PASSWORD"

TIMESTAMP_SUFFIX = re.compile(r"\s*\d{8}-\d{4}\s*$")


class DispatchError(Exception):
    """Fehler, der dem Modell eine klare, retry-sichere Handlung vorgibt."""


def log(msg):
    sys.stderr.write("[oc-dispatch] %s\n" % msg)


def load_password():
    """Liest das Serverpasswort. Wird niemals ausgegeben."""
    try:
        with open(ENV_FILE, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith(PASSWORD_VAR + "="):
                    value = line.split("=", 1)[1].strip()
                    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                        value = value[1:-1]
                    if value:
                        return value
    except OSError as exc:
        raise DispatchError("Kann %s nicht lesen: %s" % (ENV_FILE, exc))
    raise DispatchError(
        "%s enthaelt kein %s. Dispatch abgebrochen - es wurde KEINE Session angelegt."
        % (ENV_FILE, PASSWORD_VAR)
    )


def sanitize_model(model):
    """Nur die von der API erlaubten Felder (Model.Ref: additionalProperties=false)."""
    if not isinstance(model, dict):
        return None
    clean = {"id": model.get("id"), "providerID": model.get("providerID")}
    if model.get("variant"):
        clean["variant"] = model["variant"]
    if not clean["id"] or not clean["providerID"]:
        return None
    return clean


def model_ref(model):
    """{providerID,id,variant} -> 'provider/id#variant' (nur fuer Logs)."""
    if not isinstance(model, dict):
        return "-"
    ref = "%s/%s" % (model.get("providerID") or "?", model.get("id") or "?")
    if model.get("variant"):
        ref += "#%s" % model["variant"]
    return ref


def models_equal(left, right):
    """id/providerID muessen exakt passen; variant nur, wenn beide gesetzt sind.

    Bewusst tolerant bei fehlender variant: die Variante ist Feinschliff, die
    Modell-Identitaet ist das Sicherheitskriterium. Eine fehlende Variante darf
    den Dispatch nicht blockieren.
    """
    left = sanitize_model(left)
    right = sanitize_model(right)
    if not left or not right:
        return False
    if left["id"] != right["id"] or left["providerID"] != right["providerID"]:
        return False
    left_variant, right_variant = left.get("variant"), right.get("variant")
    if left_variant and right_variant and left_variant != right_variant:
        return False
    return True


def parse_model_spec(spec):
    """'provider/modelID[#variant]' -> Model.Ref."""
    raw = (spec or "").strip()
    if "/" not in raw:
        raise DispatchError(
            "--model braucht die Form provider/modelID[#variant], z. B. "
            "'opencode/space-bunny-free#high' (bekam: %r)" % spec
        )
    ref, _, variant = raw.partition("#")
    provider, _, model_id = ref.partition("/")
    model = {"id": model_id.strip(), "providerID": provider.strip()}
    if variant.strip():
        model["variant"] = variant.strip()
    if not model["id"] or not model["providerID"]:
        raise DispatchError("--model ist unvollstaendig: %r" % spec)
    return model


class OpenCodeClient(object):
    def __init__(self, base_url, password, timeout=45):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        token = base64.b64encode(
            ("opencode:" + password).encode("utf-8")
        ).decode("ascii")
        self.auth_header = "Basic " + token

    def request(self, method, path, body=None):
        """Fuehrt einen API-Aufruf aus. Gibt (status, parsed_json) zurueck."""
        url = self.base_url + path
        data = None
        headers = {"Authorization": self.auth_header, "Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                return resp.status, self._parse(raw)
        except urllib.error.HTTPError as exc:
            raw = ""
            try:
                raw = exc.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            return exc.code, self._parse(raw)
        except urllib.error.URLError as exc:
            raise DispatchError(
                "OpenCode nicht erreichbar (%s). Versand nicht moeglich - "
                "nochmal versuchen ist gefahrlos." % exc.reason
            )

    @staticmethod
    def _parse(raw):
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except ValueError:
            return {"_raw": raw[:400]}

    def list_sessions(self):
        status, payload = self.request("GET", "/api/session")
        if status != 200:
            raise DispatchError("GET /api/session lieferte HTTP %s" % status)
        if isinstance(payload, dict):
            return payload.get("data") or []
        return payload or []

    def session_exists(self, sid):
        status, _ = self.request("GET", "/api/session/%s" % sid)
        return status == 200

    def has_user_messages(self, sid):
        """True, wenn die Session bereits eine User-Nachricht enthaelt.

        Zweite Idempotenz-Stufe: selbst wenn die Registry verloren ging, wird
        ein bereits versandter Auftrag nicht ein zweites Mal gefeuert.
        """
        status, payload = self.request(
            "GET", "/api/session/%s/message?limit=5" % sid
        )
        if status != 200:
            return False
        messages = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(messages, list):
            return False
        for message in messages:
            if not isinstance(message, dict):
                continue
            # OpenCode v2 liefert "type"; aeltere Versionen nutzen "role".
            if message.get("type") == "user" or message.get("role") == "user":
                return True
        return False

    def pending_form_id(self, sid):
        """Form-ID, wenn auf dieser Session ein Formular auf Antwort wartet.

        Genau das ist der Fall, wenn der Vater (Nutzer) auf eine Rueckfrage
        antwortet: die Facharbeiter-Session blockiert im Formular und wartet.
        """
        status, payload = self.request("GET", "/api/session/%s/form" % sid)
        if status != 200:
            return None
        data = payload.get("data") if isinstance(payload, dict) else payload
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            return None
        for form in data:
            if isinstance(form, dict) and form.get("id"):
                return form["id"]
        return None

    def agent_model(self, agent):
        """Tier-Modell eines Agenten erfragen.

        Der Agent traegt das von Hestia im Panel gesetzte Tier-Modell. Das ist die
        einzige Quelle, die einer Panel-Aenderung automatisch folgt - ohne
        Modellpflege in diesem Skript.
        """
        status, payload = self.request("GET", "/api/agent/%s" % agent)
        if status != 200:
            raise DispatchError(
                "GET /api/agent/%s lieferte HTTP %s - Tier-Modell nicht "
                "ermittelbar. Dispatch abgebrochen, es wurde KEINE Session angelegt."
                % (agent, status)
            )
        data = payload.get("data") if isinstance(payload, dict) else payload
        model = None
        if isinstance(data, dict):
            model = sanitize_model(data.get("model"))
        if not model:
            raise DispatchError(
                "Agent %r traegt kein verwendbares Modell. Dispatch abgebrochen, "
                "es wurde KEINE Session angelegt." % agent
            )
        return model

    def session_model(self, sid):
        """Aktuelles Modell einer Session lesen (fuer die Verifikation)."""
        status, payload = self.request("GET", "/api/session/%s" % sid)
        if status != 200:
            return None
        data = payload.get("data") if isinstance(payload, dict) else payload
        return (data or {}).get("model") if isinstance(data, dict) else None

    def set_session_model(self, sid, model):
        status, payload = self.request(
            "POST", "/api/session/%s/model" % sid, {"model": sanitize_model(model)}
        )
        if status not in (200, 201, 204):
            raise DispatchError(
                "POST /api/session/%s/model lieferte HTTP %s: %s"
                % (sid, status, str(payload)[:200])
            )

    def ensure_session_model(self, sid, wanted):
        """Stellt sicher, dass die Session genau auf `wanted` laeuft.

        Reihenfolge: lesen -> bei Abweichung nativ umstellen -> erneut lesen.
        Schlaegt die Verifikation fehl, wird KEIN Prompt gefeuert.
        """
        current = self.session_model(sid)
        if models_equal(current, wanted):
            return current
        log("Session %s traegt %s - stelle auf %s um."
            % (sid, model_ref(current), model_ref(wanted)))
        self.set_session_model(sid, wanted)
        current = self.session_model(sid)
        if not models_equal(current, wanted):
            raise DispatchError(
                "Session %s laeuft auf %s statt auf %s. Prompt NICHT gesendet."
                % (sid, model_ref(current), model_ref(wanted))
            )
        return current

    def create_session(self, directory, title, model):
        status, payload = self.request(
            "POST",
            "/api/session",
            {
                "location": {"directory": directory},
                "title": title,
                "model": sanitize_model(model),
            },
        )
        if status not in (200, 201):
            raise DispatchError(
                "POST /api/session lieferte HTTP %s: %s - keine Session angelegt."
                % (status, str(payload)[:200])
            )
        sid = None
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, dict):
                sid = data.get("id")
            if not sid:
                sid = payload.get("id")
        if not sid:
            raise DispatchError(
                "POST /api/session: keine ID in der Antwort: %s" % str(payload)[:200]
            )
        return sid

    def fire_prompt(self, sid, text):
        status, payload = self.request(
            "POST", "/api/session/%s/prompt" % sid, {"text": text}
        )
        if status not in (200, 201, 202):
            raise DispatchError(
                "POST /api/session/%s/prompt lieferte HTTP %s: %s"
                % (sid, status, str(payload)[:200])
            )
        return payload


DATEISH = re.compile(r"\d{4}-\d{2}-\d{2}|\d{8}[-_]?\d{4}|\d{1,2}:\d{2}")
NOISE_WORDS = {"neu", "wieder", "erneut", "retry", "wiederholung", "neuversuch"}
MIN_PREFIX_MATCH = 8


def normalize_title(title):
    """Vereinheitlicht einen Session-Titel fuer den Adoption-Abgleich.

    Entfernt trailing Klammern sowie trailing Tokens, die wie ein Zeitstempel
    oder ein Flickwort aussehen - bewahrt aber Tokens, die Teil des Themas sind:
      "H: Linkwarden Verfuegbarkeit 20261012-1358"      -> "h: linkwarden verfuegbarkeit"
      "H: Linkwarden Verfuegbarkeit (wiederholt) 14:02"  -> "h: linkwarden verfuegbarkeit"
      "H: selftest-t2"                                   -> "h: selftest-t2"  (bleibt!)
      "H: selftest-t2 NEU-20260101-0000"                 -> "h: selftest-t2"
    """
    text = (title or "").strip().lower()
    changed = True
    while changed and text:
        changed = False
        stripped = re.sub(r"\s*[\(\[\{][^\)\]\}]*[\)\]\}]\s*$", "", text)
        if stripped != text:
            text, changed = stripped, True
        tokens = text.split()
        if tokens:
            last = tokens[-1]
            if DATEISH.search(last) or last.strip(".,;:") in NOISE_WORDS:
                tokens.pop()
                text, changed = " ".join(tokens), True
    return text.strip()


def titles_match(left, right):
    """Passt ein Titel zum anderen (gleich oder einer ist Prefix des anderen)?"""
    a = normalize_title(left)
    b = normalize_title(right)
    if not a or not b:
        return False
    if a == b:
        return True
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    # Mindestlaenge verhindert Fehltreffer wie "h: test" -> "h: testbackup".
    if len(shorter) < MIN_PREFIX_MATCH:
        return False
    return longer.startswith(shorter)


def task_key(directory, text):
    # Verzeichnis normalisieren, damit "/a/b" und "/a/b/" denselben Key ergeben.
    normalized_dir = (directory or "").strip().rstrip("/") or "/"
    raw = normalized_dir + "\x00" + (text or "")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def load_registry():
    try:
        with open(REGISTRY, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_registry(registry):
    """Atomar schreiben - damit ein Abbruch mitten im Schreiben nichts zerstoert."""
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = REGISTRY + ".tmp.%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(registry, fh, indent=2, sort_keys=True)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, REGISTRY)


class Lock(object):
    """Exklusiver Lock, damit parallele Aufrufe keine Doppel-Session erzeugen."""

    def __init__(self, timeout=60.0):
        self.timeout = timeout
        self.handle = None

    def __enter__(self):
        os.makedirs(STATE_DIR, exist_ok=True)
        self.handle = open(LOCKFILE, "a+")
        deadline = time.time() + self.timeout
        while True:
            try:
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN):
                    raise
                if time.time() >= deadline:
                    raise DispatchError(
                        "Warte zu lange auf den oc-dispatch-Lock (%s). "
                        "Ein anderer Dispatch laeuft noch." % LOCKFILE
                    )
                time.sleep(0.25)

    def __exit__(self, exc_type, exc, tb):
        if self.handle is not None:
            try:
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            finally:
                self.handle.close()
        return False


def adopt_existing(sessions, directory, title, recover_minutes):
    """Healing: eine bereits serverseitig existierende Session uebernehmen."""
    if recover_minutes <= 0:
        return None
    window_ms = recover_minutes * 60 * 1000
    now_ms = int(time.time() * 1000)
    wanted_dir = (directory or "").rstrip("/")
    best = None
    for session in sessions:
        if not isinstance(session, dict):
            continue
        location = session.get("location") or {}
        existing_dir = (location.get("directory") or "").rstrip("/")
        if existing_dir != wanted_dir:
            continue
        if not titles_match(session.get("title"), title):
            continue
        created = ((session.get("time") or {}).get("created")) or 0
        if created and abs(now_ms - created) > window_ms:
            continue
        if best is None or created > ((best.get("time") or {}).get("created") or 0):
            best = session
    if best and best.get("id"):
        return best["id"]
    return None


def adopt_pending_form(client, sessions, directory):
    """Healing 2: Session mit OFFENEM FORMULAR im selben Verzeichnis uebernehmen.

    Greift, wenn der Vater eine Rueckfrage beantwortet und der Turn neu
    startet: dann ist der Auftragstext im zweiten Turn fast nie wortgleich
    (Registry-Miss) und der Titel weicht ab (Adoptions-Miss) - ohne diesen
    Fallback entstaende eine zweite Session und die Antwort waere verloren.

    Bewusst streng: nur EINE Session mit offenem Formular im Verzeichnis
    genuegt; bei mehreren ist unklar, welche gemeint ist, und es wird nichts
    adoptiert (kein Fehl-Ziel).
    """
    wanted_dir = (directory or "").rstrip("/")
    candidates = []
    for session in sessions:
        if not isinstance(session, dict):
            continue
        location = session.get("location") or {}
        if (location.get("directory") or "").rstrip("/") != wanted_dir:
            continue
        sid = session.get("id")
        if not sid:
            continue
        try:
            form_id = client.pending_form_id(sid)
        except Exception:
            continue
        if form_id:
            created = ((session.get("time") or {}).get("created")) or 0
            candidates.append((created or 0, sid, form_id))
    if len(candidates) == 1:
        return candidates[0][1], candidates[0][2]
    return None, None


def read_task_text(args):
    if args.task_file:
        if args.task_file == "-":
            return sys.stdin.read()
        try:
            with open(args.task_file, "r", encoding="utf-8") as fh:
                return fh.read()
        except OSError as exc:
            raise DispatchError("Auftragsdatei nicht lesbar: %s" % exc)
    if args.text is not None:
        return args.text
    if not sys.stdin.isatty():
        return sys.stdin.read()
    raise DispatchError(
        "Kein Auftrag uebergeben. Nutze --task-file <pfad>, --text \"...\" oder stdin."
    )


def emit(args, sid, status, prompt_state, title, directory):
    if args.json:
        print(json.dumps({
            "sid": sid,
            "status": status,
            "prompt": prompt_state,
            "title": title,
            "directory": directory,
        }, ensure_ascii=False))
    else:
        print("SID=%s" % (sid or "-"))
        print("STATUS=%s" % status)
        print("PROMPT=%s" % prompt_state)
        print("TITLE=%s" % title)
        print("DIRECTORY=%s" % directory)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Idempotenter OpenCode-Dispatch (Session anlegen + Prompt feuern)."
    )
    parser.add_argument("--dir", "--directory", dest="directory", required=True,
                        help="Workspace-Verzeichnis fuer location.directory")
    parser.add_argument("--title", required=True,
                        help='Session-Titel, z. B. "H: Kurztitel 20261013-1930"')
    parser.add_argument("--task-file", default=None,
                        help="Datei mit dem Auftrag ('-' = stdin). Empfohlen.")
    parser.add_argument("--text", default=None,
                        help="Auftrag direkt als Argument (besser: --task-file)")
    parser.add_argument("--agent", default="medium",
                        choices=["fast", "medium", "heavy"],
                        help="Hestia-Tier, dessen Modell verwendet wird (Default "
                             "medium). Das Modell wird beim Agenten gelesen, nicht "
                             "hier festgelegt.")
    parser.add_argument("--model", default=None,
                        help="Explizites Modell provider/modelID[#variant], z. B. "
                             "'opencode/space-bunny-free#high'. Ueberschreibt --agent.")
    parser.add_argument("--base-url",
                        default=os.environ.get(
                            "OPENCODE_BASE_URL",
                            "http://coder-carstenmueller2002-infrastructure:49374",
                        ))
    parser.add_argument("--force-prompt", action="store_true",
                        help="Prompt erneut feuern (Fortsetzung)")
    parser.add_argument("--sid", default=None,
                        help="Bestehende Session direkt verwenden (Fortsetzung). "
                             "Ueberschreibt Registry und Adoption.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Nur Status zeigen, nichts aendern")
    parser.add_argument("--recover-minutes", type=int, default=30,
                        help="Healing-Fenster fuer verwaiste Sessions (Default 30)")
    parser.add_argument("--json", action="store_true", help="Ausgabe als JSON")
    args = parser.parse_args(argv)

    directory = args.directory.rstrip("/")
    text = read_task_text(args).strip()
    if not text:
        raise DispatchError("Leerer Auftrag - Dispatch abgebrochen.")
    # KEIN lokaler isdir-Check: das Workspace-Verzeichnis existiert im
    # OpenCode-Container, nicht im Hermes-Container. Validiert wird stattdessen
    # gegen die Verzeichnisse, die der OpenCode-Server kennt (siehe unten).

    client = OpenCodeClient(args.base_url, load_password())
    # Modell VOR allem anderen festlegen. Ohne diesen Schritt erbt die Session den
    # Server-Default (/api/model/default), der nicht setzbar und nicht ueberwacht
    # ist - genau so kam am 14.10.2026 ein unbrauchbares Modell in den Lauf.
    if args.model:
        wanted_model = parse_model_spec(args.model)
        model_source = "--model"
    else:
        wanted_model = client.agent_model(args.agent)
        model_source = "Tier @%s" % args.agent
    log("Modell: %s (aus %s)" % (model_ref(wanted_model), model_source))
    key = task_key(directory, text)

    with Lock():
        registry = load_registry()
        entry = registry.get(key) or {}
        sid = None
        status = None

        if args.sid:
            # Fortsetzung: der Aufrufer pinnt die Session explizit. Damit ist
            # garantiert, dass genau diese Session weiterverwendet wird - unab-
            # haengig von Titel, Zeitstempel und Registry-Zustand.
            if not client.session_exists(args.sid):
                raise DispatchError(
                    "Session %s existiert nicht (mehr). Nichts gesendet." % args.sid
                )
            sid = args.sid
            status = "explicit"
            log("Explizite Session %s uebernommen." % sid)
        elif entry.get("sid") and client.session_exists(entry["sid"]):
            sid = entry["sid"]
            status = "reused"
            log("Registry-Treffer: Session %s wird wiederverwendet." % sid)
        elif entry.get("sid"):
            log("Session %s existiert nicht mehr - lege neue an." % entry["sid"])
            registry.pop(key, None)

        if sid is None:
            sessions = client.list_sessions()
            known_dirs = set()
            for session in sessions:
                if isinstance(session, dict):
                    known = (session.get("location") or {}).get("directory")
                    if known:
                        known_dirs.add(known.rstrip("/"))
            if known_dirs and directory not in known_dirs:
                log("WARNUNG: Der Server kennt %s bisher nicht (%d bekannte "
                    "Verzeichnisse). Moeglicher Tippfehler - es wird trotzdem "
                    "angelegt." % (directory, len(known_dirs)))
            adopted = adopt_existing(
                sessions, directory, args.title, args.recover_minutes
            )
            if adopted:
                sid = adopted
                status = "adopted"
                log("Verwaiste Session %s adoptiert (keine neue angelegt)." % sid)
                registry[key] = {
                    "sid": sid,
                    "title": args.title,
                    "directory": directory,
                    "created_at": int(time.time()),
                    "adopted": True,
                }
                save_registry(registry)

        if sid is None:
            # Zweite Heilungsstufe: laeuft im selben Verzeichnis genau eine
            # Session mit offenem Formular, ist der neue Auftrag deren Antwort
            # (der Vater hat auf die Rueckfrage geantwortet). Dann muss DIESE
            # Session fortgefuehrt werden - niemals eine zweite angelegt werden.
            pending_sid, pending_form = adopt_pending_form(
                client, sessions, directory
            )
            if pending_sid:
                sid = pending_sid
                status = "adopted-form"
                log(
                    "Offenes Formular %s auf Session %s uebernommen - Antwort "
                    "wird dorthin geschrieben, keine neue Session."
                    % (pending_form, sid)
                )
                registry[key] = {
                    "sid": sid,
                    "title": args.title,
                    "directory": directory,
                    "created_at": int(time.time()),
                    "adopted": True,
                }
                save_registry(registry)

        if sid is None:
            if args.dry_run:
                emit(args, None, "would-create", "dry-run", args.title, directory)
                return 0
            sid = client.create_session(directory, args.title, wanted_model)
            status = "new"
            # Fail-closed: Modell verifizieren, BEVOR registriert oder gesendet wird.
            client.ensure_session_model(sid, wanted_model)
            # WICHTIG: sofort persistieren, BEVOR der Prompt gefeuert wird.
            # Bricht der Prozess danach ab, adoptiert der naechste Aufruf diese
            # Session, statt eine zweite anzulegen.
            registry[key] = {
                "sid": sid,
                "title": args.title,
                "directory": directory,
                "created_at": int(time.time()),
                "prompted_at": None,
            }
            save_registry(registry)
            log("Session %s angelegt und registriert (%s)." % (sid, key))

        if args.dry_run:
            emit(args, sid, status or "existing", "dry-run", args.title, directory)
            return 0

        if status != "new":
            # Bestehende Session (reused/adopted/explicit): Modell ebenfalls
            # verifizieren und bei Abweichung nativ nachziehen. Eine Session aus
            # einem frueheren Lauf kann sonst auf einem unbrauchbaren Modell sitzen.
            resolved = client.ensure_session_model(sid, wanted_model)
            log("Verifiziert: Session %s laeuft auf %s."
                % (sid, model_ref(resolved)))

        already_prompted = bool((registry.get(key) or {}).get("prompted_at"))
        if status == "explicit":
            # Fortsetzung: die Session hat natuerlich schon Nachrichten, sonst
            # waere gar kein Fortsetzen moeglich. Nur die Registry entscheidet,
            # ob genau dieser Auftragstext schon versandt wurde.
            pass
        elif not already_prompted and not args.force_prompt:
            # Registry koennte verloren sein (Prozessabbruch). Zweite Stufe:
            # fragt der Server, ob die Session schon eine User-Nachricht hat.
            if client.has_user_messages(sid):
                log("Session %s enthaelt bereits eine User-Nachricht - "
                    "kein erneuter Versand." % sid)
                already_prompted = True
        if already_prompted and not args.force_prompt:
            emit(args, sid, status or "reused", "skipped", args.title, directory)
            return 0

        prompt_text = text
        if args.force_prompt and already_prompted:
            prompt_text = (
                text
                + "\n\n(Fortsetzung: Der vorige Auftrag wurde bereits ausgefuehrt. "
                "Bitte nur die neue Anweisung bearbeiten.)"
            )
        try:
            client.fire_prompt(sid, prompt_text)
        except DispatchError as exc:
            log("Prompt nicht gesendet: %s" % exc)
            log("Die Session %s existiert. Erneut ausfuehren ist gefahrlos." % sid)
            emit(args, sid, status or "reused", "failed", args.title, directory)
            return 2

        entry = registry.setdefault(key, {})
        entry["sid"] = sid
        entry["prompted_at"] = int(time.time())
        save_registry(registry)

        emit(args, sid, status or "reused", "sent", args.title, directory)
        return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except DispatchError as exc:
        log("FEHLER: %s" % exc)
        sys.exit(3)
    except KeyboardInterrupt:
        log("Abgebrochen.")
        sys.exit(130)