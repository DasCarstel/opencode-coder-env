#!/usr/bin/env python3
"""obsidian.py - Erzeugung der Markdown-Notizen im Vault.

Enthaelt die Namensregeln (Slug, Dateinamen), das Frontmatter-Schema und die
Renderer fuer Abschnittsnotizen, Aktivitaetszeilen und den Kursindex.

Alle Notizen tragen im Frontmatter ``generated: true``. Nur solche Dateien
darf der Sync ueberschreiben.
"""
import datetime
import html as html_mod
import re
import unicodedata
import urllib.parse

# Aktivitaetstyp -> Icon laut Vorgabe
ICONS = {
    "resource": "\U0001f4c4",            # 📄
    "folder": "\U0001f4c4",              # 📄 (enthält Dateien)
    "helixmedia": "\U0001f3ac",          # 🎬
    "quiz": "\u270f\ufe0f",              # ✏️
    "assign": "\u270f\ufe0f",            # ✏️
    "forum": "\U0001f4ac",               # 💬
    "url": "\U0001f517",                 # 🔗
    "label": "\U0001f4dd",               # 📝
    "page": "\U0001f4dd",                # 📝
    "book": "\U0001f4dd",                # 📝
}
ICON_UNKNOWN = "\u2753"                  # ❓
AUTO_MARKER = "AUTO-GENERIERT von fernuni-sync"

# Die FernUni-Instanz haengt an Aktivitaetsnamen ein Typ-Label an
# (z. B. "Folien-Intro.pdf Datei", "Intro (Video) MEDIAL").
ACTIVITY_LABELS = {
    "resource": ("Datei", "File"),
    "resource_pdf": ("Kurstext, PDF", "Kurstext"),
    "folder": ("Ordner", "Folder"),
    "helixmedia": ("MEDIAL", "Media"),
    "quiz": ("Test", "Quiz"),
    "assign": ("Aufgabe", "Assignment"),
    "forum": ("Forum",),
    "url": ("Link", "URL"),
    "page": ("Textseite", "Seite", "Page"),
    "book": ("Buch", "Book"),
    "label": ("Text", "Label"),
}


def strip_activity_label(name, typ):
    """Typ-Label am Namensende entfernen ('Folien.pdf Datei' -> 'Folien.pdf')."""
    if not name:
        return name
    for label in ACTIVITY_LABELS.get(typ or "", ()):
        if name == label:
            return name
        if name.endswith(" " + label):
            trimmed = name[: -len(label)].strip(" -\u2013\u00b7")
            if trimmed:
                return trimmed
    return name

