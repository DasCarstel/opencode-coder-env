#!/usr/bin/env python3
"""moodle_parse.py - Parser fuer die FernUni-Moodle-Instanz (Theme boost_union_feu).

Die Instanz nutzt zwei verschiedene Kursformate, beide werden unterstuetzt:

  * ``format_onetopic``  - alle Abschnitte stehen inline auf der Kursseite,
    Abschnittsnamen kommen aus der Tab-Navigation (``id="onetabid-<sectionid>"``).
    Beispiel: 6556 Data Engineering.

  * ``format_tiles``     - die Kursseite zeigt nur Kacheln (``ul.tiles > li.tile``)
    mit ``data-section`` / ``data-true-sectionid``; der Inhalt eines Abschnitts
    wird ueber ``/course/section.php?id=<sectionid>`` geladen.
    Beispiel: 6591 Maschinelles Lernen, 4714, 4419.

Das Theme bricht ``<li>``-Tags ueber mehrere Zeilen um, daher wird durchgehend mit
Positions-Slicing statt mit verschachtelten Regex-Listen gearbeitet.
"""
import datetime
import html as ht
import re

UA_DEFAULT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120 Safari/537.36")

RE_COURSE_TITLE = re.compile(r"<title>(.*?)\|\s*Moodle</title>", re.S)
RE_TAB = re.compile(r'<li[^>]*id="onetabid-(\d+)"[^>]*>(.*?)</li>', re.S)
RE_ONETOPIC_SECTION = re.compile(r'<li[^>]*id="section-(\d+)"[^>]*>', re.S)
RE_TILE = re.compile(r'<li[^>]*class="[^"]*\btile\b[^"]*"[^>]*data-section="(\d+)"[^>]*'
                     r'data-true-sectionid="(\d+)"[^>]*>', re.S)
RE_TILE_LINK = re.compile(r'<a[^>]*class="tile-link"[^>]*>(.*?)</a>', re.S)
RE_ARIA = re.compile(r'aria-label="([^"]*)"')
RE_MODULE = re.compile(r'<li\b[^>]*id="module-(\d+)"[^>]*>', re.S)
RE_MODTYPE = re.compile(r'data-modtype="([a-z_]+)"')
RE_MODTYPE_CLASS = re.compile(r'\bmodtype_([a-z_]+)')
RE_ACTNAME = re.compile(r'<span class="instancename">(.*?)</span>\s*</a>', re.S)
RE_ACTHREF = re.compile(r'<a[^>]+href="([^"]*?/mod/[a-z_]+/view\.php\?[^"]*?id=\d+[^"]*)"', re.S)
RE_ACTDESC = re.compile(r'class="activity-description"[^>]*>(.*?)</div>', re.S)
RE_ACTDETAIL = re.compile(r'class="resourcelinkdetails">(.*?)</span>', re.S)
RE_H3 = re.compile(r"<h3[^>]*>(.*?)</h3>", re.S)
RE_FILELINK = re.compile(r'href="([^"]*?/pluginfile\.php[^"]*)"')
RE_FOLDERITEM = re.compile(r'<a[^>]+href="([^"]*?/pluginfile\.php[^"]*)"[^>]*>(.*?)</a>', re.S)
RE_CONTENT_URL = re.compile(r'href="([^"]*?/mod/(?:url|resource)/view\.php\?[^"]*)"')
RE_ACTIVITYNAME = re.compile(r'data-activityname="([^"]*)"')
RE_AVAIL = re.compile(r'<div[^>]*class="[^"]*\bavailabilityinfo\b[^"]*"[^>]*>(.*?)</div>',
                      re.S)
RE_TAB_TITLE = re.compile(r'title="([^"]*)"')
RE_DIV_OPEN = re.compile(r"<div\b[^>]*>", re.I)
RE_DIV_TAGS = re.compile(r"<div\b[^>]*>|</div\s*>", re.I)
RE_LABEL_DIV = re.compile(r'<div[^>]*class="[^"]*\b(?:no-overflow|contentwithoutlink|'
                          r'contentafterlink)\b[^"]*"[^>]*>', re.I)
