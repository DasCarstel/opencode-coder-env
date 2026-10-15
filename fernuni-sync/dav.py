#!/usr/bin/env python3
"""dav.py - WebDAV-Client fuer den oCIS-Space (OpenCloud).

Nutzt ausschliesslich die Standardbibliothek. Zugangsdaten kommen aus der
Umgebung (FERNUNI_OCIS_USER, FERNUNI_OCIS_TOKEN, FERNUNI_SPACE_ID) - sie
werden nie geloggt oder in Dateien geschrieben.

Merkmale:
  * Browser-User-Agent ist Pflicht, sonst blockt Cloudflare mit Error 1010.
  * 3 Versuche pro Request mit Backoff 1s/3s/9s.
  * Vault-Pfade werden mit urllib.parse.quote URL-kodiert (Umlaute, Leerzeichen).
  * Basis-Pfad wird einmal ermittelt: ``/dav/spaces/<SPACE_ID>/``; antwortet der
    Space mit 404 (z. B. nicht sichtbar), wird auf ``/dav/files/<user>/``
    zurueckgefallen und ein Hinweis ausgegeben.
  * Fehler kommen als DavError mit sprechender Meldung (ohne Zugangsdaten).
"""
import base64
import http.client
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120 Safari/537.36")

DAV_BASE = os.environ.get("FERNUNI_OCIS_BASE", "https://opencloud.mueller-nas.de").rstrip("/")
RETRY_DELAYS = (1, 3, 9)          # len+1 == Anzahl Versuche
NS = "{DAV:}"
PROPFIND_BODY = (b'<?xml version="1.0" encoding="utf-8"?>'
                 b'<d:propfind xmlns:d="DAV:"><d:prop>'
                 b"<d:resourcetype/><d:getcontentlength/><d:getlastmodified/>"
                 b"<d:getetag/></d:prop></d:propfind>")


class DavError(Exception):
    """Fehler beim WebDAV-Zugriff (Netz, Auth, Statuscode)."""


def _join_encoded(path):
    """Vault-Pfad (Umlaute/Leerzeichen) URL-kodieren - Segment fuer Segment."""
    return "/".join(urllib.parse.quote(s, safe="") for s in path.strip("/").split("/") if s != "")


