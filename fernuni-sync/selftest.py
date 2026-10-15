#!/usr/bin/env python3
"""selftest.py - Offline-Tests fuer fernuni-sync (ohne Netz, ohne Zugangsdaten).

Geprueft werden Parser, Slug-/Dateinamenregeln, Notiz-Rendering, die
Idempotenz-Regel und der komplette Kurslauf gegen Attrappen (FakeMoodle/
FakeDav) mit synthetischem Moodle-HTML:

  * format_onetopic: Abschnitt "Data Engineering für Data Science" mit
    genau 2 Aktivitaeten (helixmedia "Intro (Video)", resource "Folien-Intro.pdf").
  * format_tiles: Kacheln + separater section.php-Inhalt.
  * 1. Lauf laedt die Datei, 2. Lauf laedt nichts erneut, `created` bleibt.
  * Material-Datei liegt schon im Vault, aber nicht im State: gleiche Groesse ->
    uebernehmen, abweichende Groesse -> ueberschreiben (nie "-generated-2").

Aufruf: python3 fernuni-sync/selftest.py     (Exit 1 bei Fehlschlag)
"""
import os
import shutil
import sys
import tempfile
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import moodle_parse as mp      # noqa: E402
import obsidian as ob          # noqa: E402
import sync as sy              # noqa: E402
from dav import Dav            # noqa: E402

FEHLER = []
OK = [0]

MODUL = {"id": "6556", "slug": "63123-Data-Engineering-fuer-Data-Science",
         "base": "10-Semester/1-Semester", "modul": "63123",
         "titel": "Data Engineering für Data Science"}
BASIS = "10-Semester/1-Semester/63123-Data-Engineering-fuer-Data-Science/Moodle"
NOTIZ = f"{BASIS}/03-Data-Engineering-fuer-Data-Science.md"
PDF = f"{BASIS}/Material/03-Data-Engineering-fuer-Data-Science/Folien-Intro.pdf"


def check(name, cond, info=""):
    if cond:
        OK[0] += 1
        print(f"  ok   {name}")
    else:
        FEHLER.append(name)
        print(f"  FAIL {name}  {info}")


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
ONETOPIC = """<html><head><title>Kurs: Data Engineering für Data Science | \
Data Engineering für Data Science - WiSe 2026/27 | Moodle</title></head><body>
<ul class="nav-tabs">
<li id="onetabid-58900"><a href="#section-3">Data Engineering für Data Science</a></li>
<li id="onetabid-59000"><a href="#section-4">Überwachtes Lernen</a></li>
</ul>
<li id="section-3" class="section" data-id="58900">
 <h3 class="sectionname">Data Engineering für Data Science</h3>
 <div class="summarytext">Einführung in Datenpipelines.</div>
 <ul class="section img-text">
  <li class="activity helixmedia modtype_helixmedia" id="module-101" data-title="Intro (Video) MEDIAL">
   <a href="/mod/helixmedia/view.php?id=101"><span class="instancename">Intro (Video) <span class="accesshide " > MEDIAL</span></span></a></li>
  <li class="activity resource modtype_resource" id="module-102" data-title="Folien-Intro.pdf Datei">
   <a href="/mod/resource/view.php?id=102"><span class="instancename">Folien-Intro.pdf <span class="accesshide " > Datei</span></span></a>
   <span class="resourcelinkdetails">1.7 MB</span></li>
 </ul>
</li>
<li id="section-4" class="section" data-id="59000">
 <h3 class="sectionname">Überwachtes Lernen</h3>
 <ul><li class="activity quiz modtype_quiz" id="module-201" data-title="Übungsquiz 1">
  <a href="/mod/quiz/view.php?id=201"><span class="instancename">Übungsquiz 1</span></a></li></ul>
</li>
</body></html>"""

TILES = """<html><head><title>Kurs: Maschinelles Lernen | Moodle</title></head><body>
<li class="tile" data-section="4" data-true-sectionid="70004">
 <a class="tile-link" href="/course/section.php?id=70004"
    aria-label="Überwachtes Lernen, öffnet Kursabschnitt">Überwachtes Lernen</a></li>
</body></html>"""

TILES_SECTION = """<h3 class="sectionname">Überwachtes Lernen</h3>
<div class="summary">Regression und Klassifikation.</div>
<li id="section-4" class="section">
 <ul><li class="activity folder modtype_folder" id="module-301" data-title="Kapitel 2">
  <a href="/mod/folder/view.php?id=301"><span class="instancename">Kapitel 2</span></a></li></ul>
</li>"""

FOLDER = """<html><body><div class="filemanager">
<a href="/pluginfile.php/123/mod_folder/content/0/2.1 Lineare Regression.pdf?forcedownload=1">
 2.1 Lineare Regression.pdf</a>
</div></body></html>"""


def _tabs_abschnitt(num, sid, inhalt):
    """Ein einzelner Abschnitt, wie ihn die Instanz rendert (Abschnitt 0 + aktiv)."""
    return (f'<li id="section-0" class="section" data-id="58606">'
            f'<h3 class="sectionname">Allgemein</h3></li>\n'
            f'<li id="section-{num}" class="section" data-id="{sid}">\n'
            f'<h3 class="sectionname">Abschnitt {num}</h3>\n{inhalt}\n</li>\n')


# format_onetopic mit Tabs: die Kursseite zeigt nur den aktiven Abschnitt
ONETOPIC_TABS = """<html><head><title>Kurs: Data Engineering für Data Science | \
Data Engineering für Data Science - WiSe 2026/27 | Moodle</title></head><body>
<ul class="nav nav-tabs">
 <li class="nav-item" id="onetabid-58607"><a class="nav-link" href="#section-1"
   title="Data Engineering für Data Science">Data Engineering für Data Science</a></li>
 <li class="nav-item" id="onetabid-58608"><a class="nav-link" href="#section-2"
   title="Organisatorisches">Organisatorisches</a></li>
 <li class="nav-item" id="onetabid-58614"><a class="nav-link" href="#section-8"
   title="Lektion 1 - Einführung">Lektion 1 - Einführung</a></li>
</ul>
""" + _tabs_abschnitt(1, "58607", """
 <div class="summarytext">Einführung in Datenpipelines.</div>
 <ul class="section img-text">
  <li class="activity label modtype_label" id="module-501">
   <div class="contentwithoutlink"><div class="no-overflow">
    <p>Willkommen im Modul. Bitte zuerst die <a \
href="https://lehre.pages.dbis-pro1.fernuni-hagen.de/module/63123_data_engineering_for_data_science/docker-hadoop-cluster/">Infrastruktur-Seite</a> lesen.</p>
    <ul><li><strong>Hinweis:</strong> Jupyter nutzen</li><li>Zweiter Punkt</li></ul>
   </div></div></li>
 </ul>""") + "</body></html>"