RE_DATE_DE = re.compile(r"\d{1,2}\.\d{1,2}\.\d{2,4}(?:[,\s]+(?:um\s+)?\d{1,2}:\d{2})?")
RE_DATE_LONG = re.compile(
    r"(?:(?:Montag|Dienstag|Mittwoch|Donnerstag|Freitag|Samstag|Sonntag),?\s+)?"
    r"\d{1,2}\.\s*(?:Januar|Februar|M(?:ä|ae)rz|April|Mai|Juni|Juli|August|September|"
    r"Oktober|November|Dezember)\s+\d{4}(?:[,\s]+(?:um\s+)?\d{1,2}:\d{2})?", re.I)
RE_EPOCH = re.compile(r'data-timestamp="(\d{9,14})"')
RE_FALLIG = re.compile(r"(?:F(?:ä|ae)llig(?:keitsdatum|keit|termin)?|Abgabetermin|Due date)\b",
                       re.I)
# Ueberschriften, die auf Moodle-Seiten reine Standardtexte sind (kein Name)
STANDARD_HEADINGS = {"beschreibung", "bewertung", "bewertungen", "feedback", "abgabe",
                     "abgaben", "dateien", "inhalte", "inhalt", "einstellungen",
                     "informationen", "zusammenfassung", "weitere informationen"}


def clean(s):
    """HTML zu Klartext (Tags weg, Entities auf, Whitespace normalisiert).

    Die FernUni-Instanz schreibt Zugaenglichkeits-Labels als
    ``<span class="accesshide " > MEDIAL</span>`` - mit Leerzeichen **vor** dem
    ``>``. Das ``\\s*`` vor dem ``>`` ist deshalb Pflicht, sonst bleibt der
    Typ-Text ("MEDIAL", "Datei", "Kurstext, PDF") im Aktivitaetsnamen haengen.
    """
    if not s:
        return None
    s = re.sub(r'<span class="accesshide[^"]*"\s*>.*?</span>', " ", s, flags=re.S)
    s = re.sub(r"<script.*?</script>", " ", s, flags=re.S)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", ht.unescape(s)).strip() or None