class Dav:
    """Minimaler WebDAV-Client: PUT, GET, MKCOL, PROPFIND, HEAD."""

    def __init__(self, base=DAV_BASE, space=None, user=None, token=None,
                 dry_run=False, verbose=False):
        self.base = (base or DAV_BASE).rstrip("/")
        self.space = space if space is not None else os.environ.get("FERNUNI_SPACE_ID")
        self.user = user if user is not None else os.environ.get("FERNUNI_OCIS_USER")
        self.token = token if token is not None else os.environ.get("FERNUNI_OCIS_TOKEN")
        self.dry_run = dry_run
        self.verbose = verbose
        self.calls = 0
        if not self.space or not self.user or not self.token:
            raise DavError("oCIS-Zugangsdaten fehlen (FERNUNI_SPACE_ID, "
                           "FERNUNI_OCIS_USER, FERNUNI_OCIS_TOKEN)")
        self._auth = "Basic " + base64.b64encode(
            f"{self.user}:{self.token}".encode("utf-8")).decode("ascii")
        self._base_path = None
        self.basis_quelle = None

    # -- Basis-Pfad ---------------------------------------------------------
    def _spaces_path(self):
        # '!' und '$' sind in Pfaden gueltige Sub-Delims und bleiben unkodiert.
        return f"{self.base}/dav/spaces/{urllib.parse.quote(self.space, safe='$!')}"

    def _files_path(self):
        return f"{self.base}/dav/files/{urllib.parse.quote(self.user, safe='')}"

    def base_path(self):
        """Basis-URL (ohne Slash) - einmalig ermittelt und gecacht."""
        if self._base_path is None:
            self._base_path = self._resolve_base()
        return self._base_path

    def _resolve_base(self):
        spaces, files = self._spaces_path(), self._files_path()
        if self.dry_run:
            return spaces                      # dry-run: keine Extra-Requests
        for cand in (spaces, files):
            try:
                st, _ = self._http(cand + "/", "PROPFIND", PROPFIND_BODY,
                                   {"Depth": "0", "Content-Type": "application/xml"}, (207,))
            except DavError:
                continue
            if st == 207:
                if cand == files:
                    print(f"Hinweis: WebDAV-Space {self.space} nicht erreichbar (404) - "
                          f"nutze persoenlichen Space /dav/files/{self.user}")
                    print("        Tipp: enthaelt die Space-ID ein '$', muss sie beim "
                          "Shell-Sourcing gequotet werden (--env-file nutzen oder \\$ maskieren).")
                self.basis_quelle = "spaces" if cand == spaces else "files"
                return cand
        print(f"WARNUNG: kein WebDAV-Basis-Pfad bestaetigt, versuche {spaces}")
        return spaces

    def url(self, path):
        return self.base_path() + "/" + _join_encoded(path)

    def space_url(self):
        return self.base_path() + "/"

    # -- Request mit Retry --------------------------------------------------
    def _http(self, url, method="PROPFIND", data=None, headers=None,
              expect=(200, 201, 204, 207)):
        hdrs = {
            "Authorization": self._auth,
            "User-Agent": UA,
            "Accept": "*/*",
            # oCIS verlangt Content-Type fuer PUT; Markdown ist der Normalfall.
            "Content-Type": "text/markdown; charset=utf-8",
        }
        if headers:
            hdrs.update(headers)
        last = None
        for delay in (0,) + RETRY_DELAYS:
            if delay:
                time.sleep(delay)
            self.calls += 1
            req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    body = r.read() if method in ("GET", "PROPFIND") else b""
                    if expect and r.status not in expect:
                        raise DavError(f"{method} {url}: unerwarteter Status {r.status}")
                    return r.status, body
            except urllib.error.HTTPError as e:
                last = e
                # 4xx (ausser 423/429) sind fachliche Antworten - kein Retry.
                if e.code in (423, 429) or 500 <= e.code < 600:
                    continue
                return e.code, (e.read() if method in ("GET", "PROPFIND") else b"")
            except (urllib.error.URLError, socket.timeout, http.client.HTTPException,
                    ConnectionError) as e:
                last = e
                continue
        detail = getattr(last, "code", None) or type(last).__name__
        raise DavError(f"{method} {url}: fehlgeschlagen nach "
                       f"{len(RETRY_DELAYS) + 1} Versuchen ({detail})")

    def request(self, path, method="PROPFIND", data=None, headers=None,
                expect=(200, 201, 204, 207), raw_url=None):
        return self._http(raw_url or self.url(path), method, data, headers, expect)

    # -- Operationen --------------------------------------------------------
    def mkcol(self, path):
        """Ordner anlegen. 201 = neu, 405 = existiert schon."""
        if self.dry_run:
            return 405
        st, _ = self.request(path, "MKCOL")
        if st not in (201, 405):
            raise DavError(f"MKCOL {path}: Status {st}")
        return st

    def ensure_dir(self, path):
        """Alle Elternordner eines Pfades anlegen (idempotent)."""
        parts = path.strip("/").split("/")
        for i in range(1, len(parts) + 1):
            self.mkcol("/".join(parts[:i]))

    def put(self, path, data, content_type="application/octet-stream"):
        if self.dry_run:
            if self.verbose:
                print(f"  [dry-run] PUT {path} ({len(data)} Bytes)")
            return 200
        st, _ = self.request(path, "PUT", data=data,
                             headers={"Content-Type": content_type})
        if st not in (200, 201, 204):
            raise DavError(f"PUT {path}: Status {st}")
        return st

    def get(self, path):
        """Datei lesen. Rueckgabe (status, bytes); 404 ist kein Fehler."""
        return self.request(path, "GET")

    def get_text(self, path):
        st, body = self.get(path)
        if st == 404:
            return None
        if st >= 400:
            raise DavError(f"GET {path}: Status {st}")
        return body.decode("utf-8", "replace").lstrip("\ufeff")

    def head(self, path, raw_url=None):
        """(status, content-length|None, etag|None). 404 = nicht vorhanden."""
        url = raw_url or self.url(path)
        hdrs = {"Authorization": self._auth, "User-Agent": UA}
        last = None
        for attempt, delay in enumerate((0,) + RETRY_DELAYS):
            if delay:
                time.sleep(delay)
            self.calls += 1
            req = urllib.request.Request(url, method="HEAD", headers=hdrs)
            try:
                with urllib.request.urlopen(req, timeout=45) as r:
                    cl = r.headers.get("Content-Length")
                    return (r.status,
                            int(cl) if cl and cl.isdigit() else None,
                            r.headers.get("ETag"))
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return 404, None, None
                last = e
                if e.code in (423, 429) or 500 <= e.code < 600:
                    continue
                return e.code, None, None
            except (urllib.error.URLError, socket.timeout, http.client.HTTPException,
                    ConnectionError) as e:
                last = e
                continue
        raise DavError(f"HEAD {url}: fehlgeschlagen ({type(last).__name__})")

    def propfind(self, path=""):
        """PROPFIND Depth 1 - Rueckgabe [(href, {propname: wert})]."""
        url = self.url(path) if path else self.space_url()
        st, raw = self.request(None, "PROPFIND", data=PROPFIND_BODY,
                               headers={"Depth": "1",
                                        "Content-Type": "application/xml; charset=utf-8"},
                               expect=(207,), raw_url=url)
        if st != 207:
            raise DavError(f"PROPFIND {path or '/'}: Status {st}")
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as e:
            raise DavError(f"PROPFIND {path or '/'}: XML nicht lesbar ({e})")
        out = []
        for resp in root.findall(f"{NS}response"):
            href = resp.findtext(f"{NS}href") or ""
            props = {}
            for ps in resp.findall(f"{NS}propstat"):
                ok = ps.findtext(f"{NS}status") or ""
                if " 200 " not in ok:
                    continue
                prop = ps.find(f"{NS}prop")
                if prop is None:
                    continue
                for child in prop:
                    name = child.tag.replace(NS, "")
                    if len(child) and name == "resourcetype":
                        props["collection"] = child.find(f"{NS}collection") is not None
                    else:
                        props[name] = (child.text or "").strip()
            out.append((urllib.parse.unquote(href), props))
        return out

    def exists(self, path):
        st, _, _ = self.head(path)
        return st != 404

    def delete(self, path):
        """Datei/Ordner loeschen. 404 = war schon weg."""
        if self.dry_run:
            return 404
        st, _ = self.request(path, "DELETE")
        if st not in (200, 204, 404):
            raise DavError(f"DELETE {path}: Status {st}")
        return st