_UMLAUT = {"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "ae", "Ö": "oe", "Ü": "ue", "ß": "ss"}


def icon_for(typ):
    return ICONS.get(typ or "", ICON_UNKNOWN)


def slug(name, fallback="ohne-namen", max_len=60):
    """Umlaute -> ae/oe/ue, alles ausser [A-Za-z0-9._-] -> '-', Mehrfach-'-'
    zusammenfassen, auf max_len kuerzen, keine fuehrenden/abschliessenden '-'."""
    if not name or not name.strip():
        return fallback
    s = unicodedata.normalize("NFC", name)
    for k, v in _UMLAUT.items():
        s = s.replace(k, v)
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    s = s[:max_len].strip("-.")
    return s or fallback


def section_slug(number, name):
    """'03-<slug>' bzw. '03-abschnitt-03' wenn der Name leer ist."""
    ss = f"{int(number):02d}"
    body = slug(name, fallback="", max_len=60 - len(ss) - 1 - 4)
    return f"{ss}-{body}" if body else f"{ss}-abschnitt-{ss}"


def safe_filename(name, fallback="datei", max_len=120):
    """Moodle-Dateiname bereinigen, Endung erhalten."""
    base, ext = split_ext(name or "")
    stem = slug(base, fallback="", max_len=max(20, max_len - len(ext))) or fallback
    return (stem + ext)[:160]


def safe_existing_filename(name, fallback="datei", max_len=120):
    """Wie safe_filename, aber fuer Namen, die schon eine Endung im Stem tragen
    (file.pdf.pdf wird zu file-pdf.pdf)."""
    stem, ext = split_ext(name or "")
    stem = safe_filename(stem, fallback, max_len) if stem else fallback
    return stem + ext


def split_ext(name):
    m = re.match(r"^(.*?)(\.[A-Za-z0-9]{1,8})$", (name or "").strip())
    if m:
        return m.group(1), m.group(2).lower()
    return (name or "").strip(), ""


def resolve_collision(used, filename):
    """'x.pdf' -> 'x-2.pdf' wenn bereits vergeben."""
    if filename not in used:
        used.add(filename)
        return filename
    stem, ext = split_ext(filename)
    i = 2
    while True:
        cand = f"{stem}-{i}{ext}"
        if cand not in used:
            used.add(cand)
            return cand
        i += 1


def generated_path(path):
    """Zielpfad fuer eine kollidierende, nicht ueberschreibbare Notiz."""
    return path[:-3] + ".generated-2.md" if path.endswith(".md") else path + ".generated-2"


def wikilink(path, label=None):
    """Obsidian-Wikilink mit URL-kodiertem Pfad (Leerzeichen, Umlaute)."""
    enc = "/".join(urllib.parse.quote(p, safe="") for p in path.split("/"))
    return f"[[{enc}|{label or path.rsplit('/', 1)[-1]}]]" if label else f"[[{enc}]]"


def fmt_size(n):
    if n is None:
        return "unbekannt"
    for unit, div in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if n >= div:
            v = n / div
            return f"{v:.1f} {unit}" if v < 10 else f"{v:.0f} {unit}"
    return f"{int(n)} B"


def now_stamp():
    return datetime.datetime.now().replace(microsecond=0).isoformat()


def today():
    return datetime.date.today().isoformat()


def yaml_str(v):
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


_RE_PLAIN = re.compile(r"^[A-Za-z0-9_.-]+$")
_MAGIC = {"true", "false", "null", "yes", "no", "on", "off", "~"}


def yaml_item(v):
    """Listeneintrag: einfache Werte ohne Quotes, alles andere gequotet
    (Beispiel: '- moodle' bzw. '- \"modul/63123\"')."""
    s = str(v)
    if _RE_PLAIN.match(s) and s.lower() not in _MAGIC:
        return s
    return yaml_str(s)


def frontmatter(data):
    """Frontmatter-Block aus einem dict bauen; Listen als YAML-Sequenz."""
    lines = ["---"]
    for key, val in data.items():
        if val is None:
            continue
        if isinstance(val, list):
            lines.append(f"{key}:")
            for item in val:
                lines.append(f"  - {yaml_item(item)}")
        elif isinstance(val, bool):
            lines.append(f"{key}: {'true' if val else 'false'}")
        else:
            lines.append(f"{key}: {yaml_str(val)}")
    lines.append("---")
    return "\n".join(lines)


def parse_frontmatter(text):
    """{key: wert} aus einem bestehenden Dokument; Werte bleiben Strings."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    out = {}
    for line in text[3:end].splitlines():
        line = line.rstrip()
        if not line or line.lstrip().startswith("-") or ":" not in line:
            continue
        k, v = line.split(":", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def is_generated(text):
    return str(parse_frontmatter(text).get("generated", "")).lower() == "true"


# Typen mit uneindeutigem Icon: kurzes deutsches Wort in Klammern hinter dem
# Namen. resource/resource_pdf/folder/helixmedia bleiben ohne Zusatz.
TYPE_WORDS = {
    "quiz": "Quiz",
    "assign": "Aufgabe",
    "forum": "Forum",
    "url": "Link",
    "label": "Text",
    "page": "Textseite",
    "book": "Buch",
}


def _md_protect(store, markdown):
    """Fertigen Markdown-Baustein ablegen, im Text nur einen Platzhalter setzen.

    Noetig, weil HTML-Entities genau **einmal** unescaped werden duerfen: ein
    fertiger Link (``…course/view.php?id=6556&section=3``) darf nicht durch den
    zweiten ``unescape``-Lauf gehen - dort wird ``&sect`` sonst zu ``\u00a7``
    ("&section=3" -> "\u00a7ion=3").
    """
    store.append(markdown)
    return f"\x00{len(store) - 1}\x00"


def _md_restore(s, store):
    return re.sub(r"\x00(\d+)\x00", lambda m: store[int(m.group(1))], s)


def _md_link(match, base_url=""):
    """``<a href="…">text</a>`` -> ``[text](url)`` (relative Links absolut machen)."""
    href = html_mod.unescape(match.group(1) or "").strip()
    text = _md_inline(match.group(2), base_url).strip()
    if not href or href.startswith(("#", "javascript:")):
        return text
    if href.startswith(("http://", "https://", "mailto:")):
        url = href
    elif base_url:
        url = urllib.parse.urljoin(base_url.rstrip("/") + "/", href.lstrip("/"))
    else:
        url = href
    return f"[{text or url}]({url})"


def _md_img(match, base_url=""):
    src = html_mod.unescape(match.group(1) or "").strip()
    if not src:
        return ""
    if not src.startswith(("http://", "https://")) and base_url:
        src = urllib.parse.urljoin(base_url.rstrip("/") + "/", src.lstrip("/"))
    return f"![]({src})"


def _md_inline(s, base_url="", store=None):
    """Inline-HTML -> Markdown (Links, Fett, Kursiv, Code, Bilder).

    Entities werden genau einmal aufgeloest; fertige Links/Bilder stehen als
    Platzhalter in ``store`` und werden erst am Ende eingesetzt.
    """
    if not s:
        return ""
    store = [] if store is None else store
    s = re.sub(r"<script\b.*?</script\s*>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<style\b.*?</style\s*>", " ", s, flags=re.S | re.I)
    s = re.sub(r'<span class="accesshide[^"]*"\s*>.*?</span\s*>', " ", s, flags=re.S)
    s = re.sub(r"<a\b[^>]*?href=\"([^\"]*)\"[^>]*>(.*?)</a\s*>",
               lambda m: _md_protect(store, _md_link(m, base_url)), s,
               flags=re.S | re.I)
    s = re.sub(r"<img\b[^>]*?src=\"([^\"]*)\"[^>]*>",
               lambda m: _md_protect(store, _md_img(m, base_url)), s, flags=re.I)
    s = re.sub(r"</?(?:strong|b)\b[^>]*>", "**", s, flags=re.I)
    s = re.sub(r"</?(?:em|i)\b[^>]*>", "*", s, flags=re.I)
    s = re.sub(r"</?code\b[^>]*>", "`", s, flags=re.I)
    s = re.sub(r"<br\b[^>]*/?>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = html_mod.unescape(s)                       # genau einmal
    return _md_restore(s, store)


def html_to_markdown(html_text, base_url=""):
    """Rohes Moodle-HTML in Markdown wandeln (Absaetze, Listen, Links, Code).

    Wird fuer ``mod_label`` gebraucht: dort steht der eigentliche Aufgabentext,
    inklusive Links, die als klickbare Markdown-Links erhalten bleiben muessen.
    Rohes HTML wird nie durchgereicht; jede Zeichenkette wird genau einmal
    HTML-unescaped (fertige Markdown-Bausteine laufen nicht erneut durch).
    """
    if not html_text:
        return ""
    store = []
    s = re.sub(r"<script\b.*?</script\s*>", " ", html_text, flags=re.S | re.I)
    s = re.sub(r"<style\b.*?</style\s*>", " ", s, flags=re.S | re.I)
    s = re.sub(r'<span class="accesshide[^"]*"\s*>.*?</span\s*>', " ", s, flags=re.S)
    s = re.sub(r"<pre\b[^>]*>(.*?)</pre\s*>",
               lambda m: _md_protect(store, "\n\n```\n" + html_mod.unescape(
                   re.sub(r"<[^>]+>", "", m.group(1))).strip("\n") + "\n```\n\n"),
               s, flags=re.S | re.I)
    s = re.sub(r"<h([1-6])\b[^>]*>(.*?)</h\1\s*>",
               lambda m: _md_protect(store, "\n\n" + "#" * int(m.group(1)) + " "
                                     + _md_inline(m.group(2), base_url).strip()
                                     + "\n\n"),
               s, flags=re.S | re.I)
    s = re.sub(r"<li\b[^>]*>", "\n- ", s, flags=re.I)
    s = re.sub(r"</li\s*>", "\n", s, flags=re.I)
    s = re.sub(r"</(?:ul|ol|p|div|table|tr|thead|tbody|blockquote|dl|dt|dd|figure)\s*>",
               "\n\n", s, flags=re.I)
    s = re.sub(r"<(?:ul|ol|table|blockquote|dl|figure)\b[^>]*>", "\n\n", s,
               flags=re.I)
    s = re.sub(r"<hr\b[^>]*/?>", "\n\n---\n\n", s, flags=re.I)
    s = _md_inline(s, base_url, store)
    s = re.sub(r"[^\S\n]+", " ", s)
    s = re.sub(r" *\n *", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return _md_restore(s, store).strip()


def label_block(md):
    """Label-Inhalt als eigener Block - ohne erzeugte Zwischenueberschrift.

    Eine echte Ueberschriftzeile aus dem Label bleibt erhalten; aus Fliesstext
    wird nie eine Ueberschrift geraten und nichts abgeschnitten. Leere Labels
    liefern einen leeren Block (keine leere Ueberschrift, kein Leerblock).
    """
    md = (md or "").strip()
    if not md:
        return []
    return ["", *md.splitlines(), ""]


def activity_line(act, url, size_txt=None, local_link=None):
    """Eine Zeile fuer den Abschnitt 'Inhalte'.

    Der Typ wird ausschliesslich ueber das Icon transportiert; nur wenn das
    Icon uneindeutig ist, steht ein kurzes deutsches Wort in Klammern hinter
    dem Namen (z. B. ``- \u270f\ufe0f **\u00dcbungsblatt 3** *(Quiz)*``). Ein
    Modultyp-Zusatz wie "(helixmedia)" gehoert nicht in die Zeile.
    """
    ic = icon_for(act.get("type"))
    name = act.get("name") or "(ohne Namen)"
    wort = TYPE_WORDS.get(act.get("type") or "")
    parts = [f"{ic} **{name}**" + (f" *({wort})*" if wort else "")]
    if act.get("due"):
        parts.append(f"F\u00e4llig: {act['due']}")
    if act.get("availability"):
        parts.append(f"\U0001f512 {act['availability']}")
    if size_txt:
        parts.append(f"`{size_txt}`")
    if local_link:
        parts.append(f"\u2b07 {local_link}")
    else:
        parts.append("[In Moodle \u00f6ffnen](%s)" % url)
    if local_link:
        parts.append(f"[Moodle]({url})")
    return "- " + " \u00b7 ".join(parts)


def titel_fuer(cfg_course, abschnittsname, kursname=None):
    """Titel ohne Kursnamen-Dopplung.

    Abschnittsname (getrimmt) == Kursname -> nur der Abschnittsname (Kurs 6556:
    "Data Engineering für Data Science"); sonst '<Kurskuerzel> - <Abschnittsname>'
    mit Kurskuerzel = modul aus config.json (z. B. "64401"), sonst der Kursname.
    """
    name = (abschnittsname or "").strip()
    kursnamen = {k for k in ((kursname or "").strip(),
                             (cfg_course.get("titel") or "").strip()) if k}
    if not name:
        return next(iter(kursnamen), "")
    if name in kursnamen:
        return name
    kuerzel = (str(cfg_course.get("modul") or "").strip()
               or (kursname or "").strip() or (cfg_course.get("titel") or "").strip())
    return f"{kuerzel} \u2013 {name}" if kuerzel else name


def callout_zusammenfassung(text):
    """Zusammenfassung als eingeklappter Callout.

    Absatzumbrueche bleiben als '>'-Leerzeilen erhalten.
    """
    text = (text or "").strip()
    zeilen = ["> [!quote]- Zusammenfassung aus Moodle"]
    if not text:
        return zeilen + ["> _Keine Zusammenfassung in Moodle hinterlegt._"]
    for absatz in re.split(r"\n\s*\n", text):
        absatz = " ".join(absatz.split())
        if absatz:
            zeilen.append(f"> {absatz}")
            zeilen.append(">")
    if len(zeilen) > 1 and zeilen[-1] == ">":
        zeilen.pop()
    return zeilen


def section_note(cfg_course, section, acts, note_meta, summary, source_url,
                 created, updated, material_dir, body_lines, course_title=None):
    """Abschnittsnotiz rendern. body_lines sind die fertigen Zeilen fuer 'Inhalte'."""
    fm = {
        "title": titel_fuer(cfg_course, section.get("name"), course_title),
        "tags": ["moodle", "generated"]
        + ([f"modul/{cfg_course['modul']}"] if cfg_course.get("modul") else []),
        "created": created,
        "updated": updated,
        "status": "aktiv",
        "typ": "moodle-abschnitt",
        "generated": True,
        "moodle_course": str(cfg_course["id"]),
        "moodle_section": str(section["number"]),
        "source": source_url,
    }
    out = [frontmatter(fm), "",
           f"<!-- {AUTO_MARKER} \u00b7 NICHT BEARBEITEN \u00b7 "
           f"\u00c4nderungen werden \u00fcberschrieben -->", "",
           f"# {section['name']}", "",
           "> [!info] Automatisch aus Moodle erzeugt",
           "> Eigene Notizen bitte als **separate** Notiz anlegen und hier verlinken.",
           "",
           "## Zusammenfassung", *callout_zusammenfassung(summary),
           "",
           "## Inhalte"]
    out += body_lines or ["_Keine Aktivit\u00e4ten in diesem Abschnitt._"]
    out += [""]
    return "\n".join(out)


def index_note(cfg_course, course_title, rows, stamp, act_total, file_total, material_root):
    """_Moodle-Index.md rendern."""
    fm = {
        "title": f"{cfg_course['titel']} \u2013 Moodle-Index",
        "tags": ["moodle", "generated", "index"]
        + ([f"modul/{cfg_course['modul']}"] if cfg_course.get("modul") else []),
        "created": rows[0]["created"] if rows else today(),
        "updated": stamp,
        "status": "aktiv",
        "typ": "moodle-index",
        "generated": True,
        "moodle_course": str(cfg_course["id"]),
    }
    out = [frontmatter(fm), "",
           f"<!-- {AUTO_MARKER} \u00b7 NICHT BEARBEITEN \u00b7 "
           f"\u00c4nderungen werden \u00fcberschrieben -->", "",
           f"# {course_title} \u2013 Moodle-Index", "",
           f"- Kurs-ID: `{cfg_course['id']}`",
           f"- Sync-Zeitpunkt: `{stamp}`",
           f"- Abschnitte: {len(rows)}",
           f"- Aktivit\u00e4ten: {act_total}",
           f"- lokal vorhandene Dateien: {file_total}",
           f"- Material: [[{urllib.parse.quote(material_root, safe='/.-_')}]]",
           "",
           "| # | Abschnitt | Aktivit\u00e4ten | Dateien lokal | Notiz |",
           "|---|-----------|-------------|---------------|-------|"]
    for r in rows:
        wiki = wikilink(r["note_path"], label=r["name"])
        out.append(f"| {r['number']} | {r['name']} | {r['acts']} | {r['files']} | {wiki} |")
    out += [""]
    return "\n".join(out)