def clean_absaetze(s):
    """Wie ``clean()``, behaelt aber Absatzumbrueche fuer Zusammenfassungen.

    ``</p>``/``</div>`` -> Leerzeile, ``<br>`` -> Zeilenumbruch; Mehrfach-
    Leerzeichen je Zeile werden zusammengefasst, Absaetze bleiben getrennt.
    """
    if not s:
        return None
    s = re.sub(r'<span class="accesshide[^"]*"\s*>.*?</span>', " ", s, flags=re.S)
    s = re.sub(r"<script.*?</script>", " ", s, flags=re.S)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"</(?:p|div|li|ul|ol|tr|h[1-6])\s*>", "\n\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = ht.unescape(s)
    s = re.sub(r"[^\S\n]+", " ", s)          # Leerraum ausser Zeilenumbruechen
    s = re.sub(r" *\n *", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip() or None


def _slices(text, regex):
    """(start, end, groups) fuer jeden Treffer von regex - end ist der naechste Treffer."""
    hits = [(m.start(), m.groups()) for m in regex.finditer(text)]
    out = []
    for i, (start, groups) in enumerate(hits):
        end = hits[i + 1][0] if i + 1 < len(hits) else len(text)
        out.append((start, end, groups))
    return out


def course_title(html):
    m = RE_COURSE_TITLE.search(html)
    if not m:
        return None
    t = clean(m.group(1))
    for pre in ("Kurs:", "Course:"):
        if t and t.lower().startswith(pre.lower()):
            t = t.split(":", 1)[1].strip()
    # bei onetopic steht der Kursname doppelt: "X | X - WiSe 2026/27"
    if t and " | " in t:
        parts = [p.strip() for p in t.split(" | ")]
        if len(parts) == 2 and parts[1].startswith(parts[0]):
            t = parts[1]
    return t


def parse_activities(body):
    """Aktivitaeten aus einem Abschnitts-Body (funktioniert in beiden Formaten)."""
    out = []
    for start, end, (cmid,) in _slices(body, RE_MODULE):
        tag = body[start:body.find(">", start) + 1]
        blk = body[start:end]
        mt = RE_MODTYPE.search(tag)
        if not mt:
            mtcls = RE_MODTYPE_CLASS.search(tag)
            mt = mtcls
        href = RE_ACTHREF.search(blk)
        nm = RE_ACTNAME.search(blk)
        desc = RE_ACTDESC.search(blk)
        det = RE_ACTDETAIL.search(blk)
        if not nm:
            # Ersatz: aria-label, dann data-activityname (assign/label/folder)
            al = RE_ARIA.search(blk)
            nm_txt = clean(al.group(1)) if al else None
            if not nm_txt:
                an = RE_ACTIVITYNAME.search(tag) or RE_ACTIVITYNAME.search(blk)
                nm_txt = clean(an.group(1)) if an else None
        else:
            nm_txt = clean(nm.group(1))
        typ = mt.group(1) if mt else None
        act = {
            "type": typ,
            "cmid": cmid,
            "name": nm_txt,
            "url": ht.unescape(href.group(1)) if href else None,
            "description": clean(desc.group(1)) if desc else None,
            "details": clean(det.group(1)) if det else None,
            "files": sorted({ht.unescape(u) for u in RE_FILELINK.findall(blk)}),
        }
        if typ == "label":
            act["content_html"] = label_content(blk)
        av = RE_AVAIL.search(blk)
        if av and "isrestricted" in blk[av.start():av.start() + 200]:
            # Gesperrte Aktivitaeten sind in Moodle nicht abrufbar (view.php
            # leitet auf die Kursseite um) - Grund fuer die Notiz merken.
            txt = clean(av.group(1))
            if txt:
                act["availability"] = txt
        out.append(act)
    return out


def parse_section_body(body, number=None, name=None, section_id=None):
    """Abschnitt aus einem HTML-Fragment, das die module-<li>s enthaelt."""
    sec = {"number": number, "id": section_id, "name": name, "activities": parse_activities(body)}
    sm = re.search(r'class="summary(?:text)?"[^>]*>(.*?)</div>', body, re.S)
    if sm:
        sec["summary"] = clean_absaetze(sm.group(1))
    if not sec["name"]:
        m = RE_H3.search(body)
        if m:
            sec["name"] = clean(m.group(1))
    return sec


def parse_activities_only(body):
    return parse_activities(body)


def parse_onetopic(html):
    """Die auf der Kursseite gerenderten Abschnitte einer onetopic-Seite.

    Achtung: bei ``format_onetopic`` mit Tabs steht nur der **aktive** Abschnitt
    im HTML - die uebrigen laedt ``sync.py`` einzeln nach.
    """
    title = course_title(html)
    tabs = dict(parse_tabs(html))
    sections = []
    for num, sid, body in parse_onetopic_blocks(html):
        name = tabs.get(sid)
        if not name or name == title:
            name = "Allgemein" if num == 0 else f"Abschnitt {num}"
        sections.append(parse_section_body(body, num, name, sid))
    return title, sections


def parse_tiles(html):
    """Kacheln einer tiles-Kursseite: [(nummer, sectionid, name)]."""
    titles = {}
    for m in RE_TILE.finditer(html):
        start, end = m.start(), m.end()
        # Kachel endet beim naechsten <li class="tile
        nxt = html.find('<li class="tile', end)
        blk = html[start:nxt if nxt > 0 else end + 4000]
        al = RE_ARIA.search(blk)
        nm = None
        if al:
            nm = re.split(r",\s*(?:öffnet|opens)", ht.unescape(al.group(1)))[0].strip()
        if not nm:
            nm = clean(RE_TILE_LINK.search(blk).group(1)) if RE_TILE_LINK.search(blk) else None
        titles[m.group(2)] = (m.group(1), nm)
    # Reihenfolge erhalten
    out = []
    for m in RE_TILE.finditer(html):
        num, sid = m.group(1), m.group(2)
        nm = titles.get(sid, (num, None))[1]
        out.append((num, sid, nm))
    return out


def parse_course(html):
    """Vereinheitlicht: Kursseite -> {title, format, sections:[...]}."""
    title = course_title(html)
    if RE_ONETOPIC_SECTION.search(html) and not RE_TILE.search(html):
        t, secs = parse_onetopic(html)
        return {"title": t or title, "format": "onetopic", "sections": secs}
    tiles = parse_tiles(html)
    if tiles:
        return {"title": title, "format": "tiles",
                "sections": [{"number": int(n), "id": sid, "name": nm,
                              "activities": None} for n, sid, nm in tiles]}
    # Standard-Themenformat: Abschnitte inline
    secs = []
    for start, end, (num,) in _slices(html, RE_ONETOPIC_SECTION):
        secs.append(parse_section_body(html[start:end], int(num)))
    return {"title": title, "format": "topics", "sections": secs}


def div_inhalt(html, pos):
    """HTML-Inhalt des ``<div>``-Elements, das an ``pos`` beginnt (schachtelungssicher).

    Die Instanz schachtelt Label-Inhalte mehrfach (``contentwithoutlink`` >
    ``no-overflow``), ein ``(.*?)</div>`` wuerde dort zu frueh abschneiden.
    """
    tag = RE_DIV_OPEN.match(html, pos)
    if not tag:
        return None
    start = tag.end()
    depth = 1
    for m in RE_DIV_TAGS.finditer(html, start):
        if m.group(0).lower().startswith("</"):
            depth -= 1
            if depth == 0:
                return html[start:m.start()]
        else:
            depth += 1
    return None


def _dateilinks(html, needle=None):
    """[(url, name|None)] aller pluginfile-Links einer Seite.

    ``needle`` grenzt auf einen Modultyp ein (z. B. ``mod_assign``). Eintraege
    ohne Linktext bleiben erhalten - der Dateiname kommt dann aus
    Content-Disposition bzw. dem URL-Basename.
    """
    out, seen = [], set()
    for m in RE_FOLDERITEM.finditer(html):
        url = ht.unescape(m.group(1))
        if needle and needle not in url:
            continue
        key = url.split("?", 1)[0]
        if key in seen:
            continue
        seen.add(key)
        name = clean(m.group(2))
        if not (name or re.search(r"\.[A-Za-z0-9]{1,8}(?:\?|$)", url)):
            continue
        out.append((url, name))
    return out


def parse_folder(html):
    """Dateien einer mod/folder-Seite: [(url, name|None)]."""
    return _dateilinks(html)


def parse_assign_attachments(html):
    """Anhaenge einer assign-Aktivitaet: [(url, name|None)].

    ``assign`` haengt Dateien an ``introattachments``; sie stehen nicht in der
    Abschnittsliste und nur auf ``/mod/assign/view.php?id=<cmid>``.
    """
    return _dateilinks(html, needle="mod_assign")


def label_content(blk):
    """Inneres HTML eines label-Moduls (dort steht der eigentliche Aufgabentext)."""
    m = RE_LABEL_DIV.search(blk)
    if m:
        inner = div_inhalt(blk, m.start())
        if inner and clean(inner):
            return inner
    m = RE_DIV_OPEN.search(blk)
    if m:
        inner = div_inhalt(blk, m.start())
        if inner and clean(inner):
            return inner
    rest = re.sub(r"^<li\b[^>]*>", "", blk, flags=re.I)
    rest = re.sub(r"</li\s*>\s*$", "", rest, flags=re.I)
    return rest if clean(rest) else None


def parse_tabs(html):
    """Tab-Navigation von format_onetopic: [(sectionid, name)] in Seitenreihenfolge."""
    out = []
    for m in RE_TAB.finditer(html):
        inner = m.group(2)
        t = RE_TAB_TITLE.search(inner) or RE_TAB_TITLE.search(m.group(0))
        name = clean(t.group(1)) if t else None
        out.append((m.group(1), name or clean(inner)))
    return out


def parse_onetopic_blocks(html):
    """Gerenderte Abschnitte einer onetopic-Seite: [(nummer, sectionid, body)]."""
    out = []
    for start, end, (num,) in _slices(html, RE_ONETOPIC_SECTION):
        body = html[start:end]
        tag = html[start:html.find(">", start) + 1]
        m = re.search(r'data-id="(\d+)"', tag) or re.search(r'data-id="(\d+)"',
                                                           body)
        out.append((int(num), m.group(1) if m else None, body))
    return out


def title_name(html):
    """Aktivitaetsname aus ``<title>Kurs: NAME | Moodle</title>``."""
    m = RE_COURSE_TITLE.search(html or "")
    if not m:
        return None
    t = clean(m.group(1))
    if not t:
        return None
    t = t.split(" | ")[0].strip()
    if ":" in t:
        t = t.rsplit(":", 1)[1].strip()
    elif " \u00b7 " in t:
        t = t.rsplit(" \u00b7 ", 1)[1].strip()
    return t or None


def module_page_names(html):
    """Kandidaten fuer den Namen einer Aktivitaet von ihrer view.php (bester zuerst).

    ``assign``/``folder``/``label`` tragen oft keinen ``instancename``; der Name
    steht dann im Aktivitaetskopf, in einer Ueberschrift oder im Seitentitel.
    """
    out = []

    def add(t):
        if t and t not in out:
            out.append(t)

    m = re.search(r'<div[^>]*class="[^"]*\bactivity-header\b[^"]*"[^>]*>(.*?)</div>',
                  html or "", re.S)
    if m:
        for h in re.finditer(r"<h[1-4][^>]*>(.*?)</h[1-4]\s*>", m.group(1), re.S):
            add(clean(h.group(1)))
    for rx in (r"<h1[^>]*>(.*?)</h1\s*>", r"<h2[^>]*>(.*?)</h2\s*>",
               r"<h3[^>]*>(.*?)</h3\s*>"):
        mm = re.search(rx, html or "", re.S)
        if mm:
            add(clean(mm.group(1)))
    add(title_name(html))
    return out


def _epoch_text(ts):
    try:
        sek = int(str(ts)[:10])
        return datetime.datetime.fromtimestamp(sek).strftime("%d.%m.%Y %H:%M")
    except (ValueError, OverflowError, OSError):
        return None


def assign_due(html):
    """Faelligkeitsdatum einer assign-Seite ('Faellig: ...') bzw. None.

    Erst der Text hinter einem 'Faellig'-Label (bereits lokalisiert), sonst ein
    ``data-timestamp`` in dessen Naehe - und nur wenn es genau einen gibt.
    """
    if not html:
        return None
    for m in RE_FALLIG.finditer(html):
        fenster = html[m.start():m.start() + 700]
        d = RE_DATE_LONG.search(fenster) or RE_DATE_DE.search(fenster)
        if d:
            return clean(d.group(0))
        ts = RE_EPOCH.search(fenster)
        if ts:
            return _epoch_text(ts.group(1))
    treffer = RE_EPOCH.findall(html)
    if len(treffer) == 1:
        return _epoch_text(treffer[0])
    return None


if __name__ == "__main__":
    import sys
    for f in sys.argv[1:]:
        html = open(f, encoding="utf-8").read()
        c = parse_course(html)
        print(f"\n########## {f} :: {c['title']}  [{c['format']}] ##########")
        for s in c["sections"]:
            acts = s.get("activities")
            if acts is None:
                print(f"  § [{s['number']}] {s['name']}  (id={s['id']}, Inhalt separat)")
                continue
            print(f"  § [{s['number']}] {s['name']}  ({len(acts)} Akt.)")
            for a in acts:
                det = f"   {a['details']}" if a["details"] else ""
                print(f"      - {str(a['type']):12s} {str(a['name'])[:56]:56s}{det}")
