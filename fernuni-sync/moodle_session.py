#!/usr/bin/env python3
"""moodle_session.py - Login-Helfer fuer die FernUni-Moodle-Instanz.

Primaerweg ist das **lokale Loginformular** der Moodle-Instanz:
  1. ``GET /``            -> ``logintoken`` aus dem Formular holen
  2. ``POST /login/index.php`` mit ``username``, ``password``, ``logintoken``
  3. Pruefen: ``GET /my/`` muss eine angemeldete Moodle-Seite liefern
     (kein ``j_username``-Feld, keine IdP-URL, kein ``logintoken``-Formular).

Erst wenn dieser lokale Weg **keine** Session ergibt, laeuft als Fallback der
Shibboleth-Formularfluss ueber ``login.fernuni-hagen.de`` (IdP). Dieser Fallback
ist gegen die echte IdP-Konfiguration **nicht verifiziert** - mit gueltigen
Zugangsdaten greift immer der lokale Weg.

Wichtig: In den env-Dateien muss ein ``$`` in ``MD_PW``/``FERNUNI_SPACE_ID``
vor der Shell geschuetzt sein (einfache Quotes oder ``\\$``). Wird es
expandiert, schickt Moodle die (dann falschen) Zugangsdaten in den SSO-Fluss
und antwortet mit einer IdP-Seite statt einer Kursseite. Genau das sah wie ein
SSO-Zwang aus.
"""
import http.cookiejar
import html as ht
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120 Safari/537.36")
BASE = os.environ.get("MD_BASE", "https://moodle.fernuni-hagen.de").rstrip("/")
MAX_SSO_STEPS = 12
RE_TITLE = re.compile(r"<title>(.*?)</title>", re.S | re.I)


def debug(*a):
    if os.environ.get("MD_DEBUG"):
        print("[login]", *a, file=sys.stderr)


def title_of(html):
    m = RE_TITLE.search(html or "")
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else "?"


def _new_opener():
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj),
                                     urllib.request.HTTPRedirectHandler())
    op.addheaders = [("User-Agent", UA)]
    return op


def _open(op, url, data=None, referer=None):
    hdrs = {"User-Agent": UA}
    if data is not None:
        hdrs["Content-Type"] = "application/x-www-form-urlencoded"
    if referer:
        hdrs["Referer"] = referer
    return op.open(urllib.request.Request(url, data=data, headers=hdrs), timeout=60)


def angemeldet(url, html):
    """True, wenn die Seite eine angemeldete Moodle-Seite ist."""
    if "/idp/profile" in (url or "") or "j_username" in (html or ""):
        return False
    if 'name="logintoken"' in (html or ""):
        return False                       # Anmeldeformular
    return True


# --------------------------------------------------------------------------- #
# SSO-Fallback (nur wenn der lokale Login keine Session ergibt)
# --------------------------------------------------------------------------- #
def parse_forms(html):
    """Alle POST-Formulare als {action, method, fields}."""
    forms = []
    for m in re.finditer(r"<form\b([^>]*)>(.*?)</form>", html or "", re.S | re.I):
        attrs, body = m.group(1), m.group(2)
        method = ((re.search(r'method="([^"]*)"', attrs) or [None, "get"])[1] or "get").lower()
        if method != "post":
            continue
        action = (re.search(r'action="([^"]*)"', attrs) or [None, ""])[1]
        fields = {}
        for i in re.finditer(r"<input\b([^>]*)>", body, re.I):
            a = i.group(1)
            name = (re.search(r'name="([^"]*)"', a) or [None, None])[1]
            if not name:
                continue
            typ = ((re.search(r'type="([^"]*)"', a) or [None, "text"])[1] or "text").lower()
            val = (re.search(r'value="([^"]*)"', a) or [None, ""])[1]
            if typ in ("checkbox", "radio") and "checked" not in a.lower():
                continue
            fields[ht.unescape(name)] = ht.unescape(val)
        for b in re.finditer(r"<button\b([^>]*)>", body, re.I):
            a = b.group(1)
            name = (re.search(r'name="([^"]*)"', a) or [None, None])[1]
            if name and name not in fields:
                val = (re.search(r'value="([^"]*)"', a) or [None, ""])[1]
                fields[ht.unescape(name)] = ht.unescape(val)
        forms.append({"action": ht.unescape(action), "method": method, "fields": fields,
                      "has_pw": "j_password" in fields or "password" in fields})
    return forms


