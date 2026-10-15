#!/usr/bin/env python3
"""sync.py - fernuni-sync: Moodle-Kurse -> Obsidian-Vault (oCIS-Space).

Ablauf pro Kurs:
  1. Kursseite laden, Format erkennen (format_onetopic / format_tiles).
     Bei tiles wird jeder Abschnitt einzeln ueber /course/section.php?id=<sid>
     nachgeladen.
  2. Aktivitaeten sammeln. resource/folder werden heruntergeladen (wenn nicht
     zu gross und nicht schon im Vault), alles andere wird nur verlinkt.
  3. Abschnittsnotiz + Kursindex schreiben, Material in Material/<SS>-<Slug>/.
  4. Am Ende einen Block an 90-Meta/01-Sync-Log.md anhaengen.

Sicherheit/Idempotenz:
  * Secrets nur aus der Umgebung, niemals in Dateien oder Logs.
  * Notizen ohne 'generated: true' werden nie ueberschrieben.
  * Material wird ueber die State-Datei (Groesse/ETag) wiedererkannt.
  * Exit 0 = ok, 1 = Fehler bei --strict, 2 = Konfigurations-/Secret-Fehler.
"""
import argparse
import hashlib
import http.client
import json
import os
import re
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import moodle_parse as mp          # noqa: E402  (Parser beider Kursformate)
import moodle_session as ms        # noqa: E402  (Login/Opener)
import obsidian as ob              # noqa: E402  (Notizen/Slugs)
from dav import Dav, DavError      # noqa: E402  (oCIS-WebDAV)

RETRY_DELAYS = (1, 3, 9)
ENV_REQUIRED = ("MD_USER", "MD_PW", "MD_BASE",
                "FERNUNI_OCIS_USER", "FERNUNI_OCIS_TOKEN", "FERNUNI_SPACE_ID")
DOWNLOAD_TYPES = ("resource", "resource_pdf", "folder", "assign")
# Typen, deren Aktivitaetsseite zusaetzliche Infos traegt (Name, Anhaenge, Termin)
SEITEN_TYPEN = ("assign", "folder")
# Typen, fuer die bei fehlendem href die view.php aus der cmid gebaut wird
VIEW_TYPES = ("assign", "folder", "resource", "resource_pdf")
UA = mp.UA_DEFAULT


class FetchError(Exception):
    """Netz-/HTTP-Fehler beim Moodle-Zugriff."""


class AuthError(FetchError):
    """Die Moodle-Session ist nicht angemeldet (IdP-/Anmeldeseite statt Inhalt)."""


def pruefen_kursseite(html, cid):
    """Klarer Fehler statt stiller Leere, wenn Moodle keine Kursseite liefert."""
    if "/idp/profile/" in html or "j_username" in html or "Identity Provider" in html:
        raise AuthError(
            "Moodle liefert die SAML-Anmeldeseite von login.fernuni-hagen.de statt "
            "der Kursseite - die Session ist nicht angemeldet. Details: README, "
            "Abschnitt 'Bekanntes Problem: FernUni-SSO'.")
    if 'name="logintoken"' in html and "login/index.php" in html:
        raise AuthError("Moodle zeigt die Anmeldeseite - Login fehlgeschlagen "
                        "(Zugangsdaten/Auth-Pfad pruefen).")
    if not (mp.RE_ONETOPIC_SECTION.search(html) or mp.RE_TILE.search(html)):
        raise AuthError(f"Kurs {cid}: keine Abschnitte im HTML gefunden "
                        f"({len(html)} Bytes) - unbekanntes Format oder fehlende Rechte.")


# --------------------------------------------------------------------------- #
# kleine Helfer
# --------------------------------------------------------------------------- #
def join_path(*parts):
    """Vault-Pfad zusammensetzen, leere Teile und Doppel-Slashes vermeiden."""
    return "/".join(p.strip("/") for p in parts if p and p.strip("/"))


def norm_key(url):
    """Stabile Kennung fuer eine Moodle-Datei (ohne Query-Strings)."""
    if not url:
        return ""
    u = urllib.parse.urlsplit(url)
    return f"{u.scheme}://{u.netloc}{u.path}" if u.netloc else u.path


def abs_url(base, url):
    if not url:
        return None
    if url.startswith(("http://", "https://")):
        return url
    return urllib.parse.urljoin(base.rstrip("/") + "/", url.lstrip("/"))


def disp_filename(disp):
    """Dateiname aus Content-Disposition (RFC 5987 und Klassiker)."""
    if not disp:
        return None
    m = re.search(r"filename\*\s*=\s*([^']*)''([^;]+)", disp, re.I)
    if m:
        return urllib.parse.unquote(m.group(2).strip().strip('"'))
    m = re.search(r'filename\s*=\s*"?([^";]+)"?', disp, re.I)
    return m.group(1).strip() if m else None


def url_filename(url):
    seg = urllib.parse.unquote(urllib.parse.urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1])
    return seg if seg and "." in seg else None


def dateiname_waehlen(explicit, disp, final_url, ctype, fallback):
    """Dateinamen bestimmen - Namen mit Endung bevorzugt:
    Aktivitaets-/Ordner-Name -> Content-Disposition -> URL-Basename."""
    for cand in (explicit, disp, url_filename(final_url)):
        if cand and os.path.splitext(cand)[1]:
            return cand
    for cand in (explicit, disp):
        if cand:
            return cand + (".pdf" if ctype == "application/pdf" else "")
    return fallback