# mit &section=2 geliefert (Abschnitt 2 ist ein reiner Link-Abschnitt)
ONETOPIC_SEC2 = _tabs_abschnitt(2, "58608", """
 <ul class="section img-text">
  <li class="activity url modtype_url" id="module-601" data-title="Organisatorisches Link">
   <a href="/mod/url/view.php?id=601"><span class="instancename">Organisatorisches \
<span class="accesshide " > Link</span></span></a></li>
 </ul>""")

# mit &section=8 geliefert: Label (Aufgabentext), assign mit Anhaengen, folder
ONETOPIC_SEC8 = _tabs_abschnitt(8, "58614", """
 <ul class="section img-text">
  <li class="activity label modtype_label" id="module-504">
   <div class="contentwithoutlink"><div class="no-overflow">
    <h3>Kurstext</h3>
    <p>Aufgabe: Laden Sie das <a \
href="https://lehre.pages.dbis-pro1.fernuni-hagen.de/module/63123_data_engineering_for_data_science/docker-hadoop-cluster/">Docker-Hadoop-Cluster</a> \
herunter und führen Sie das Notebook aus.</p>
    <p>Infrastruktur: <a href="/course/view.php?id=6556&amp;section=3">Abschnitt Infrastruktur</a> lesen.</p>
    <ul><li><strong>Hinweis:</strong> Jupyter nutzen</li><li>Zweiter Punkt</li></ul>
   </div></div></li>
  <li class="activity label modtype_label" id="module-505">
   <div class="contentwithoutlink"><div class="no-overflow"><h3>Übungsaufgaben</h3></div></div></li>
  <li class="activity label modtype_label" id="module-506">
   <div class="contentwithoutlink"><div class="no-overflow">   </div></div></li>
  <li class="activity label modtype_label" id="module-507">
   <div class="contentwithoutlink"><div class="no-overflow"><p>Bitte beachten Sie die Hinweise.</p></div></div></li>
  <li class="activity label modtype_label" id="module-508">
   <div class="contentwithoutlink"><div class="no-overflow"><p>Bitte beachten Sie die Hinweise.</p></div></div></li>
  <li class="activity assign modtype_assign" id="module-502" data-activityname="Übungsaufgabe - Lektion 1">
   <a href="/mod/assign/view.php?id=502"><span class="accesshide " > Aufgabe</span></a></li>
  <li class="activity folder modtype_folder" id="module-503">
   <a href="/mod/folder/view.php?id=503"><span class="accesshide " > Ordner</span></a></li>
 </ul>""")

ASSIGN_PAGE = """<html><head><title>Kurs: Data Engineering für Data Science: \
Übungsaufgabe - Lektion 1 | Moodle</title></head><body>
<div class="activity-header"><h2>Übungsaufgabe - Lektion 1</h2></div>
<div class="box generalbox"><p>Bitte reichen Sie die Notebook-Datei ein.</p>
<a href="/pluginfile.php/7777/mod_assign/introattachment/0/Einfuehrung_Jupyter_Notebook.ipynb?forcedownload=1">
 Einfuehrung_Jupyter_Notebook.ipynb</a>
<a href="/pluginfile.php/7777/mod_assign/introattachment/1/customers.csv?forcedownload=1">
 customers.csv</a></div>
<table class="generaltable"><tr><td class="cell c0">Fällig</td>
 <td class="cell c1">Freitag, 15. November 2026, 23:59</td></tr></table>
</body></html>"""

FOLDER_PAGE = """<html><head><title>Kurs: Data Engineering für Data Science: \
Übungsdateien Lektion 1 | Moodle</title></head><body>
<div class="filemanager">
<a href="/pluginfile.php/8888/mod_folder/content/0/uebung1.ipynb?forcedownload=1">
 uebung1.ipynb</a>
<a href="/pluginfile.php/8888/mod_folder/content/0/daten.csv?forcedownload=1">
 daten.csv</a>
</div></body></html>"""

# Gesperrter Ordner (Realitaet Kurs 6556, section=8, Modul 339098): der
# Modulblock der Kursseite, auf das Wesentliche gekuerzt. Der Sperrhinweis
# steht in ``class="activity-availability availabilityinfo isrestricted"``.
FOLDER_GESPERRT_BLOCK = """<li class="activity folder modtype_folder  hasinfo indented"
 id="module-339098" data-for="cmitem" data-id="339098">
 <div class="activity-item focus-control " data-activityname="Musterlösung - Lektion 1"
  data-region="activity-card">
  <div class="activity-altcontent text-break activity-description">
   <div class="no-overflow"><div class="no-overflow"><p>Jupyter Notebook und PDF mit der Musterlösung</p></div></div>
  </div>
  <div class="activity-availability availabilityinfo isrestricted" data-region="availabilityinfo">
   <div><i class="icon fa fa-lock fa-fw " aria-hidden="true" ></i> Verfügbar ab <strong>16. Oktober 2026</strong></div>
  </div>
 </div>
</li>"""

# Was Moodle liefert, wenn die gesperrte view.php direkt aufgerufen wird:
# Redirect auf die Kursseite mit Fehlermeldung (Klasse availabilityinfo-error),
# keine Dateiliste. Auszug aus dem echten Abruf von
# /mod/folder/view.php?id=339098 (Oktober 2026).
FOLDER_GESPERRT_REDIRECT = """<html><body><span class="notifications">
<div class="alert alert-danger alert-block fade in alert-dismissible" role="alert">
 <div class="activityinstance-error"><span class="instancename"> Musterlösung - Lektion 1<span class="accesshide"> Verzeichnis</span></span></div>
 <div class="availabilityinfo-error">Verfügbar ab <strong>16. Oktober 2026</strong></div>
</div></span></body></html>"""

# Kursseite mit genau diesem gesperrten Ordner (eine Tab-Leiste, ein Abschnitt).
GESPERRT_KURS = """<html><head><title>Kurs: Data Engineering für Data Science | \
Data Engineering für Data Science - WiSe 2026/27 | Moodle</title></head><body>
<ul class="nav nav-tabs">
 <li class="nav-item" id="onetabid-58614"><a class="nav-link" href="#section-8"
   title="Lektion 1 - Einführung">Lektion 1 - Einführung</a></li>
</ul>
<li id="section-8" class="section" data-id="58614">
 <h3 class="sectionname">Lektion 1 - Einführung</h3>
 <ul class="section img-text">
""" + FOLDER_GESPERRT_BLOCK + """
 </ul>
</li></body></html>"""