def pick_form(html, user, pw):
    """Passendes IdP/SAML-Formular fuellen: (action, daten) oder None."""
    forms = parse_forms(html)
    cred = next((f for f in forms if f["has_pw"]), None)
    saml = next((f for f in forms if "SAMLResponse" in f["fields"]), None)
    proceed = next((f for f in forms if any(k.startswith("_eventId") for k in f["fields"])), None)
    f = saml or cred or proceed
    if not f:
        return None
    data = dict(f["fields"])
    if f is cred:
        for k in list(data):
            if k in ("j_username", "username", "user"):
                data[k] = user
            elif k in ("j_password", "password", "pass"):
                data[k] = pw
        for k in list(data):
            if k.startswith("_eventId"):
                data[k] = data[k] or "login"
    return f["action"], data


def _sso_fallback(op, user, pw, html, url):
    """IdP-Formularfluss; Rueckgabe (html, url) der letzten Antwort."""
    for step in range(MAX_SSO_STEPS):
        nxt = pick_form(html, user, pw)
        if not nxt:
            raise RuntimeError(f"kein bekanntes Formular auf der SSO-Seite: {url}")
        action, fields = nxt
        target = urllib.parse.urljoin(url, action)
        debug(f"SSO Schritt {step + 1}: POST {target} "
              f"({', '.join(k for k in fields if 'pass' not in k.lower())})")
        r = _open(op, target, urllib.parse.urlencode(fields).encode(), referer=url)
        html, url = r.read().decode("utf-8", "replace"), r.geturl()
        if angemeldet(url, html):
            debug("SSO-Fallback lieferte eine Moodle-Seite:", url)
            return html, url
    raise RuntimeError(f"SSO-Fallback nach {MAX_SSO_STEPS} Schritten ohne Ergebnis "
                       f"(letzte Station: {url})")


# --------------------------------------------------------------------------- #
def login(user, pw):
    """Liefert einen urllib-Opener mit gueltiger MoodleSession."""
    op = _new_opener()

    # --- primaer: lokales Loginformular -----------------------------------
    home = _open(op, BASE + "/").read().decode("utf-8", "replace")
    m = re.search(r'name="logintoken"\s+value="([^"]*)"', home)
    if not m:
        raise RuntimeError("kein logintoken auf der Moodle-Startseite gefunden")
    data = urllib.parse.urlencode({"username": user, "password": pw,
                                   "logintoken": m.group(1)}).encode()
    r = _open(op, BASE + "/login/index.php", data, referer=BASE + "/")
    html, url = r.read().decode("utf-8", "replace"), r.geturl()
    debug("nach lokalem Login:", url, "|", title_of(html))

    check = _open(op, BASE + "/my/")
    cbody, curl = check.read().decode("utf-8", "replace"), check.geturl()
    if angemeldet(curl, cbody):
        debug("lokaler Login ok")
        return op

    # --- Fallback: IdP/Shibboleth-Formularfluss ---------------------------
    debug("lokaler Login ergab keine Session -> SSO-Fallback")
    html, url = _sso_fallback(op, user, pw, html, url)
    check = _open(op, BASE + "/my/")
    cbody, curl = check.read().decode("utf-8", "replace"), check.geturl()
    if angemeldet(curl, cbody):
        debug("SSO-Login ok")
        return op
    raise RuntimeError(
        "Login fehlgeschlagen - weder lokales Formular noch SSO-Fallback haben eine "
        f"Session ergeben (Endstation: {curl}, {title_of(cbody)}). Zugangsdaten pruefen; "
        "Achtung: ein '$' in MD_PW muss in der env-Datei gequotet sein.")


def get(op, path):
    """HTML einer Moodle-Seite (Pfad relativ zu MD_BASE oder absolute URL)."""
    url = path if path.startswith("http") else BASE + path
    with op.open(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60) as r:
        return r.read().decode("utf-8", "replace")


if __name__ == "__main__":
    op = login(os.environ["MD_USER"], os.environ["MD_PW"])
    cid = sys.argv[1] if len(sys.argv) > 1 else "6556"
    h = get(op, f"/course/view.php?id={cid}")
    out = f"/tmp/opencode/course{cid}.html"
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(h)
    abschnitte = h.count('id="section-')
    print(f"gespeichert: {out} ({len(h)} bytes, {abschnitte} Abschnitte)")