RE_DATEILINK = re.compile(r'(?:href|src)="([^"]*?/pluginfile\.php[^"]*)"')
RE_NICHT_DATEI = re.compile(r"\.(?:css|js|mjs|png|jpe?g|gif|svg|woff2?|ico)(?:\?|$)", re.I)


def activity_titles(html_fragments):
    """{cmid: data-title} - Labels haben oft keinen sichtbaren Namen."""
    out = {}
    for html in html_fragments:
        for m in re.finditer(r'<li[^>]*id="module-(\d+)"[^>]*>', html):
            tag = m.group(0)
            t = re.search(r'data-title="([^"]+)"', tag)
            if t and m.group(1) not in out:
                out[m.group(1)] = mp.clean(t.group(1))
    return out


def load_env_file(path):
    """KEY=VALUE-Datei einlesen (nur wenn --env-file gesetzt).

    Werte aus der Datei haben Vorrang: beim Shell-Sourcing (`set -a; . datei`)
    expandiert die Shell ein `$` in der Space-ID und zerstoert den Wert.
    """
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip().strip('"').strip("'")


def fmt_dauer(sek):
    return f"{sek:.1f}s" if sek < 60 else f"{sek / 60:.1f}min"


def ist_unveraendert(eintrag, size, etag):
    """Idempotenz-Regel: Eintrag aus dem State passt zu Groesse/ETag der Quelle."""
    if not eintrag or size is None:
        return False
    if eintrag.get("moodle_size") != size:
        return False
    alt = eintrag.get("etag")
    return not (alt and etag and alt != etag)


# --------------------------------------------------------------------------- #
# Moodle-Zugriff mit Retry
# --------------------------------------------------------------------------- #
class Moodle:
    def __init__(self, base, opener, verbose=False):
        self.base = base.rstrip("/")
        self.op = opener
        self.verbose = verbose

    def text(self, path_or_url):
        url = abs_url(self.base, path_or_url)
        last = None
        for delay in (0,) + RETRY_DELAYS:
            if delay:
                if self.verbose:
                    print(f"    .. Retry in {delay}s ({url})")
                time.sleep(delay)
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA})
                with self.op.open(req, timeout=60) as r:
                    return r.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as e:
                last = e
                if e.code in (423, 429) or 500 <= e.code < 600:
                    continue
                raise FetchError(f"{url}: HTTP {e.code}")
            except (urllib.error.URLError, socket.timeout,
                    http.client.HTTPException, ConnectionError) as e:
                last = e
                continue
        raise FetchError(f"{url}: fehlgeschlagen nach {len(RETRY_DELAYS) + 1} Versuchen "
                         f"({getattr(last, 'code', None) or type(last).__name__})")

    def stream(self, url):
        """GET mit Redirect-Folge; Rueckgabe des offenen Response-Objekts."""
        url = abs_url(self.base, url)
        last = None
        for delay in (0,) + RETRY_DELAYS:
            if delay:
                time.sleep(delay)
            try:
                req = urllib.request.Request(
                    url, headers={"User-Agent": UA, "Referer": self.base + "/"})
                return self.op.open(req, timeout=180)
            except urllib.error.HTTPError as e:
                last = e
                if e.code in (423, 429) or 500 <= e.code < 600:
                    continue
                raise FetchError(f"{url}: HTTP {e.code}")
            except (urllib.error.URLError, socket.timeout,
                    http.client.HTTPException, ConnectionError) as e:
                last = e
                continue
        raise FetchError(f"{url}: fehlgeschlagen nach {len(RETRY_DELAYS) + 1} Versuchen "
                         f"({getattr(last, 'code', None) or type(last).__name__})")

    def probe(self, url):
        """Nur Header lesen (Groesse/Ziel-URL), Body wird verworfen."""
        r = self.stream(url)
        try:
            cl = r.headers.get("Content-Length")
            return {"final": r.geturl(),
                    "size": int(cl) if cl and cl.isdigit() else None,
                    "etag": r.headers.get("ETag"),
                    "ctype": (r.headers.get("Content-Type") or "").split(";")[0].strip(),
                    "disp": r.headers.get("Content-Disposition") or ""}
        finally:
            r.close()

    def download(self, url, max_bytes, tmpdir):
        """Datei in eine temporaere Datei streamen. Rueckgabe dict mit Status."""
        r = self.stream(url)
        tmp = None
        try:
            cl = r.headers.get("Content-Length")
            size = int(cl) if cl and cl.isdigit() else None
            info = {"final": r.geturl(),
                    "ctype": (r.headers.get("Content-Type") or "").split(";")[0].strip(),
                    "disp": r.headers.get("Content-Disposition") or "",
                    "size": size}
            if size is not None and size > max_bytes:
                info["status"] = "zu_gross"
                return info
            h = hashlib.sha256()
            total = 0
            fd, tmp = tempfile.mkstemp(prefix="fernuni-", suffix=".part", dir=tmpdir)
            with os.fdopen(fd, "wb") as fh:
                while True:
                    chunk = r.read(65536)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        info.update(status="zu_gross", size=total)
                        fh.close()
                        os.unlink(tmp)
                        return info
                    h.update(chunk)
                    fh.write(chunk)
            info.update(status="ok", size=total, sha256=h.hexdigest(), path=tmp)
            return info
        finally:
            r.close()