class FakeDav:
    """WebDAV-Attrappe mit In-Memory-Dateisystem."""

    def __init__(self, files=None, dry_run=False):
        self.files = dict(files or {})
        self.puts = []
        self.calls = 0
        self.dry_run = dry_run

    def get_text(self, path):
        self.calls += 1
        v = self.files.get(path)
        return v.decode("utf-8") if isinstance(v, bytes) else v

    def get(self, path):
        return (200, self.files[path]) if path in self.files else (404, b"")

    def put(self, path, data, content_type=None):
        self.calls += 1
        if self.dry_run:
            raise AssertionError("dry-run darf nicht schreiben!")
        self.files[path] = data
        self.puts.append(path)
        return 201

    def ensure_dir(self, path):
        return 201

    def head(self, path):
        self.calls += 1
        return (200, len(self.files[path]), None) if path in self.files else (404, None, None)

    def exists(self, path):
        return path in self.files


class FakeMoodle:
    """Moodle-Attrappe: liefert Fixture-HTML und 'Dateien' aus dem Speicher."""

    base = "https://moodle.fernuni-hagen.de"

    def __init__(self, pages, dateien, groesse=1782579):
        self.pages = pages
        self.dateien = dateien
        self.groesse = groesse
        self.verbose = False

    def text(self, path):
        url = sy.abs_url(self.base, path)
        if url in self.pages:                       # Fixture mit Query-String
            return self.pages[url]
        key = sy.norm_key(url)
        if key not in self.pages:
            raise sy.FetchError(f"kein Fixture fuer {url}")
        return self.pages[key]

    def probe(self, url):
        name = sy.url_filename(url) or "Folien-Intro.pdf"
        return {"final": url, "size": self.groesse, "etag": '"etag-1"',
                "ctype": "application/pdf",
                "disp": f'attachment; filename="{name}"'}

    def download(self, url, max_bytes, tmpdir):
        import hashlib
        fd, path = tempfile.mkstemp(dir=tmpdir, suffix=".part")
        with os.fdopen(fd, "wb") as fh:
            fh.write(b"%PDF-1.4 fake")
        return {"status": "ok", "size": self.groesse, "path": path, "ctype": "application/pdf",
                "disp": "", "final": url,
                "sha256": hashlib.sha256(b"%PDF-1.4 fake").hexdigest(), "etag": '"etag-1"'}


def args(**kw):
    d = {"dry_run": False, "limit_sections": None, "no_material": False, "verbose": False}
    d.update(kw)
    return types.SimpleNamespace(**d)


def txt(dav, path):
    """Inhalt aus der FakeDav (bytes oder str) als Text."""
    v = dav.files[path]
    return v.decode("utf-8") if isinstance(v, bytes) else v


# --------------------------------------------------------------------------- #
def test_parser():
    print("Parser")
    c = mp.parse_course(ONETOPIC)
    check("onetopic erkannt", c["format"] == "onetopic", c["format"])
    sec = next((s for s in c["sections"] if s["number"] == 3), None)
    check("Abschnitt 3 vorhanden", sec is not None)
    check("Abschnittsname", sec and sec["name"] == "Data Engineering für Data Science",
          sec and sec["name"])
    check("genau 2 Aktivitaeten", sec and len(sec["activities"]) == 2,
          sec and len(sec["activities"]))
    typen = [a["type"] for a in sec["activities"]]
    namen = [a["name"] for a in sec["activities"]]
    check("Typen helixmedia+resource", typen == ["helixmedia", "resource"], typen)
    check("accesshide-Label ('class=\"accesshide \" >') entfernt",
          namen == ["Intro (Video)", "Folien-Intro.pdf"], namen)
    check("Groessendetail 1.7 MB", sec["activities"][1]["details"] == "1.7 MB",
          sec["activities"][1]["details"])
    check("Zusammenfassung", sec.get("summary") == "Einführung in Datenpipelines.",
          sec.get("summary"))

    t = mp.parse_course(TILES)
    check("tiles erkannt", t["format"] == "tiles", t["format"])
    check("Kachelname", t["sections"][0]["name"] == "Überwachtes Lernen",
          t["sections"][0]["name"])
    check("Kachel-sectionid", t["sections"][0]["id"] == "70004", t["sections"][0]["id"])
    check("tiles: Aktivitaeten erst per section.php", t["sections"][0]["activities"] is None)
    s2 = mp.parse_section_body(TILES_SECTION, number=4, section_id="70004")
    check("section.php: Name aus h3", s2["name"] == "Überwachtes Lernen", s2["name"])
    check("section.php: 1 Aktivitaet (folder)", len(s2["activities"]) == 1,
          len(s2["activities"]))
    check("section.php: Zusammenfassung", s2["summary"] == "Regression und Klassifikation.",
          s2["summary"])
    f = mp.parse_folder(FOLDER)
    check("parse_folder: 1 Datei mit Query-URL", len(f) == 1 and "pluginfile.php" in f[0][0], f)
    check("helper: data-title", sy.activity_titles([ONETOPIC]).get("101") == "Intro (Video) MEDIAL")
    check("helper: disp_filename", sy.disp_filename('attachment; filename="a b.pdf"') == "a b.pdf")


def test_slug():
    print("Slug-/Dateinamenregeln")
    # Typ-Label, das die Instanz an Aktivitaetsnamen anhaengt
    check("Typ-Label 'Datei' entfernt",
          ob.strip_activity_label("Folien-Intro.pdf Datei", "resource") == "Folien-Intro.pdf",
          ob.strip_activity_label("Folien-Intro.pdf Datei", "resource"))
    check("Typ-Label 'MEDIAL' entfernt",
          ob.strip_activity_label("Intro (Video) MEDIAL", "helixmedia") == "Intro (Video)",
          ob.strip_activity_label("Intro (Video) MEDIAL", "helixmedia"))
    check("ohne Label unveraendert",
          ob.strip_activity_label("Übungsquiz 1", "quiz") == "Übungsquiz 1")
    check("Typ-Label 'Kurstext, PDF' entfernt (resource_pdf)",
          ob.strip_activity_label("Kapitel 1 Kurstext, PDF", "resource_pdf") == "Kapitel 1",
          ob.strip_activity_label("Kapitel 1 Kurstext, PDF", "resource_pdf"))
    # Typ-Wort nur bei uneindeutigem Icon; resource/helixmedia/folder ohne Zusatz
    check("Quiz-Zeile mit Typ-Wort",
          ob.activity_line({"type": "quiz", "name": "Übungsquiz 1"}, "u")
          == "- \u270f\ufe0f **Übungsquiz 1** *(Quiz)* \u00b7 [In Moodle \u00f6ffnen](u)",
          ob.activity_line({"type": "quiz", "name": "Übungsquiz 1"}, "u"))
    check("resource/helixmedia/folder ohne Typ-Wort",
          ob.activity_line({"type": "resource", "name": "a.pdf"}, "u")
          == "- \U0001f4c4 **a.pdf** \u00b7 [In Moodle \u00f6ffnen](u)"
          and "*(helixmedia)*" not in ob.activity_line({"type": "helixmedia", "name": "V"}, "u")
          and "*(folder)*" not in ob.activity_line({"type": "folder", "name": "F"}, "u"))
    # accesshide-Label auch bei resource_pdf (format_tiles, Kurs 6591) entfernen
    tiles_pdf = ('<ul><li class="activity resource modtype_resource_pdf" id="module-401" '
                 'data-title="Kapitel 1 (PDF)"><a href="/mod/resource/view.php?id=401">'
                 '<span class="instancename">Kapitel 1 <span class="accesshide " > Kurstext, PDF'
                 '</span></span></a></li></ul>')
    tp = mp.parse_activities(tiles_pdf)[0]
    check("tiles: resource_pdf erkannt", tp["type"] == "resource_pdf", tp["type"])
    check("tiles: Label 'Kurstext, PDF' entfernt", tp["name"] == "Kapitel 1", tp["name"])
    check("Zusammenfassung: Absaetze bleiben erhalten",
          mp.clean_absaetze("<p>Absatz eins.</p><p>Absatz zwei.</p>")
          == "Absatz eins.\n\nAbsatz zwei.",
          mp.clean_absaetze("<p>Absatz eins.</p><p>Absatz zwei.</p>"))
    # Vorgabe: äöüÄÖÜ -> ae/oe/ue/ae/oe/ue/ss (auch fuer Umlaute in Grossschreibung)
    check("Umlaute", ob.slug("Überwachtes Lernen") == "ueberwachtes-Lernen",
          ob.slug("Überwachtes Lernen"))
    check("Umlaute im Wortinneren", ob.slug("Einführung") == "Einfuehrung",
          ob.slug("Einführung"))
    check("Sonderzeichen", ob.slug("Was? Ist: das/so!") == "Was-Ist-das-so",
          ob.slug("Was? Ist: das/so!"))
    check("max 60 Zeichen", len(ob.slug("x" * 200)) == 60)
    check("leerer Name -> Fallback", ob.slug("###", fallback="abschnitt-03") == "abschnitt-03")
    check("section_slug", ob.section_slug(3, "Data Engineering für Data Science")
          == "03-Data-Engineering-fuer-Data-Science",
          ob.section_slug(3, "Data Engineering für Data Science"))
    check("section_slug ohne Namen", ob.section_slug(7, "") == "07-abschnitt-07",
          ob.section_slug(7, ""))
    check("Dateiname: Endung bleibt, Punkte bleiben (Vorgabe-Zeichenklasse)",
          ob.safe_existing_filename("2.1 Lineare Regression (PDF).PDF")
          == "2.1-Lineare-Regression-PDF.pdf",
          ob.safe_existing_filename("2.1 Lineare Regression (PDF).PDF"))
    check("Kollision -2", ob.safe_existing_filename("x.pdf") == "x.pdf")
    used = set()
    a = ob.resolve_collision(used, "x.pdf")
    b = ob.resolve_collision(used, "x.pdf")
    check("Kollision -> x-2.pdf", (a, b) == ("x.pdf", "x-2.pdf"), (a, b))
    check("generated_path", ob.generated_path("a/b.md") == "a/b.generated-2.md")
    check("wikilink kodiert", ob.wikilink("a b/Ü.pdf", "Datei")
          == "[[a%20b/%C3%9C.pdf|Datei]]", ob.wikilink("a b/Ü.pdf", "Datei"))
    check("Icons", (ob.icon_for("helixmedia"), ob.icon_for("resource"), ob.icon_for("x"))
          == ("\U0001f3ac", "\U0001f4c4", "\u2753"))
    check("fmt_size", ob.fmt_size(int(1.7 * 1024 ** 2)) == "1.7 MB",
          ob.fmt_size(int(1.7 * 1024 ** 2)))


def test_notiz():
    print("Notiz-Rendering")
    c = mp.parse_course(ONETOPIC)
    sec = next(s for s in c["sections"] if s["number"] == 3)
    a0 = dict(sec["activities"][0],
              name=ob.strip_activity_label(sec["activities"][0]["name"], "helixmedia"))
    a1 = dict(sec["activities"][1],
              name=ob.strip_activity_label(sec["activities"][1]["name"], "resource"))
    zeilen = [ob.activity_line(a0,
                               "https://moodle.fernuni-hagen.de/mod/helixmedia/view.php?id=101"),
              ob.activity_line(a1,
                               "https://moodle.fernuni-hagen.de/mod/resource/view.php?id=102",
                               "1.7 MB", ob.wikilink(PDF, label="Datei"))]
    text = ob.section_note(MODUL, sec, sec["activities"], None, sec.get("summary"),
                           "https://moodle.fernuni-hagen.de/course/section.php?id=58900",
                           "2026-10-15", "2026-10-15T14:22:10", f"{BASIS}/Material/03-x", zeilen)
    for erwartet in ('title: "Data Engineering für Data Science"', "  - moodle",
                     "  - generated", "  - \"modul/63123\"",
                     "created: \"2026-10-15\"", "updated: \"2026-10-15T14:22:10\"",
                     "status: \"aktiv\"", "typ: \"moodle-abschnitt\"", "generated: true",
                     'moodle_course: "6556"', 'moodle_section: "3"',
                     "NICHT BEARBEITEN", "## Zusammenfassung", "## Inhalte",
                     "> [!quote]- Zusammenfassung aus Moodle",
                     "> Einführung in Datenpipelines."):
        check(f"enthaelt {erwartet[:34]!r}", erwartet in text)
    check("Titel ohne Kursnamen-Dopplung (Abschnittsname == Kursname)",
          'title: "Data Engineering für Data Science"' in text
          and "Data Science – Data Science" not in text)
    check("Videozeile: Icon + Name, kein Modultyp-Zusatz",
          "- \U0001f3ac **Intro (Video)** \u00b7 " in text
          and "(helixmedia)" not in text
          and "[In Moodle \u00f6ffnen](https://moodle.fernuni-hagen.de/mod/helixmedia/view.php?id=101)"
          in text)
    check("PDF-Zeile: Name pur, Groesse/Wikilink/Datei",
          "**Folien-Intro.pdf**" in text and "(Kurstext, PDF)" not in text
          and "(resource" not in text and "`1.7 MB`" in text
          and f"\u2b07 [[{PDF}|Datei]]" in text and f"[Moodle](https://moodle.fernuni-hagen.de"
          in text)
    idx = ob.index_note(MODUL, c["title"], [{"number": 3, "name": sec["name"], "acts": 2,
                                             "files": 1, "note_path": NOTIZ,
                                             "created": "2026-10-15"}],
                        "2026-10-15T14:22:10", 2, 1, f"{BASIS}/Material")
    check("Index: Wikilink auf Abschnittsnotiz",
          f"[[{NOTIZ}|Data Engineering für Data Science]]" in idx)
    check("Index: Kennzahlen", "Aktivitäten: 2" in idx and "lokal vorhandene Dateien: 1" in idx)
    fm = ob.parse_frontmatter(text)
    check("Frontmatter lesbar", fm.get("generated") == "true" and fm.get("created") == "2026-10-15")
    check("is_generated", ob.is_generated(text) and not ob.is_generated("# nur Notizen"))