# --------------------------------------------------------------------------- #
# State
# --------------------------------------------------------------------------- #
def load_state(path):
    if not os.path.exists(path):
        return {"version": 1, "material": {}, "notes": {}}
    try:
        with open(path, encoding="utf-8") as fh:
            st = json.load(fh)
    except (OSError, ValueError) as e:
        print(f"WARNUNG: State {path} nicht lesbar ({e}) - starte mit leerem State")
        return {"version": 1, "material": {}, "notes": {}}
    st.setdefault("material", {})
    st.setdefault("notes", {})
    return st


def save_state(path, state):
    """Atomar schreiben (tmp + os.replace)."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="state-", suffix=".json",
                               dir=os.path.dirname(os.path.abspath(path)))
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, path)


# --------------------------------------------------------------------------- #
# Kurs-Synchronisation
# --------------------------------------------------------------------------- #
class Kurs:
    def __init__(self, cfg, args, moodle, dav, state, tmpdir):
        self.cfg = cfg
        self.args = args
        self.moodle = moodle
        self.dav = dav
        self.state = state
        self.tmpdir = tmpdir
        self.max_bytes = int(cfg.get("max_file_mb", 100)) * 1024 * 1024
        self.base_dir = join_path(cfg.get("_vault_root", ""), cfg["base"], cfg["slug"], "Moodle")
        self.errors = []
        self.abteilung = []
        self.note_total = 0
        self.files_downloaded = 0
        self.files_skipped = 0
        self.files_too_big = 0
        self.bytes_downloaded = 0
        self.run_urls = {}

    # -- Laden ------------------------------------------------------------
    def _sections(self):
        html = self.moodle.text(f"/course/view.php?id={self.cfg['id']}")
        pruefen_kursseite(html, self.cfg["id"])
        parsed = mp.parse_course(html)
        self.title = parsed["title"] or self.cfg["titel"]
        self.format = parsed["format"]
        frags = [html]
        sections = parsed["sections"]
        if self.format == "onetopic":
            sections = self._onetopic_abschnitte(html, sections, frags)
        out = []
        for sec in sections:
            if sec.get("activities") is None:            # tiles-Format
                try:
                    body = self.moodle.text(f"/course/section.php?id={sec['id']}")
                except FetchError as e:
                    # Ein kaputter Abschnitt bricht den Kurs nicht ab.
                    self.errors.append(f"Abschnitt {sec['number']} ({sec['name']}): {e}")
                    print(f"    ! Abschnitt {sec['number']} nicht ladbar, wird uebersprungen: {e}")
                    continue
                frags.append(body)
                s2 = mp.parse_section_body(body, number=sec["number"], section_id=sec["id"])
                sec["activities"] = s2["activities"]
                sec["summary"] = s2.get("summary")
                if s2.get("name") and s2["name"] != self.title:
                    sec["name"] = s2["name"]
                if self.args.verbose:
                    print(f"    § {sec['number']:>2} {sec['name']!r} <- section.php?"
                          f"id={sec['id']} ({len(sec['activities'])} Akt.)")
            if self.args.limit_sections and len(out) >= self.args.limit_sections:
                break                                     # nichts weiter laden
            if self.args.limit_sections and not sec["activities"]:
                continue                                  # leere Abschnitte ueberspringen
            out.append(sec)
        self.titles = activity_titles(frags)              # cmid -> data-title
        return out

    def _onetopic_abschnitte(self, html, inline_secs, frags):
        """format_onetopic mit Tabs: jeden Abschnitt einzeln laden.

        Die Kursseite rendert immer nur den **aktiven** Abschnitt; alle uebrigen
        fehlen im HTML. Deshalb wird jeder Abschnitt der Tab-Navigation einzeln
        ueber ``/course/view.php?id=<cid>&section=<n>`` geholt. Die Zuordnung
        laeuft ueber die Tab-Reihenfolge und die ``data-id`` des geladenen
        Abschnitts - nicht ueber geratene IDs.
        """
        tabs = mp.parse_tabs(html)
        if len(tabs) < 2:
            return inline_secs
        cid = self.cfg["id"]
        ids = [sid for sid, _ in tabs]
        gefunden = {}
        for num, sid, body in mp.parse_onetopic_blocks(html):   # aktiver Abschnitt
            if num != 0 and sid in ids:
                gefunden[sid] = (num, body)
        # 1) &section=<nummer>: Nummern ab 1 durchprobieren, bis alle Tabs gefunden
        #    sind. Zugeordnet wird immer ueber die data-id des geladenen Abschnitts.
        fehlend = [sid for sid in ids if sid not in gefunden]
        num, misses = 1, 0
        while fehlend and misses <= len(tabs) + 3 and num <= len(tabs) + 20:
            try:
                teil = self.moodle.text(f"/course/view.php?id={cid}&section={num}")
            except FetchError as e:
                if self.args.verbose:
                    print(f"    .. section={num} liefert nichts ({e})")
                misses += 1
                num += 1
                continue
            neu = [b for b in mp.parse_onetopic_blocks(teil) if b[1] in fehlend]
            if neu:
                frags.append(teil)
                for n2, sid2, body2 in neu:
                    gefunden[sid2] = (n2, body2)
                fehlend = [sid for sid in ids if sid not in gefunden]
                misses = 0
            else:
                misses += 1
            num += 1
        # 2) Fallback &section=<sectionid> (andere Moodle-/Format-Semantik)
        for sid in list(fehlend):
            try:
                teil = self.moodle.text(f"/course/view.php?id={cid}&section={sid}")
            except FetchError as e:
                if self.args.verbose:
                    print(f"    .. section={sid} liefert nichts ({e})")
                continue
            treffer = [b for b in mp.parse_onetopic_blocks(teil) if b[1] == sid]
            if treffer:
                frags.append(teil)
                gefunden[sid] = (treffer[0][0], treffer[0][2])
        out = []
        for sid, tabname in tabs:
            num, body = gefunden.get(sid, (None, None))
            if body is None:
                self.errors.append(f"Abschnitt {tabname!r} (id={sid}): Inhalt nicht "
                                   f"ladbar (weder ueber section=<nummer> noch "
                                   f"section={sid})")
                print(f"    ! Abschnitt {tabname!r} (id={sid}) ohne Inhalt")
                num, body = len(out) + 1, ""
            sec = mp.parse_section_body(body, number=num, name=tabname, section_id=sid)
            if not sec["name"]:
                sec["name"] = tabname
            out.append(sec)
            if self.args.verbose:
                print(f"    \u00a7 {num:>2} {sec['name']!r} <- view.php?section={num} "
                      f"({len(sec['activities'])} Akt.)")
        return out

    # -- Material ---------------------------------------------------------
    def _note_exists(self, path):
        """(text, generiert?) der bestehenden Notiz bzw. (None, False)."""
        try:
            txt = self.dav.get_text(path)
        except DavError as e:
            self.errors.append(f"{path}: {e}")
            return None, False
        return txt, (ob.is_generated(txt) if txt else False)

    def _write_text(self, path, text, is_note):
        """Notiz/Index schreiben (dry-run: nur melden)."""
        if self.args.dry_run:
            print(f"    [dry-run] wuerde schreiben: {path} ({len(text)} B)")
            return
        self.dav.ensure_dir(os.path.dirname(path))
        self.dav.put(path, text.encode("utf-8"),
                     "text/markdown; charset=utf-8"
                     if is_note else "application/octet-stream")

    def _render_note(self, sec, slug):
        """Abschnittsnotiz erzeugen; beruecksichtigt Nutzerdateien."""
        note_path = join_path(self.base_dir, f"{slug}.md")
        target = note_path
        existing, gen = self._note_exists(note_path)
        if existing is not None and not gen:
            target = ob.generated_path(note_path)
            e2, g2 = self._note_exists(target)
            if e2 is not None and not g2:
                target = ob.generated_path(target)        # .generated-2.generated-2.md
            self.errors.append(f"WARNUNG: {note_path} ist keine generierte Notiz "
                               f"-> schreibe {target}")
            print(f"    ! Nutzerdatei erkannt, schreibe nach {target}")
            existing = e2 if (e2 is not None and g2) else existing
        created = None
        if existing:
            created = ob.parse_frontmatter(existing).get("created")
        created = created or ob.today()
        stamp = ob.now_stamp()

        body_lines = []
        dateien_lokal = 0
        letztes_label = None            # Doppelte Label-Texte nur einmal rendern
        for act in sec["activities"]:
            typ = act.get("type")
            name = ob.strip_activity_label(
                act.get("name") or self.titles.get(act["cmid"]), typ)
            url = self._modul_url(act)
            seite = None
            gesperrt = bool(act.get("availability"))    # Moodle gibt nichts heraus
            if url and typ in SEITEN_TYPEN and not gesperrt \
                    and (not name or typ == "assign"):
                seite = self._aktivitaetsseite(act, url)
                if not name and seite:
                    name = self._seitenname(seite)
            name = name or "(ohne Namen)"
            act = dict(act, name=name)
            if typ == "assign" and seite:
                termin = mp.assign_due(seite) or act.get("details")
                if termin:
                    act = dict(act, due=termin)
            if self.args.verbose:
                det = f"  [{act['details']}]" if act.get("details") else ""
                print(f"      - {str(typ or '?'):11s} {name}{det}")
            if typ == "helixmedia":
                body_lines.append(ob.activity_line(act, url))
                continue
            if typ == "label":
                zeilen, letztes_label = self._label_zeilen(act, letztes_label)
                body_lines += zeilen
                continue
            if typ in DOWNLOAD_TYPES and url and not self.args.no_material:
                try:
                    zeilen, n = self._material(sec, slug, act, url, seite)
                    body_lines += zeilen
                    dateien_lokal += n
                except (FetchError, DavError) as e:
                    self.errors.append(f"Abschnitt {sec['number']} / {name}: {e}")
                    print(f"    ! Fehler bei {name}: {e}")
                    body_lines.append(ob.activity_line(act, url))
                continue
            body_lines.append(ob.activity_line(act, url))

        source = (f"https://moodle.fernuni-hagen.de/course/section.php?id={sec['id']}"
                  if sec.get("id") else f"{self.moodle.base}/course/view.php?id={self.cfg['id']}")
        text = ob.section_note(self.cfg, sec, sec["activities"], None, sec.get("summary"),
                               source, created, stamp, join_path(self.base_dir, "Material", slug),
                               body_lines, course_title=self.title)
        if self.args.dry_run:
            print(f"    § {sec['number']:>2} {sec['name']!r}: {len(sec['activities'])} Aktivit\u00e4t(en), "
                  f"{dateien_lokal} Datei(en) lokal -> {target}")
        else:
            self._write_text(target, text, True)
            print(f"    § {sec['number']:>2} {sec['name']!r}: {len(sec['activities'])} Aktivit\u00e4t(en), "
                  f"{dateien_lokal} Datei(en) lokal")
        self.state["notes"][target] = {"created": created, "updated": stamp}
        self.note_total += 1
        return {"number": sec["number"], "name": sec["name"], "acts": len(sec["activities"]),
                "files": dateien_lokal, "note_path": target, "created": created}

    def _modul_url(self, act):
        """Link zur Aktivitaet - bei fehlendem href aus cmid + Modultyp gebaut.

        ``folder``/``assign`` haben in der Abschnittsliste teils keinen Link,
        ihre Inhalte stehen aber nur auf ``/mod/<typ>/view.php?id=<cmid>``.
        """
        url = abs_url(self.moodle.base, act.get("url") or "")
        if url:
            return url
        if act.get("type") in VIEW_TYPES and act.get("cmid"):
            return f"{self.moodle.base}/mod/{act['type']}/view.php?id={act['cmid']}"
        return None

    def _aktivitaetsseite(self, act, url):
        """HTML der view.php einer Aktivitaet (None, wenn sie nicht laedt)."""
        try:
            return self.moodle.text(url)
        except FetchError as e:
            self.errors.append(f"{act.get('name') or act['cmid']}: {e}")
            print(f"    ! Aktivitaetsseite nicht ladbar ({act['cmid']}): {e}")
            return None

    def _seitenname(self, html):
        """Aktivitaetsname von der view.php (Standardueberschriften/Kursname raus)."""
        for cand in mp.module_page_names(html):
            c = cand.strip().rstrip(":")
            if not c or c.lower() in mp.STANDARD_HEADINGS:
                continue
            if c in {self.title, self.cfg.get("titel")}:
                continue
            return c
        return None

    def _label_zeilen(self, act, vorher=None):
        """Label-Inhalt (Aufgabentext) als Markdown - leere/Dubletten entfallen.

        ``content_html`` ist rohes HTML (genau einmal unescaped);
        ``description`` ist schon Klartext und darf nicht erneut HTML-unescaped
        werden. Rueckgabe: (Zeilen fuer die Notiz, gerenderter Text).
        """
        html = act.get("content_html")
        md = (ob.html_to_markdown(html, self.moodle.base) if html
              else (act.get("description") or "").strip())
        if not md or md == vorher:
            if self.args.verbose:
                print(f"      * Label {act['cmid']}: "
                      f"{'Dublette, uebersprungen' if md else 'ohne Text, uebersprungen'}")
            return [], vorher
        if self.args.verbose:
            print(f"      * Label-Inhalt: {len(md)} Zeichen Markdown")
        return ob.label_block(md), md

    def _kandidaten(self, act, url, seite=None):
        """Datei-Kandidaten einer resource-/folder-/assign-Aktivitaet.

        Rueckgabe ``[(url, name|None, info|None)]``. ``assign`` haengt seine
        Dateien als ``introattachments`` an die view.php - sie stehen nicht in
        der Abschnittsliste.
        """
        if act["type"] == "assign":
            html = seite if seite is not None else self.moodle.text(url)
            out = [(abs_url(self.moodle.base, u), n, None)
                   for u, n in mp.parse_assign_attachments(html)]
            if not out and self.args.verbose:
                print(f"      * {act['name']!r}: keine assign-Anhaenge")
            return out
        if act["type"] == "folder":
            html = seite if seite is not None else self.moodle.text(url)
            out = [(abs_url(self.moodle.base, u), n, None)
                   for u, n in mp.parse_folder(html)]
            if out:
                if self.args.verbose:
                    print(f"      * Ordner {act['name']!r}: {len(out)} Datei(en)")
            else:
                print(f"      ! Ordner {act['name']!r} ohne Dateiliste")
            return out
        info = self.moodle.probe(url)
        if not (info.get("ctype") or "").startswith("text/html"):
            return [(info["final"], None, info)]          # Redirect direkt zur Datei
        html = self.moodle.text(url)
        links = [mp.ht.unescape(u) for u in RE_DATEILINK.findall(html)
                 if "/mod_" in u and "tiny_elements" not in u
                 and not RE_NICHT_DATEI.search(u)]
        bevorzugt = [u for u in links if "/mod_resource/" in u] or links
        if not bevorzugt:
            print(f"      ! {act['name']!r}: kein Dateilink auf der Moodle-Seite gefunden")
            return []
        return [(abs_url(self.moodle.base, bevorzugt[0]), None, None)]

    def _material(self, sec, slug, act, url, seite=None):
        """Dateien einer resource/folder/assign-Aktivitaet holen bzw. pruefen."""
        mat_dir = join_path(self.base_dir, "Material", slug)
        used = set()
        zeilen = []
        lokal = 0
        entries = []

        entries = self._kandidaten(act, url, seite)
        if not entries:
            zeilen.append(ob.activity_line(act, url))
            return zeilen, lokal

        for furl, prename, vorab in entries:
            key = norm_key(furl)
            if key in self.run_urls:                       # im Lauf schon geladen
                zeilen.append(ob.activity_line(act, url))
                continue
            if self.args.no_material:
                zeilen.append(ob.activity_line(act, url))
                continue
            try:
                info = vorab or self.moodle.probe(furl)
            except FetchError as e:
                self.errors.append(f"{act['name']}: {e}")
                zeilen.append(ob.activity_line(act, url))
                continue
            name = dateiname_waehlen(prename or act.get("name"), disp_filename(info["disp"]),
                                     info["final"], info["ctype"], f"datei-{act['cmid']}")
            dateiname = ob.safe_existing_filename(name, fallback=f"datei-{act['cmid']}")
            gr = info["size"]
            if gr is not None and gr > self.max_bytes:
                self.files_too_big += 1
                print(f"      ! zu gross ({ob.fmt_size(gr)}): {name} -> nur Link")
                self.errors.append(f"{name}: {ob.fmt_size(gr)} > Limit, nicht geladen")
                zeilen.append(ob.activity_line(act, url))
                continue

            ziel = join_path(mat_dir, dateiname)
            eintrag = self.state["material"].get(ziel)
            unveraendert = ist_unveraendert(eintrag, gr, info.get("etag"))
            if unveraendert and not self.args.dry_run:
                st, _, _ = self.dav.head(ziel)
                if st == 404:
                    unveraendert = False
                    print(f"      ! State sagt unveraendert, Vault-Datei fehlt: {ziel}")
            if unveraendert:
                self.files_skipped += 1
                self.run_urls[key] = ziel
                lokal += 1
                if self.args.verbose:
                    print(f"      = unveraendert, kein Download: {dateiname}")
                zeilen.append(ob.activity_line(
                    act, url, ob.fmt_size(gr),
                    ob.wikilink(ziel, label="Datei")))
                continue

            if self.args.dry_run:
                print(f"      [dry-run] wuerde laden: {name} ({ob.fmt_size(gr)}) -> {ziel}")
                zeilen.append(ob.activity_line(act, url, ob.fmt_size(gr)))
                continue

            if self.dav.exists(ziel) and ziel not in self.state["material"]:
                alt = ob.safe_existing_filename(
                    os.path.splitext(dateiname)[0] + "-generated-2" + os.path.splitext(dateiname)[1])
                neu = join_path(mat_dir, alt)
                self.errors.append(f"WARNUNG: {ziel} existiert, steht nicht im State "
                                   f"-> schreibe {neu}")
                print(f"      ! vorhandene Datei nicht im State -> {alt}")
                ziel = neu

            dl = self.moodle.download(furl, self.max_bytes, self.tmpdir)
            if dl["status"] != "ok":
                if os.path.exists(dl.get("path") or ""):
                    os.unlink(dl["path"])
                self.files_too_big += 1
                print(f"      ! zu gross: {name} ({ob.fmt_size(dl.get('size'))}) -> nur Link")
                self.errors.append(f"{name}: groesser als Limit, nicht geladen")
                zeilen.append(ob.activity_line(act, url))
                continue
            try:
                self.dav.ensure_dir(os.path.dirname(ziel))
                with open(dl["path"], "rb") as fh:
                    self.dav.put(ziel, fh.read(), dl["ctype"] or "application/octet-stream")
            finally:
                if os.path.exists(dl["path"]):
                    os.unlink(dl["path"])
            self.state["material"][ziel] = {
                "source": key, "moodle_size": dl["size"], "etag": info.get("etag"),
                "sha256": dl["sha256"], "updated": ob.now_stamp(),
            }
            self.files_downloaded += 1
            self.bytes_downloaded += dl["size"] or 0
            self.run_urls[key] = ziel
            lokal += 1
            print(f"      + {dateiname} ({ob.fmt_size(dl['size'])})")
            zeilen.append(ob.activity_line(act, url, ob.fmt_size(dl["size"]),
                                           ob.wikilink(ziel, label="Datei")))
        return zeilen, lokal

    # -- Index ------------------------------------------------------------
    def _index(self, rows, stamp):
        path = join_path(self.base_dir, "_Moodle-Index.md")
        existing, gen = self._note_exists(path)
        target = path
        if existing is not None and not gen:
            target = ob.generated_path(path)
            self.errors.append(f"WARNUNG: {path} ist keine generierte Notiz -> schreibe {target}")
            print(f"    ! Nutzerdatei erkannt, schreibe nach {target}")
        acts = sum(r["acts"] for r in rows)
        files = sum(r["files"] for r in rows)
        text = ob.index_note(self.cfg, self.title, rows, stamp, acts, files,
                             join_path(self.base_dir, "Material"))
        if self.args.dry_run:
            print(f"    [dry-run] wuerde schreiben: {target} ({len(text)} B)")
        else:
            self._write_text(target, text, True)
        self.state["notes"][target] = {"created": rows[0]["created"] if rows else ob.today(),
                                       "updated": stamp}
        return target

    # -- Lauf -------------------------------------------------------------
    def run(self):
        t0 = time.time()
        print(f"Kurs {self.cfg['id']} \u00b7 {self.cfg['titel']} "
              f"-> {join_path(self.cfg['base'], self.cfg['slug'])}")
        sections = self._sections()
        print(f"  Format {self.format}, {len(sections)} Abschnitt(e) "
              f"({self.args.limit_sections or 'alle'} verarbeitet)")
        stamp = ob.now_stamp()
        rows = []
        for sec in sections:
            if not sec.get("name"):
                sec["name"] = f"Abschnitt {sec['number']}"
            slug = ob.section_slug(sec["number"], sec["name"])
            try:
                rows.append(self._render_note(sec, slug))
            except (FetchError, DavError) as e:
                self.errors.append(f"Abschnitt {sec['number']} {sec['name']!r}: {e}")
                print(f"    ! Abschnitt {sec['number']} uebersprungen: {e}")
        index = None
        if rows:
            try:
                index = self._index(rows, stamp)
            except (FetchError, DavError) as e:
                self.errors.append(f"Index: {e}")
                print(f"    ! Index nicht geschrieben: {e}")
        return {"title": self.title, "format": self.format, "abschnitte": len(rows),
                "notizen": self.note_total, "dateien": self.files_downloaded,
                "uebersprungen": self.files_skipped, "zu_gross": self.files_too_big,
                "fehler": self.errors, "bytes": self.bytes_downloaded,
                "dauer": time.time() - t0, "index": index, "dav_calls": self.dav.calls}


# --------------------------------------------------------------------------- #
# Sync-Log
# --------------------------------------------------------------------------- #
def append_log(dav, args, statistik, gesamt_sek, fehler, kursinfos):
    tag = datetime.now().strftime("%Y-%m-%d")
    zeit = datetime.now().strftime("%H:%M:%S")
    zeilen = [f"", f"---", f"",
              f"## {tag} {zeit} \u00b7 fernuni-sync"
              + ("  (dry-run, nicht geschrieben)" if args.dry_run else ""),
              f"",
              "| Kurs | Abschnitte | Dateien geladen | \u00fcbersprungen | zu gro\u00df | Fehler |",
              "|------|-----------|-----------------|---------------|--------|--------|"]
    for kid, s in statistik.items():
        zeilen.append(f"| {kid} ({s['title']}) | {s['abschnitte']} | {s['dateien']} | "
                      f"{s['uebersprungen']} | {s['zu_gross']} | {len(s['fehler'])} |")
    zeilen.append("")
    zeilen.append(f"Gesamtdauer: {fmt_dauer(gesamt_sek)} \u00b7 "
                  f"Modus: {'dry-run' if args.dry_run else 'echt'}"
                  + (f" \u00b7 Limit: {args.limit_sections} Abschnitt(e)"
                     if args.limit_sections else ""))
    if fehler:
        zeilen.append("")
        zeilen.append("Fehler/Warnungen:")
        zeilen += [f"- {f}" for f in fehler[:50]]
        if len(fehler) > 50:
            zeilen.append(f"- ... {len(fehler) - 50} weitere")
    block = "\n".join(zeilen) + "\n"

    path = "90-Meta/01-Sync-Log.md"
    if args.dry_run:
        print(f"[dry-run] Sync-Log {path} nicht geaendert "
              f"({len(block)} B wuerden angehaengt)")
        return
    try:
        alt = dav.get_text(path)
    except DavError as e:
        print(f"WARNUNG: Sync-Log nicht lesbar ({e}) - wird neu angelegt")
        alt = None
    neu = (alt if alt is not None else "# Sync-Log\n") + block
    dav.ensure_dir("90-Meta")
    dav.put(path, neu.encode("utf-8"), "text/markdown; charset=utf-8")
    print(f"Sync-Log: {path} +{len(block)} B")


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def parse_args(argv):
    here = os.path.dirname(os.path.abspath(__file__))
    p = argparse.ArgumentParser(
        prog="sync.py", add_help=True,
        description="fernuni-sync: Moodle-Kurse als Obsidian-Notizen + Material in den "
                    "oCIS-Space spiegeln (nur Python-Standardbibliothek).",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default=os.path.join(here, "config.json"),
                   help="Pfad zur config.json (Default: neben sync.py)")
    p.add_argument("--only", action="append", metavar="KURSID",
                   help="nur diesen Kurs verarbeiten (mehrfach angebbar)")
    p.add_argument("--dry-run", action="store_true",
                   help="nichts schreiben, nichts herunterladen - nur Report")
    p.add_argument("--limit-sections", type=int, metavar="N",
                   help="nur die ersten N Abschnitte mit Inhalt je Kurs")
    p.add_argument("--no-material", action="store_true",
                   help="nur Notizen, keine Dateien aus Moodle laden")
    p.add_argument("--strict", action="store_true",
                   help="Exit 1, wenn Fehler aufgetreten sind")
    p.add_argument("--verbose", action="store_true", help="jede Aktivitaet anzeigen")
    p.add_argument("--env-file", metavar="PATH", default=None,
                   help="optional: KEY=VALUE-Datei laden (nur wenn alle Variablen "
                        "sonst fehlen); wird nie geloggt")
    return p.parse_args(argv)


def main(argv):
    t0 = time.time()
    args = parse_args(argv)
    if args.limit_sections is not None and args.limit_sections < 1:
        print("FEHLER: --limit-sections muss >= 1 sein", file=sys.stderr)
        return 2

    try:
        with open(args.config, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError) as e:
        print(f"FEHLER: config nicht lesbar: {args.config} ({e})", file=sys.stderr)
        return 2

    fehlend = [k for k in ENV_REQUIRED if not os.environ.get(k)]
    if args.env_file:
        load_env_file(args.env_file)
        fehlend = [k for k in ENV_REQUIRED if not os.environ.get(k)]
    if fehlend:
        print("FEHLER: fehlende Umgebungsvariablen: " + ", ".join(fehlend),
              file=sys.stderr)
        print("Hinweis: set -a; . <env-datei>; set +a  (Secrets nie ins Repo)",
              file=sys.stderr)
        return 2
    if "$" not in os.environ["FERNUNI_SPACE_ID"]:
        print("WARNUNG: FERNUNI_SPACE_ID enthaelt kein '$' - beim Shell-Sourcing "
              "kann die Shell ein '$...' im Wert expandiert (zerstoert) haben. "
              "Abhilfe: --env-file nutzen oder '\\$' maskieren.", file=sys.stderr)

    kurse = [c for c in cfg.get("courses", []) if c.get("enabled", True)]
    if args.only:
        bekannt = {c["id"] for c in cfg.get("courses", [])}
        unbekannt = [i for i in args.only if i not in bekannt]
        if unbekannt:
            print(f"FEHLER: unbekannte Kurs-ID(s): {', '.join(unbekannt)}", file=sys.stderr)
            return 2
        kurse = [c for c in kurse if c["id"] in args.only]
    if not kurse:
        print("FEHLER: kein Kurs ausgewaehlt", file=sys.stderr)
        return 2

    state_path = os.path.expanduser(cfg.get("state_file")
                                    or "~/.local/state/fernuni-sync/state.json")
    state = load_state(state_path)
    dav = Dav(verbose=args.verbose, dry_run=args.dry_run)
    print(f"fernuni-sync \u00b7 {datetime.now():%Y-%m-%d %H:%M:%S}"
          + (" \u00b7 DRY-RUN (keine Schreibzugriffe)" if args.dry_run else "")
          + f" \u00b7 State: {state_path}")

    try:
        opener = ms.login(os.environ["MD_USER"], os.environ["MD_PW"])
    except Exception as e:                                   # noqa: BLE001
        print(f"FEHLER: Moodle-Login fehlgeschlagen: {type(e).__name__}: {e}",
              file=sys.stderr)
        return 2
    moodle = Moodle(os.environ["MD_BASE"], opener, verbose=args.verbose)

    tmpdir = tempfile.mkdtemp(prefix="fernuni-sync-")
    alle_fehler, statistik, kursinfos = [], {}, []
    auth_fehler = None
    for c in kurse:
        c = dict(c)
        c["_vault_root"] = cfg.get("vault_root", "")
        k = Kurs(c, args, moodle, dav, state, tmpdir)
        try:
            s = k.run()
        except AuthError as e:
            print(f"  ! Kurs {c['id']}: {e}")
            auth_fehler = auth_fehler or str(e)
            s = {"title": c.get("titel", c["id"]), "format": "-", "abschnitte": 0,
                 "notizen": 0, "dateien": 0, "uebersprungen": 0, "zu_gross": 0,
                 "fehler": [str(e)], "bytes": 0, "dauer": 0.0, "index": None,
                 "dav_calls": dav.calls}
        except (FetchError, DavError) as e:
            print(f"  ! Kurs {c['id']} abgebrochen: {e}")
            s = {"title": c.get("titel", c["id"]), "format": "-", "abschnitte": 0,
                 "notizen": 0, "dateien": 0, "uebersprungen": 0, "zu_gross": 0,
                 "fehler": [str(e)], "bytes": 0, "dauer": 0.0, "index": None,
                 "dav_calls": dav.calls}
        except KeyboardInterrupt:
            print("\nAbbruch durch Benutzer")
            break
        statistik[c["id"]] = s
        alle_fehler += [f"{c['id']}: {f}" for f in s["fehler"]]
        kursinfos.append((c, s))
        if not args.dry_run:
            try:
                save_state(state_path, state)
            except OSError as e:
                alle_fehler.append(f"State konnte nicht geschrieben werden: {e}")
                print(f"WARNUNG: State nicht gespeichert ({e})")

    for c, s in kursinfos:
        print(f"  {c['id']}: {s['abschnitte']} Abschnitt(e), {s['notizen']} Notiz(en), "
              f"{s['dateien']} Datei(en) geladen, {s['uebersprungen']} unveraendert, "
              f"{s['zu_gross']} zu gross, {len(s['fehler'])} Fehler, "
              f"{ob.fmt_size(s['bytes'])}, {fmt_dauer(s['dauer'])}")

    if alle_fehler:
        print("Fehler/Warnungen:")
        for f in alle_fehler[:40]:
            print(f"  - {f}")
        if len(alle_fehler) > 40:
            print(f"  - ... {len(alle_fehler) - 40} weitere")

    if not args.dry_run:
        try:
            append_log(dav, args, statistik, time.time() - t0, alle_fehler, kursinfos)
        except DavError as e:
            print(f"WARNUNG: Sync-Log nicht geschrieben: {e}")
            alle_fehler.append(f"Sync-Log: {e}")

    print(f"fertig in {fmt_dauer(time.time() - t0)} \u00b7 "
          f"{sum(s['dateien'] for s in statistik.values())} Download(s), "
          f"{sum(s['uebersprungen'] for s in statistik.values())} unveraendert, "
          f"{len(alle_fehler)} Fehler/Warnung(en), {dav.calls} DAV-Aufrufe")
    if auth_fehler:
        print(f"FEHLER: Moodle-Session nicht angemeldet - {auth_fehler}", file=sys.stderr)
        print("Es wurde nichts geschrieben.", file=sys.stderr)
        return 2
    if allen := alle_fehler:
        print(f"Ergebnis: teilweise erfolgreich ({len(allen)} Fehler/Warnung(en))")
    else:
        print("Ergebnis: ok")
    if alle_fehler and args.strict:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