def test_dav():
    print("WebDAV-Pfade")
    d = Dav(space="8da8246b$de5001bf", user="u", token="t", dry_run=True)
    u = d.url("10-Semester/1-Semester/Ü-Lernen/Material/03-x/2-1 lineare.pdf")
    check("Space-ID unkodiert ($ erhalten)", "8da8246b$de5001bf" in d.space_url(), d.space_url())
    check("Umlaute kodiert", "%C3%9C" in u, u)
    check("Leerzeichen kodiert", "%20" in u and " " not in u, u)
    check("Segmenttrenner erhalten", u.count("/") >= 5, u)
    check("dry-run: PUT schreibt nicht", d.put("a.md", b"x") == 200)
    check("dry-run: MKCOL schreibt nicht", d.mkcol("a/b") == 405)


def test_state():
    print("State/Idempotenz")
    d = tempfile.mkdtemp()
    p = os.path.join(d, "sub", "state.json")
    st = {"version": 1, "material": {PDF: {"moodle_size": 1782579, "etag": '"e1"'}},
          "notes": {NOTIZ: {"created": "2026-10-15"}}}
    sy.save_state(p, st)
    st2 = sy.load_state(p)
    check("State atomar geschrieben + gelesen", st2["material"][PDF]["moodle_size"] == 1782579)
    check("State: unbekannter Pfad -> nicht ueberspringen",
          not sy.ist_unveraendert(None, 1782579, '"e1"'))
    check("State: gleiche Groesse+ETag -> unveraendert",
          sy.ist_unveraendert(st["material"][PDF], 1782579, '"e1"'))
    check("State: andere Groesse -> laden",
          not sy.ist_unveraendert(st["material"][PDF], 1000, '"e1"'))
    check("State: anderes ETag -> laden",
          not sy.ist_unveraendert(st["material"][PDF], 1782579, '"e2"'))
    check("State: ohne ETag -> Groesse entscheidet",
          sy.ist_unveraendert(st["material"][PDF], 1782579, None))
    shutil.rmtree(d)


def test_lauf():
    print("Kompletter Kurslauf (Attrappen)")
    pages = {sy.norm_key("https://moodle.fernuni-hagen.de/course/view.php?id=6556"): ONETOPIC}
    st = {"version": 1, "material": {}, "notes": {}}
    tmpdir = tempfile.mkdtemp()
    dav1 = FakeDav()
    k1 = sy.Kurs(MODUL, args(), FakeMoodle(pages, {}), dav1, st, tmpdir)
    s1 = k1.run()
    check("1. Lauf: 2 Abschnitte", s1["abschnitte"] == 2, s1["abschnitte"])
    check("1. Lauf: 1 Datei geladen", s1["dateien"] == 1, s1["dateien"])
    check("1. Lauf: keine Fehler", not s1["fehler"], s1["fehler"])
    check("Notiz im Vault", NOTIZ in dav1.files, sorted(dav1.files))
    check("PDF im Vault", PDF in dav1.files, sorted(dav1.files))
    check("Index im Vault", f"{BASIS}/_Moodle-Index.md" in dav1.files)
    check("State: PDF eingetragen", PDF in st["material"]
          and st["material"][PDF]["etag"] == '"etag-1"'
          and st["material"][PDF]["moodle_size"] == 1782579, st["material"].get(PDF))
    check("Widget-Namen (FakeMoodle liefert Groesse)",
          "`1.7 MB`" in txt(dav1, NOTIZ), txt(dav1, NOTIZ)[:200])
    check("created gesetzt", "created:" in txt(dav1, NOTIZ))
    created1 = ob.parse_frontmatter(txt(dav1, NOTIZ)).get("created")

    dav2 = FakeDav(dav1.files)                      # gleicher Vault-Stand
    k2 = sy.Kurs(MODUL, args(), FakeMoodle(pages, {}), dav2, st, tmpdir)
    s2 = k2.run()
    check("2. Lauf: 0 Downloads", s2["dateien"] == 0, s2["dateien"])
    check("2. Lauf: 1 uebersprungen", s2["uebersprungen"] == 1, s2["uebersprungen"])
    check("2. Lauf: PDF nicht angefasst", PDF not in dav2.puts, dav2.puts)
    check("2. Lauf: created stabil",
          ob.parse_frontmatter(txt(dav2, NOTIZ)).get("created") == created1)

    dav3 = FakeDav(dav1.files, dry_run=True)
    k3 = sy.Kurs(MODUL, args(dry_run=True), FakeMoodle(pages, {}), dav3, st, tmpdir)
    s3 = k3.run()
    check("dry-run: 0 Downloads", s3["dateien"] == 0, s3["dateien"])
    check("dry-run: keine Schreibzugriffe", not dav3.puts, dav3.puts)

    st_nom = {"version": 1, "material": {}, "notes": {}}
    dav4 = FakeDav()
    k4 = sy.Kurs(MODUL, args(no_material=True), FakeMoodle(pages, {}), dav4, st_nom, tmpdir)
    s4 = k4.run()
    check("--no-material: keine Datei geladen", s4["dateien"] == 0 and PDF not in dav4.files)

    # Nutzerdatei darf nicht ueberschrieben werden
    st_n = {"version": 1, "material": {}, "notes": {}}
    dav_n = FakeDav({NOTIZ: "---\ntitle: eigene Notiz\ngenerated: false\n---\nInhalt"})
    k_n = sy.Kurs(MODUL, args(), FakeMoodle(pages, {}), dav_n, st_n, tmpdir)
    k_n.run()
    check("Nutzer-Notiz unangetastet",
          txt(dav_n, NOTIZ).startswith("---\ntitle: eigene Notiz"), txt(dav_n, NOTIZ)[:40])
    check("Nutzer-Notiz -> .generated-2.md", f"{BASIS}/03-Data-Engineering-fuer-Data-Science.generated-2.md" in dav_n.files)

    # Fehler in einem Abschnitt/einer Aktivitaet bricht den Lauf nicht ab
    class KaputteQuelle(FakeMoodle):
        def probe(self, url):
            raise sy.FetchError("Netz weg beim Aufloesen der Datei")

    st_f = {"version": 1, "material": {}, "notes": {}}
    dav_f = FakeDav()
    k_f = sy.Kurs(MODUL, args(), KaputteQuelle(pages, {}), dav_f, st_f, tmpdir)
    s_f = k_f.run()
    check("Materialfehler beendet Kurs nicht",
          s_f["abschnitte"] == 2 and len(s_f["fehler"]) == 1,
          (s_f["abschnitte"], s_f["fehler"]))
    check("Materialfehler: Notiz trotzdem geschrieben, Datei nicht",
          NOTIZ in dav_f.files and PDF not in dav_f.files)
    check("Materialfehler: Notiz verlinkt Moodle statt Datei",
          "[In Moodle \u00f6ffnen]" in txt(dav_f, NOTIZ))
    check("Materialfehler: 2. Abschnitt weiterhin verarbeitet",
          f"{BASIS}/04-ueberwachtes-Lernen.md" in dav_f.files, sorted(dav_f.files))

    class KaputteTiles(FakeMoodle):
        def text(self, path):
            if "section.php" in path:
                raise sy.FetchError("section.php nicht erreichbar")
            return FakeMoodle.text(self, path)

    pages_t = {sy.norm_key("https://moodle.fernuni-hagen.de/course/view.php?id=6556"): TILES}
    st_t = {"version": 1, "material": {}, "notes": {}}
    dav_t = FakeDav()
    k_t = sy.Kurs(MODUL, args(), KaputteTiles(pages_t, {}), dav_t, st_t, tmpdir)
    s_t = k_t.run()
    check("tiles: kaputter Abschnitt beendet Kurs nicht",
          s_t["abschnitte"] == 0 and len(s_t["fehler"]) == 1 and not dav_t.puts,
          (s_t["abschnitte"], s_t["fehler"]))
    shutil.rmtree(tmpdir)


def test_onetopic_tabs():
    print("onetopic mit Tabs: jeder Abschnitt einzeln (assign/folder/label)")
    b = FakeMoodle.base
    pages = {
        f"{b}/course/view.php?id=6556": ONETOPIC_TABS,
        f"{b}/course/view.php?id=6556&section=1": ONETOPIC_TABS,
        f"{b}/course/view.php?id=6556&section=2": ONETOPIC_SEC2,
        f"{b}/course/view.php?id=6556&section=8": ONETOPIC_SEC8,
        f"{b}/mod/assign/view.php?id=502": ASSIGN_PAGE,
        f"{b}/mod/folder/view.php?id=503": FOLDER_PAGE,
    }
    # Parser-Bausteine
    tabs = mp.parse_tabs(ONETOPIC_TABS)
    check("parse_tabs: Reihenfolge + Titel", tabs == [("58607", "Data Engineering für Data Science"),
                                                     ("58608", "Organisatorisches"),
                                                     ("58614", "Lektion 1 - Einführung")], tabs)
    blk = mp.parse_onetopic_blocks(ONETOPIC_SEC8)
    check("parse_onetopic_blocks: Nummer + data-id",
          [(n, s) for n, s, _ in blk] == [(0, "58606"), (8, "58614")],
          [(n, s) for n, s, _ in blk])
    att = mp.parse_assign_attachments(ASSIGN_PAGE)
    check("parse_assign_attachments: 2 Anhaenge",
          [n for _, n in att] == ["Einfuehrung_Jupyter_Notebook.ipynb", "customers.csv"], att)
    check("assign_due: aus dem Seitentext", mp.assign_due(ASSIGN_PAGE)
          == "Freitag, 15. November 2026, 23:59", mp.assign_due(ASSIGN_PAGE))
    check("assign_due: ohne Termin -> None", mp.assign_due("<p>kein Termin</p>") is None)
    check("module_page_names: Name aus dem Titel",
          "Übungsdateien Lektion 1" in mp.module_page_names(FOLDER_PAGE),
          mp.module_page_names(FOLDER_PAGE))
    lab = [a for a in mp.parse_activities(ONETOPIC_SEC8) if a["type"] == "label"][0]
    check("label: content_html geparst", bool(lab.get("content_html")), lab.get("content_html"))
    md = ob.html_to_markdown(lab["content_html"], "https://moodle.fernuni-hagen.de")
    check("html_to_markdown: echte Ueberschrift bleibt", md.startswith("### Kurstext"), md)
    check("html_to_markdown: Link + Absatz", "[Docker-Hadoop-Cluster](https://lehre.pages."
          "dbis-pro1.fernuni-hagen.de/module/63123_data_engineering_for_data_science/"
          "docker-hadoop-cluster/) herunter und führen Sie das Notebook aus." in md, md)
    check("html_to_markdown: kein HTML durchgereicht", "<" not in md, md)
    check("html_to_markdown: &amp; genau einmal unescaped",
          "[Abschnitt Infrastruktur](https://moodle.fernuni-hagen.de/course/view.php"
          "?id=6556&section=3) lesen." in md and "§ion" not in md
          and "&amp;" not in md, md)
    check("label_block: keine erzeugte Ueberschrift, kein Abschneiden",
          ob.label_block("Fließtext ohne Überschrift")
          == ["", "Fließtext ohne Überschrift", ""]
          and "…" not in "".join(ob.label_block("x" * 200)),
          ob.label_block("Fließtext ohne Überschrift"))
    check("label_block: leeres Label -> leerer Block", ob.label_block("   ") == [])

    mat = f"{BASIS}/Material"
    m1 = f"{mat}/01-Data-Engineering-fuer-Data-Science"
    m8 = f"{mat}/08-Lektion-1-Einfuehrung"
    n1 = f"{BASIS}/01-Data-Engineering-fuer-Data-Science.md"
    n2 = f"{BASIS}/02-Organisatorisches.md"
    n8 = f"{BASIS}/08-Lektion-1-Einfuehrung.md"
    st = {"version": 1, "material": {}, "notes": {}}
    tmpdir = tempfile.mkdtemp()
    dav = FakeDav()
    s = sy.Kurs(MODUL, args(), FakeMoodle(pages, {}), dav, st, tmpdir).run()
    check("onetopic: 3 Abschnitte (nicht nur der aktive)", s["abschnitte"] == 3, s["abschnitte"])
    check("onetopic: keine Fehler", not s["fehler"], s["fehler"])
    check("Notizen 01/02/08 geschrieben", all(p in dav.files for p in (n1, n2, n8)),
          sorted(dav.files))
    t1 = txt(dav, n1)
    check("Label-Text steht in Abschnitt 1 (ohne erzeugte Ueberschrift)",
          "Willkommen im Modul" in t1 and "- **Hinweis:** Jupyter nutzen" in t1
          and "### Willkommen" not in t1, t1[:300])
    check("Label: Link bleibt klickbarer Markdown-Link",
          "[Infrastruktur-Seite](https://lehre.pages.dbis-pro1.fernuni-hagen.de/module/"
          "63123_data_engineering_for_data_science/docker-hadoop-cluster/)" in t1, t1)
    check("Label: kein rohes HTML in der Notiz", "<p>" not in t1 and "<div" not in t1)
    t8 = txt(dav, n8)
    check("assign: Name", "**Übungsaufgabe - Lektion 1**" in t8, t8[:400])
    check("assign: Faelligkeit in der Notiz",
          "Fällig: Freitag, 15. November 2026, 23:59" in t8, t8[:600])
    check("assign: Anhang notebook als lokaler Wikilink",
          ob.wikilink(f"{m8}/Einfuehrung_Jupyter_Notebook.ipynb", "Datei") in t8, t8)
    check("assign: Anhang csv als lokaler Wikilink",
          ob.wikilink(f"{m8}/customers.csv", "Datei") in t8, t8)
    check("assign: Anhaenge im Vault",
          f"{m8}/Einfuehrung_Jupyter_Notebook.ipynb" in dav.files
          and f"{m8}/customers.csv" in dav.files, sorted(dav.files))
    check("folder: Dateien im Vault", f"{m8}/uebung1.ipynb" in dav.files
          and f"{m8}/daten.csv" in dav.files, sorted(dav.files))
    check("folder: Name aus dem Seitentitel", "**Übungsdateien Lektion 1**" in t8, t8[:600])
    check("Abschnitt 8: keine leere/doppelte/abgeschnittene Ueberschrift",
          t8.count("### Übungsaufgaben") == 1 and "### Kurstext" in t8
          and "### Aufgabe: Laden Sie das" not in t8
          and "### \n" not in t8 and "\u2026" not in t8, t8)
    check("Abschnitt 8: inhaltsgleiches Folge-Label nur einmal",
          t8.count("Bitte beachten Sie die Hinweise.") == 1, t8)
    check("Abschnitt 8: &amp;-URL exakt einmal unescaped",
          "?id=6556&section=3" in t8 and "\u00a7ion" not in t8, t8)
    check("1. Lauf: 4 Dateien (2 assign, 2 folder)", s["dateien"] == 4, s["dateien"])
    check("Material nur fuer Abschnitte mit Dateien",
          not any(p.startswith(m1 + "/") for p in dav.files), sorted(dav.files))
    dav2 = FakeDav(dav.files)
    s2 = sy.Kurs(MODUL, args(), FakeMoodle(pages, {}), dav2, st, tmpdir).run()
    check("2. Lauf: 0 Downloads", s2["dateien"] == 0, s2["dateien"])
    check("2. Lauf: 4 unveraendert", s2["uebersprungen"] == 4, s2["uebersprungen"])
    shutil.rmtree(tmpdir)


def test_material_kollision():
    """Frischer State + Datei schon im Vault: uebernehmen/ueberschreiben, nie duplizieren.

    Sichert den Bugfix gegen den Fehlerfall ab, bei dem der Sync mit frischem
    State (neuer Coder-Workspace) fuer JEDE vorhandene Material-Datei eine
    "-generated-2"-Kopie anlegte (186 Duplikate im FernUni-Vault).
    """
    print("Material-Kollision (Datei im Vault, nicht im State)")
    pages = {sy.norm_key("https://moodle.fernuni-hagen.de/course/view.php?id=6556"): ONETOPIC}
    tmpdir = tempfile.mkdtemp()
    groesse = 1782579                       # Moodle-Groesse der Fake-Datei

    # (a) Vault-Datei ohne State-Eintrag, gleiche Groesse -> uebernehmen
    st1 = {"version": 1, "material": {}, "notes": {}}
    dav1 = FakeDav({PDF: b"x" * groesse})
    k1 = sy.Kurs(MODUL, args(), FakeMoodle(pages, {}), dav1, st1, tmpdir)
    s1 = k1.run()
    check("Uebernahme: 0 Downloads", s1["dateien"] == 0, s1["dateien"])
    check("Uebernahme: 1 uebersprungen", s1["uebersprungen"] == 1, s1["uebersprungen"])
    e1 = st1["material"].get(PDF)
    check("Uebernahme: State-Eintrag angelegt",
          bool(e1) and "source" in e1 and e1.get("moodle_size") == groesse
          and e1.get("sha256") is None and e1.get("etag") == '"etag-1"'
          and bool(e1.get("updated")), e1)
    check("Uebernahme: Datei unveraendert",
          dav1.files.get(PDF) == b"x" * groesse, len(dav1.files.get(PDF) or b""))
    check("Uebernahme: keine -generated-2-Datei",
          not any("-generated-2" in p for p in dav1.files), sorted(dav1.files))
    check("Uebernahme: Notiz verlinkt die vorhandene Datei",
          ob.wikilink(PDF, label="Datei") in txt(dav1, NOTIZ), txt(dav1, NOTIZ)[-300:])
    dav2 = FakeDav(dav1.files)
    s2 = sy.Kurs(MODUL, args(), FakeMoodle(pages, {}), dav2, st1, tmpdir).run()
    check("Uebernahme: 2. Lauf 0 Downloads", s2["dateien"] == 0, s2["dateien"])
    check("Uebernahme: 2. Lauf PDF nicht angefasst", PDF not in dav2.puts, dav2.puts)
    check("Uebernahme: 2. Lauf keine -generated-2-Datei",
          not any("-generated-2" in p for p in dav2.files), sorted(dav2.files))

    # (b) Vault-Datei ohne State-Eintrag, andere Groesse -> ueberschreiben
    st3 = {"version": 1, "material": {}, "notes": {}}
    dav3 = FakeDav({PDF: b"alter Inhalt"})
    k3 = sy.Kurs(MODUL, args(), FakeMoodle(pages, {}), dav3, st3, tmpdir)
    s3 = k3.run()
    check("Ueberschreiben: 1 Download", s3["dateien"] == 1, s3["dateien"])
    check("Ueberschreiben: PDF neu geschrieben", PDF in dav3.puts, dav3.puts)
    check("Ueberschreiben: genau eine Datei am Zielpfad",
          [p for p in dav3.files
           if p.startswith(f"{BASIS}/Material/03-Data-Engineering-fuer-Data-Science/")] == [PDF],
          sorted(dav3.files))
    check("Ueberschreiben: keine -generated-2-Datei",
          not any("-generated-2" in p for p in dav3.files), sorted(dav3.files))
    check("Ueberschreiben: keine Warnung mehr",
          not any("WARNUNG" in f for f in s3["fehler"]), s3["fehler"])
    shutil.rmtree(tmpdir)


def test_gesperrter_ordner():
    """Verfuegbarkeitsbedingung: kein Abruf der gesperrten Seite, klarer Hinweis.

    Realfall Kurs 6556: alle sechs 'Musterloesung - Lektion N'-Ordner sind bis
    zum jeweiligen Freigabedatum gesperrt; /mod/folder/view.php?id=<cmid> leitet
    auf die Kursseite um. Vor dem Fix lief trotzdem ein Abruf und der Lauf
    meldete "! Ordner '...' ohne Dateiliste".
    """
    print("Gesperrter Ordner (Verfuegbarkeitsbedingung)")
    import contextlib
    import io

    acts = mp.parse_activities(FOLDER_GESPERRT_BLOCK)
    check("Sperrhinweis am Modul erkannt",
          len(acts) == 1 and acts[0].get("availability") == "Verfügbar ab 16. Oktober 2026",
          acts)
    check("gesperrte view.php liefert keine Dateiliste",
          mp.parse_folder(FOLDER_GESPERRT_REDIRECT) == [],
          mp.parse_folder(FOLDER_GESPERRT_REDIRECT))

    class ZaehlendesFakeMoodle(FakeMoodle):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.abrufe = []

        def text(self, path):
            self.abrufe.append(path)
            return FakeMoodle.text(self, path)

    b = FakeMoodle.base
    pages = {sy.norm_key(f"{b}/course/view.php?id=6556"): GESPERRT_KURS,
             f"{b}/mod/folder/view.php?id=339098": FOLDER_GESPERRT_REDIRECT}
    st = {"version": 1, "material": {}, "notes": {}}
    tmpdir = tempfile.mkdtemp()
    dav = FakeDav()
    fm = ZaehlendesFakeMoodle(pages, {})
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s = sy.Kurs(MODUL, args(verbose=True), fm, dav, st, tmpdir).run()
    out = buf.getvalue()
    check("keine Fehler, nichts heruntergeladen",
          not s["fehler"] and s["dateien"] == 0, (s["fehler"], s["dateien"]))
    check("gesperrte Seite wird gar nicht abgerufen",
          not any("/mod/folder/" in p for p in fm.abrufe), fm.abrufe)
    check("keine irrefuehrende 'ohne Dateiliste'-Meldung",
          "ohne Dateiliste" not in out, out[-400:])
    note = f"{BASIS}/08-Lektion-1-Einfuehrung.md"
    t = txt(dav, note)
    check("Sperrhinweis in der Notiz",
          "\U0001f512 Verfügbar ab 16. Oktober 2026" in t, t[-400:])
    check("gesperrter Ordner bleibt verlinkt",
          "[In Moodle \u00f6ffnen](https://moodle.fernuni-hagen.de/mod/folder/view.php?id=339098)"
          in t, t[-400:])
    check("kein Material-Baum fuer gesperrte Ordner",
          not any("/Material/" in p for p in dav.files), sorted(dav.files))
    shutil.rmtree(tmpdir)


def test_auth():
    print("Auth-/Formatpruefung")
    try:
        sy.pruefen_kursseite(
            '<html><title>Identity Provider der FernUniversität in Hagen</title>'
            '<form action="/idp/profile/SAML2/Redirect/SSO"><input name="j_username"></form>',
            "6556")
        check("IdP-Seite wird erkannt", False)
    except sy.AuthError as e:
        check("IdP-Seite wird erkannt", "SAML" in str(e))
    try:
        sy.pruefen_kursseite('<form><input name="logintoken" value="x">'
                             '<a href="/login/index.php">Anmelden</a></form>', "6556")
        check("Anmeldeseite wird erkannt", False)
    except sy.AuthError:
        check("Anmeldeseite wird erkannt", True)
    try:
        sy.pruefen_kursseite("<html>leer</html>", "6556")
        check("fehlende Abschnitte -> Fehler", False)
    except sy.AuthError:
        check("fehlende Abschnitte -> Fehler", True)
    sy.pruefen_kursseite(ONETOPIC, "6556")
    check("echte Kursseite ohne Fehler", True)


def test_cli():
    print("CLI/Secrets")
    a = sy.parse_args(["--only", "6556", "--dry-run", "--verbose"])
    check("--only/--dry-run/--verbose", a.only == ["6556"] and a.dry_run and a.verbose)
    a2 = sy.parse_args(["--only", "1", "--only", "2", "--limit-sections", "1", "--no-material"])
    check("mehrfaches --only + Limit", a2.only == ["1", "2"] and a2.limit_sections == 1)
    os.environ.pop("MD_USER", None)
    bez = {k: os.environ.get(k) for k in sy.ENV_REQUIRED}
    for k in sy.ENV_REQUIRED:
        os.environ.pop(k, None)
    check("fehlende Secrets -> Exit 2", sy.main(["--dry-run"]) == 2)
    check("unbekannte Kurs-ID -> Exit 2",
          sy.main(["--only", "9999", "--dry-run",
                   "--config", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                            "config.json")]) == 2)
    for k, v in bez.items():
        if v is not None:
            os.environ[k] = v
    check("ungueltiges Limit -> Exit 2",
          sy.main(["--limit-sections", "0", "--dry-run"]) == 2)


if __name__ == "__main__":
    for t in (test_parser, test_slug, test_notiz, test_dav, test_state, test_lauf,
              test_onetopic_tabs, test_material_kollision, test_gesperrter_ordner,
              test_auth, test_cli):
        t()
    print(f"\n{OK[0]} Pruefungen ok, {len(FEHLER)} fehlgeschlagen")
    if FEHLER:
        for f in FEHLER:
            print(f"  FAIL {f}")
        sys.exit(1)
    print("selftest: alles gruen")
