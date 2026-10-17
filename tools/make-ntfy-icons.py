#!/usr/bin/env python3
"""Erzeugt die ntfy-Icons (PNG) für alle Workspaces und für Hermes.

Die Icons werden im Repo abgelegt und von ntfy über eine öffentliche
Roh-URL geladen, damit auf dem Handy sofort erkennbar ist, woher eine
Meldung kommt:

    https://raw.githubusercontent.com/DasCarstel/opencode-coder-env/master/assets/ntfy-icons/<name>.png

Jedes Icon ist ein abgerundetes Quadrat in der Workspace-Farbe mit dünnem
hellem Rand; darin liegt ein **weißes Piktogramm** aus einfachen
geometrischen Formen. Bewusst keine Buchstaben, keine Ziffern und keine
Sonderzeichen — Kürzel wie "SM" oder "HA" sind nicht selbsterklärend.

Sieben Motive werden gezeichnet; das achte, ``homeassistant``, zeigt das
**echte Home-Assistant-Zeichen** in Weiß auf Home-Assistant-Blau. Dafür wird
kein Motiv gezeichnet: aus dem Quellbild
``assets/ntfy-icons/src/home-assistant.png`` (die Originalkachel — weißes
Zeichen auf blauem Grund) werden die **hellen Partien** als weiße Form
gewonnen und auf das übliche abgerundete Quadrat samt hellem Rand gesetzt —
siehe ``zeichen_erzeugen``. Der Alphakanal der Kachel taugt dafür *nicht*: er
umreißt das ganze Quadrat, nicht das Zeichen. Die Quellbilder liegen im Repo,
das Skript braucht also weiterhin kein Netz.

Motive und ihre Abgrenzung (auch in Graustufen und auf Handygröße):

* ``server-management``  **Kisten-Stapel ohne Standortnadel**: derselbe
  Container-Stapel wie ``cleve`` — drei schmalere Kisten mit je vier
  senkrechten Rillen, treppenförmig nach rechts oben versetzt —, aber **ohne**
  die Nadel und **größer** (rund 85 % der Fläche, während der Stapel im
  cleve-Icon neben der Nadel nur knapp die Hälfte einnimmt). Keine Nadel =
  Innenstandort, Nadel = Auswärtsstandort.
* ``homeassistant``      **Home-Assistant-Zeichen in Weiß** auf
  Home-Assistant-Blau (``#18bcf2``, dieselbe Farbe wie die Originalkachel):
  das echte Zeichen als weiße Form auf dem üblichen abgerundeten Quadrat mit
  hellem Rand — dieselbe Machart wie alle übrigen Motive.
* ``cleve``              **Kisten-Stapel mit Standortnadel**: drei schmalere
  Kisten mit je vier senkrechten dunklen Rillen, treppenförmig nach rechts
  oben versetzt; oben rechts eine **Standortnadel** (Pin mit dunklem Loch)
  als Kennzeichen für den Auswärtsstandort.
* ``general``            **Kommandozeilen-Fenster**: Fenster mit Titelleiste
  und drei Fensterknöpfen, im Inhalt ein Chevron ``>`` als
  Eingabeaufforderung und ein Cursor-Balken.
* ``minijob``            **Werkzeugkasten**: breiter Korpus, darüber ein
  Bügel (Halbring), dunkle Deckelfuge mit Schloss in der Mitte.
* ``uni``                **Doktorhut**: liegende Raute auf schmalerem
  Käppchen, dazu eine Quaste rechts. Vier schräge Kanten plus hängender
  Faden — einzige spitze Silhouette neben den beiden Fenstern.
* ``svelte``             **Browserfenster**: dasselbe Fenster wie
  ``general``, im Inhalt aber eine **Erdkugel** (Kreis mit Meridian und
  Äquator) statt der Eingabeaufforderung.
* ``hermes``             **Geflügelter Helm**: Kuppel, dunkles Hutband, breite
  Krempe und links wie rechts ein Flügel aus drei Federspitzen — das
  klassische Boten-Attribut. Das dunkle Hutband trennt Kuppel und Krempe, die
  sonst auf 64 px zu einer einzigen weißen Fläche verschmelzen (siehe
  ``motiv_hermes_helm``). Der frühere geflügelte Stab bleibt als
  ``motiv_hermes_stab`` im Skript erhalten.

Gegenprobe der beiden Fenster-Motive: ``general`` und ``svelte`` teilen
sich Rahmen, Titelleiste und Fensterknöpfe und unterscheiden sich allein im
Inhalt — links unten eine Prompt-Zeile aus Chevron und Cursor gegen eine
Erdkugel, die den Innenraum füllt. Auf 64 px ist das der Unterschied
zwischen "zwei kurze Striche" und "großer Kreis mit Kreuz". Die beiden
Kisten-Motive unterscheiden sich doppelt: ``cleve`` trägt die Standortnadel
und einen kleinen Stapel, ``server-management`` den großen Stapel ohne Nadel.
Werkzeugkasten (Bügel mit Schloss), Doktorhut (Raute auf Käppchen),
Flügelhelm (Kuppel mit Hutband und Flügeln) und das weiße
Home-Assistant-Zeichen auf eigenem Blau haben eigene Silhouetten und Farben;
sie sind auch in Graustufen und auf Handygröße nicht zu verwechseln.

Aufruf:

    python3 tools/make-ntfy-icons.py
    python3 tools/make-ntfy-icons.py --uebersicht

``--uebersicht`` schreibt zusätzlich ``uebersicht.png``: je Workspace das
Icon in 160 px und in 56 px (Handy-Vorschau) mit Namen darunter, vier Spalten
und zwei Zeilen auf hellem Grund. Das ist nur eine Sichtprüfung für die
Motivwahl — dieselbe Datei entsteht sonst nirgends.

Das Skript ist deterministisch: es enthält weder Zeitstempel noch
Zufallswerte, ein erneuter Lauf erzeugt also byte-identische Dateien — auch
für die beiden Logos, deren Quellbilder fest im Repo liegen
(``assets/ntfy-icons/src/``), sodass kein Netz nötig ist. Neue Workspaces
einfach unten in ICONS ergänzen.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PIL import Image, ImageChops, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

# Kantenlänge der fertigen PNGs in Pixeln
KANTE = 256

# Kantenlänge des internen Zeichenpuffers (Supersampling für weiche Kanten).
# Wird zum Schluss auf KANTE herunterskaliert -> reproduzierbare Antialiasing.
UEBERABTASTUNG = 4

# Eckenradius des Quadrats (bezogen auf KANTE, wird mitskaliert)
ECKENRADIUS = 56

# Einzug und Breite des hellen Randes (bezogen auf KANTE)
RAND_EINZUG = 3
RAND_BREITE = 2

# Strichstärke der kräftigen weißen Linien (Anteil der Zeichenfläche)
STRICH_ANTEIL = 0.045

# Transparenter Rand eines unverändert eingepassten Logos, als Anteil der
# Zielfläche. Das Logo wird auf (KANTE - 2 * LOGO_RAND) eingepasst, damit es
# nicht am Bildrand klebt; Seitenverhältnis und Transparenz bleiben dabei
# erhalten. Derzeit nutzt das kein Icon — es bleibt als Rückfall für ein Motiv
# stehen, das ein Logo unverändert zeigen soll.
LOGO_RAND = 0.05

# Kantenlänge, auf die ein echtes Zeichen aus einem Quellbild eingepasst wird,
# als Anteil der Zielfläche (wie die gezeichneten Motive rund zwei Drittel).
ZEICHEN_ANTEIL = 0.72

# Füllung des Kisten-Stapels von ``server-management`` (Anteil der Zielfläche,
# bezogen auf die längere Seite des Stapels). ``cleve`` zeichnet denselben
# Stapel kleiner, weil dort noch die Standortnadel daneben steht.
KISTEN_FUELLUNG = 0.84

# Helligkeitsrampe, mit der aus einem Quellbild die *hellen* Partien als Form
# gewonnen werden (Maß ist der kleinste Farbkanal je Pixel): bis HELL_TIEF
# durchsichtig, ab HELL_HOCH voll deckend, dazwischen weich. So bleibt die Kante
# des Zeichens glatt, statt zu treppeln.
HELL_TIEF = 60
HELL_HOCH = 200

# Aufbau der Übersicht (Sichtprüfung): heller Grund, Titelzeile, je Workspace
# das Icon in 160 px und darunter in 56 px (Handy-Vorschau), Name darunter.
UEBERSICHT_GRUND = "#f5f5f7"
UEBERSICHT_TITEL = "ntfy-Icons (oben 160 px, unten 56 px)"
UEBERSICHT_GROSS = 160
UEBERSICHT_KLEIN = 56
UEBERSICHT_SPALTEN = 4
UEBERSICHT_RAND = 28
UEBERSICHT_TEXT = "#1f2937"

# Voll deckendes Weiß für das Piktogramm
WEISS = (255, 255, 255, 255)


@dataclass(frozen=True)
class Quellbild:
    """Verweis auf ein Bild in ``assets/ntfy-icons/src/``.

    ``als_form`` steuert, was damit geschieht:

    * ``False`` — das Bild wird unverändert auf die Zielfläche eingepasst
      (echtes Logo, wie es ist; siehe ``logo_erzeugen``).
    * ``True`` — die hellen Partien des Bildes dienen als Form für ein **weißes
      Zeichen auf dem farbigen Rahmen** (siehe ``zeichen_erzeugen``).
    """

    datei: str
    als_form: bool = False


# Zeichenfunktion eines Motivs: (Zeichner, Hintergrundfarbe, Fläche, Strich).
# In der Tabelle ICONS darf das Motiv None sein: dann entsteht das Icon nicht aus
# einer Zeichenfunktion, sondern aus dem Quellbild in der fünften Spalte (siehe
# Quellbild).
Motiv = Callable[[ImageDraw.ImageDraw, "tuple[int, int, int]", int, int], None]


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------


def hex_zu_rgb(farbe: str) -> tuple[int, int, int]:
    """Wandelt '#rrggbb' in ein RGB-Tripel um."""
    wert = farbe.lstrip("#")
    if len(wert) != 6:
        raise ValueError(f"Ungültige Farbe: {farbe!r} (erwartet #rrggbb)")
    return tuple(int(wert[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def aufhellen(rgb: tuple[int, int, int], anteil: float) -> tuple[int, int, int]:
    """Mischt eine Farbe mit Weiß auf (anteil = 0.0 .. 1.0)."""
    return tuple(round(k + (255 - k) * anteil) for k in rgb)  # type: ignore[return-value]


def abdunkeln(rgb: tuple[int, int, int], anteil: float) -> tuple[int, int, int]:
    """Mischt eine Farbe mit Schwarz ab (anteil = 0.0 .. 1.0).

    Dient für die dunklen Details (LEDs, Rillen, Punkte, Fugen): sie liegen
    auf dem weißen Piktogramm und bleiben so auch in Graustufen sichtbar.
    """
    return tuple(round(k * (1 - anteil)) for k in rgb)  # type: ignore[return-value]


def px(anteil: float, basis: int) -> int:
    """Rechnet einen Anteil der Zeichenfläche in ganze Pixel um (deterministisch)."""
    return int(round(anteil * basis))


def einpassen(form: Image.Image, nutzbar: int) -> Image.Image:
    """Skaliert eine Form seitenverhältnistreu auf höchstens ``nutzbar`` Pixel.

    Der kleinere der beiden Faktoren gewinnt, verzerrt wird also nichts; das
    Resampling mit LANCZOS (fest, kein Zufall) ergibt weiche Kanten.
    """
    faktor = nutzbar / max(form.width, form.height)
    return form.resize(
        (max(1, round(form.width * faktor)), max(1, round(form.height * faktor))),
        Image.LANCZOS,
    )


def helligkeit_rampe(wert: int) -> int:
    """Bildet den kleinsten Farbkanal eines Pixels auf eine weiche Deckung ab."""
    if wert <= HELL_TIEF:
        return 0
    if wert >= HELL_HOCH:
        return 255
    return (wert - HELL_TIEF) * 255 // (HELL_HOCH - HELL_TIEF)


def form_aus_bild(vorlage: Image.Image) -> Image.Image:
    """Gewinnt aus einem Quellbild die **hellen Partien** als Graustufenform.

    Maß ist der kleinste Farbkanal je Pixel. Bei der Home-Assistant-Kachel
    (weißes Zeichen ``#f2f4f9`` auf blauem Grund ``#18bcf2``) liegt der Grund
    bei 24 und das Zeichen bei 242 — die Rampe trennt also sauber und hält die
    Ränder weich. Zurück kommt die auf den sichtbaren Bereich zugeschnittene
    Form, damit später das Zeichen und nicht sein Randmaß die Größe bestimmt.
    """
    rot, gruen, blau = vorlage.convert("RGB").split()
    dunkelster = ImageChops.darker(ImageChops.darker(rot, gruen), blau)
    form = dunkelster.point(helligkeit_rampe)

    zuschnitt = form.getbbox()
    if zuschnitt is None:
        raise ValueError("Quellbild ohne helle Partien — daraus lässt sich keine Form gewinnen")
    return form.crop(zuschnitt)


def hintergrund(farbe: str) -> tuple[Image.Image, ImageDraw.ImageDraw, tuple[int, int, int], int]:
    """Legt das abgerundete Quadrat samt hellem Rand an.

    Rückgabe: (Bild, Zeichner, RGB der Farbe, Kantenlänge des Puffers).
    """
    rgb = hex_zu_rgb(farbe)
    kante = KANTE * UEBERABTASTUNG

    bild = Image.new("RGBA", (kante, kante), (0, 0, 0, 0))
    zeichner = ImageDraw.Draw(bild)

    # Abgerundetes Quadrat in der Workspace-Farbe füllt die ganze Fläche
    zeichner.rounded_rectangle(
        (0, 0, kante - 1, kante - 1),
        radius=ECKENRADIUS * UEBERABTASTUNG,
        fill=(*rgb, 255),
    )

    # Optionaler dünner heller Rand, leicht nach innen gesetzt
    if RAND_EINZUG >= 0 and RAND_BREITE > 0:
        einzug = RAND_EINZUG * UEBERABTASTUNG
        zeichner.rounded_rectangle(
            (einzug, einzug, kante - 1 - einzug, kante - 1 - einzug),
            radius=max(0, (ECKENRADIUS - RAND_EINZUG) * UEBERABTASTUNG),
            outline=(*aufhellen(rgb, 0.45), 200),
            width=RAND_BREITE * UEBERABTASTUNG,
        )

    return bild, zeichner, rgb, kante


# ---------------------------------------------------------------------------
# Motive (alle Koordinaten als Anteil der Zeichenfläche -> mitskalierend)
# ---------------------------------------------------------------------------


def motiv_server(zeichner: ImageDraw.ImageDraw, rgb: tuple[int, int, int], kante: int, strich: int) -> None:
    """Server-Rack: drei waagerechte Gehäuse (3 HE) mit Lüftungsschlitz und 2 LEDs.

    Die Frontblenden sind bewusst asymmetrisch aufgebaut (Schlitz links,
    LEDs rechts) — sonst sähe das Motiv wie eine Aufzählungsliste aus
    (drei Balken mit Punkt links).
    """
    del strich  # die Gehäuse sind massiv, keine Linien nötig
    detail = (*abdunkeln(rgb, 0.55), 255)

    breite = px(0.62, kante)
    hoehe = px(0.16, kante)
    luecke = px(0.032, kante)
    gesamt = 3 * hoehe + 2 * luecke
    x0 = (kante - breite) // 2
    y0 = (kante - gesamt) // 2

    led_radius = px(0.021, kante)
    schlitz_hoehe = px(0.03, kante)
    for reihe in range(3):
        oben = y0 + reihe * (hoehe + luecke)
        zeichner.rounded_rectangle(
            (x0, oben, x0 + breite, oben + hoehe),
            radius=px(0.028, kante),
            fill=WEISS,
        )
        mitte_y = oben + hoehe // 2
        # Lüftungsschlitz auf der linken Hälfte der Frontblende
        zeichner.rounded_rectangle(
            (
                x0 + px(0.09, breite),
                mitte_y - schlitz_hoehe // 2,
                x0 + px(0.55, breite),
                mitte_y + schlitz_hoehe // 2,
            ),
            radius=schlitz_hoehe // 2,
            fill=detail,
        )
        # zwei LED-Punkte rechts
        for led_anteil in (0.78, 0.90):
            led_cx = x0 + px(led_anteil, breite)
            zeichner.ellipse(
                (led_cx - led_radius, mitte_y - led_radius, led_cx + led_radius, mitte_y + led_radius),
                fill=detail,
            )


def motiv_haus(zeichner: ImageDraw.ImageDraw, rgb: tuple[int, int, int], kante: int, strich: int) -> None:
    """Haus: Dachdreieck mit Überstand, Korpus und dunkle Bogentür."""
    del strich
    cx = kante // 2

    dach_spitze = px(0.19, kante)
    traufe = px(0.47, kante)
    basis = px(0.80, kante)
    dach_halb = px(0.33, kante)
    korpus_halb = px(0.255, kante)

    # Dach (Dreieck) und Korpus (Rechteck) als massive weiße Flächen
    zeichner.polygon(
        [(cx, dach_spitze), (cx + dach_halb, traufe), (cx - dach_halb, traufe)],
        fill=WEISS,
    )
    zeichner.rectangle((cx - korpus_halb, traufe, cx + korpus_halb, basis), fill=WEISS)

    # Tür als "Loch" in der Workspace-Farbe: Bogen oben, unten offen.
    # Die untere Abrundung liegt unterhalb der Hauskante und ist unsichtbar.
    tuer_halb = px(0.075, kante)
    tuer_oben = px(0.635, kante)
    zeichner.rounded_rectangle(
        (cx - tuer_halb, tuer_oben, cx + tuer_halb, basis + tuer_halb),
        radius=tuer_halb,
        fill=(*rgb, 255),
    )


# Gemeinsames Fenster der Motive ``general`` (Kommandozeile) und ``svelte``
# (Browser): Rahmen, Titelleiste und drei Fensterknöpfe sind identisch, nur der
# Inhalt unterscheidet sich. Koordinaten als Anteil der Zeichenfläche.
FENSTER_LINKS = 0.135
FENSTER_RECHTS = 0.865
FENSTER_OBEN = 0.195
FENSTER_UNTEN = 0.805
FENSTER_LEISTE = 0.355


def fenster_zeichnen(
    zeichner: ImageDraw.ImageDraw,
    kante: int,
    strich: int,
    detail: tuple[int, int, int, int],
) -> tuple[int, int]:
    """Zeichnet das Fenster (Rahmen, Titelleiste, drei Fensterknöpfe).

    Die Knöpfe sitzen als dunkle Punkte im hellen Balken — dieselbe Technik
    wie die Rillen der Kisten, nur rund. Rückgabe: Mitte des Innenraums
    unterhalb der Titelleiste als (x, y).
    """
    links = px(FENSTER_LINKS, kante)
    rechts = px(FENSTER_RECHTS, kante)
    oben = px(FENSTER_OBEN, kante)
    unten = px(FENSTER_UNTEN, kante)
    leiste = px(FENSTER_LEISTE, kante)
    radius = px(0.075, kante)

    # Titelleiste: oben wie das Fenster gerundet, unten gerade abgeschlossen
    zeichner.rounded_rectangle((links, oben, rechts, leiste), radius=radius, fill=WEISS)
    zeichner.rectangle((links, leiste - radius, rechts, leiste), fill=WEISS)

    # Fensterrahmen; seine obere Rundung liegt deckungsgleich unter der Leiste
    zeichner.rounded_rectangle(
        (links, oben, rechts, unten),
        radius=radius,
        outline=WEISS,
        width=strich,
    )

    # drei Fensterknöpfe im hellen Balken
    knopf_radius = px(0.028, kante)
    knopf_cy = (oben + leiste) // 2
    for knopf in range(3):
        knopf_cx = links + px(0.10, kante) + knopf * px(0.075, kante)
        zeichner.ellipse(
            (knopf_cx - knopf_radius, knopf_cy - knopf_radius, knopf_cx + knopf_radius, knopf_cy + knopf_radius),
            fill=detail,
        )

    return (links + rechts) // 2, (leiste + unten) // 2


def motiv_kommandozeile(zeichner: ImageDraw.ImageDraw, rgb: tuple[int, int, int], kante: int, strich: int) -> None:
    """Kommandozeilen-Fenster: Chevron als Eingabeaufforderung plus Cursor-Balken."""
    detail = (*abdunkeln(rgb, 0.55), 255)
    _, mitte_y = fenster_zeichnen(zeichner, kante, strich, detail)

    halb = px(0.095, kante)
    zeichner.line(
        [
            (px(0.245, kante), mitte_y - halb),
            (px(0.375, kante), mitte_y),
            (px(0.245, kante), mitte_y + halb),
        ],
        fill=WEISS,
        width=max(2, px(0.042, kante)),
        joint="curve",
    )
    zeichner.rectangle(
        (px(0.475, kante), mitte_y - halb, px(0.535, kante), mitte_y + halb),
        fill=WEISS,
    )


def motiv_browser(zeichner: ImageDraw.ImageDraw, rgb: tuple[int, int, int], kante: int, strich: int) -> None:
    """Browserfenster: Erdkugel aus Kreis, Meridian und Äquator."""
    detail = (*abdunkeln(rgb, 0.55), 255)
    cx, cy = fenster_zeichnen(zeichner, kante, strich, detail)

    radius = px(0.175, kante)
    linie = max(2, px(0.038, kante))

    # Umriss der Kugel
    zeichner.ellipse(
        (cx - radius, cy - radius, cx + radius, cy + radius),
        outline=WEISS,
        width=linie,
    )
    # Meridian (schmale Ellipse) und Äquator
    zeichner.ellipse(
        (cx - px(0.075, kante), cy - radius, cx + px(0.075, kante), cy + radius),
        outline=WEISS,
        width=linie,
    )
    zeichner.line((cx - radius, cy, cx + radius, cy), fill=WEISS, width=linie)


def kistenstapel_zeichnen(
    zeichner: ImageDraw.ImageDraw,
    detail: tuple[int, int, int, int],
    kante: int,
    x0: int,
    y0: int,
    breite: int,
    hoehe: int,
    luecke: int,
    versatz: int,
) -> None:
    """Zeichnet drei treppenförmig versetzte Kisten mit je vier senkrechten Rillen.

    ``x0``/``y0`` ist die linke obere Ecke des Stapels: die unterste Kiste sitzt
    links unten, jede weitere rückt um ``versatz`` nach rechts und oben. Geteilt
    von ``cleve`` (klein, mit Standortnadel) und ``server-management`` (groß,
    ohne Nadel), damit beide wirklich denselben Stapel zeigen.
    """
    rillen_breite = max(2, px(0.035, breite))
    for kiste in range(3):
        # unterste Kiste links unten, jede weitere nach rechts oben versetzt
        links = x0 + kiste * versatz
        oben = y0 + (2 - kiste) * (hoehe + luecke)
        zeichner.rounded_rectangle(
            (links, oben, links + breite, oben + hoehe),
            radius=px(0.02, kante),
            fill=WEISS,
        )
        # vier senkrechte Rillen (Containerprofil)
        oben_rille = oben + px(0.24, hoehe)
        unten_rille = oben + px(0.76, hoehe)
        for i in range(4):
            rille_x = links + px(0.18 + 0.21 * i, breite)
            zeichner.rectangle(
                (rille_x - rillen_breite // 2, oben_rille, rille_x + rillen_breite // 2, unten_rille),
                fill=detail,
            )


def motiv_container(zeichner: ImageDraw.ImageDraw, rgb: tuple[int, int, int], kante: int, strich: int) -> None:
    """Kisten-Stapel mit Standortnadel: drei versetzte Kisten mit senkrechten
    Rillen, oben rechts eine Nadel als Kennzeichen für den Auswärtsstandort.

    Die Kisten rücken dafür etwas nach links unten; die Nadel steht frei
    daneben, damit sich ihre Umrisse auf Handygröße nicht mit den Kisten zu
    einer Fläche verbinden.
    """
    del strich
    detail = (*abdunkeln(rgb, 0.45), 255)

    kistenstapel_zeichnen(
        zeichner,
        detail,
        kante,
        x0=px(0.085, kante),
        y0=px(0.425, kante),
        breite=px(0.35, kante),
        hoehe=px(0.135, kante),
        luecke=px(0.025, kante),
        versatz=px(0.045, kante),
    )

    # Standortnadel: Kreis mit Spitze nach unten (Teardrop) und dunklem Loch
    nadel_cx = px(0.75, kante)
    nadel_cy = px(0.30, kante)
    nadel_radius = px(0.10, kante)
    zeichner.ellipse(
        (
            nadel_cx - nadel_radius,
            nadel_cy - nadel_radius,
            nadel_cx + nadel_radius,
            nadel_cy + nadel_radius,
        ),
        fill=WEISS,
    )
    zeichner.polygon(
        [
            (nadel_cx - px(0.055, kante), nadel_cy + px(0.075, kante)),
            (nadel_cx + px(0.055, kante), nadel_cy + px(0.075, kante)),
            (nadel_cx, nadel_cy + px(0.20, kante)),
        ],
        fill=WEISS,
    )
    loch_radius = px(0.042, kante)
    zeichner.ellipse(
        (nadel_cx - loch_radius, nadel_cy - loch_radius, nadel_cx + loch_radius, nadel_cy + loch_radius),
        fill=detail,
    )


def motiv_kistenstapel(zeichner: ImageDraw.ImageDraw, rgb: tuple[int, int, int], kante: int, strich: int) -> None:
    """Kisten-Stapel **ohne** Standortnadel: derselbe Stapel wie bei ``cleve``,
    nur ohne Nadel und deutlich größer, mittig im Bild.

    Er steht hier allein, während er sich bei ``cleve`` den Platz mit der Nadel
    teilt — deshalb füllt er ``KISTEN_FUELLUNG`` der Fläche. Die Unterscheidung
    der beiden Motive bleibt damit erhalten: **mit** Nadel = Auswärtsstandort,
    **ohne** Nadel = Innenstandort.
    """
    del strich
    detail = (*abdunkeln(rgb, 0.45), 255)

    # Die Höhe (3 Kisten + 2 Lücken) ist die längere Seite des Stapels und damit
    # das Maß für die Füllung; Breite, Lücken und Versatz skalieren mit, sodass
    # die Proportionen der Kisten unverändert bleiben.
    faktor = KISTEN_FUELLUNG / (3 * 0.135 + 2 * 0.025)

    breite = px(0.35 * faktor, kante)
    hoehe = px(0.135 * faktor, kante)
    luecke = px(0.025 * faktor, kante)
    versatz = px(0.045 * faktor, kante)

    kistenstapel_zeichnen(
        zeichner,
        detail,
        kante,
        x0=(kante - (breite + 2 * versatz)) // 2,
        y0=(kante - (3 * hoehe + 2 * luecke)) // 2,
        breite=breite,
        hoehe=hoehe,
        luecke=luecke,
        versatz=versatz,
    )


def motiv_werkzeugkasten(zeichner: ImageDraw.ImageDraw, rgb: tuple[int, int, int], kante: int, strich: int) -> None:
    """Werkzeugkasten: Korpus, Bügel (Halbring) und dunkle Deckelfuge mit Schloss."""
    detail = (*abdunkeln(rgb, 0.55), 255)
    cx = kante // 2

    korpus_halb = px(0.31, kante)
    korpus_oben = px(0.46, kante)
    korpus_unten = px(0.82, kante)

    # Bügel zuerst: seine Enden verschwinden im Korpus
    buergel_halb = px(0.145, kante)
    zeichner.arc(
        (cx - buergel_halb, px(0.27, kante), cx + buergel_halb, px(0.70, kante)),
        start=180,
        end=360,
        fill=WEISS,
        width=strich,
    )

    zeichner.rounded_rectangle(
        (cx - korpus_halb, korpus_oben, cx + korpus_halb, korpus_unten),
        radius=px(0.045, kante),
        fill=WEISS,
    )

    # Deckelfuge (quer) und Schloss (senkrecht darüber) als dunkle Details
    fuge = korpus_oben + px(0.10, kante)
    zeichner.rectangle(
        (
            cx - korpus_halb + px(0.012, kante),
            fuge - px(0.011, kante),
            cx + korpus_halb - px(0.012, kante),
            fuge + px(0.011, kante),
        ),
        fill=detail,
    )
    schloss_halb = px(0.038, kante)
    zeichner.rounded_rectangle(
        (cx - schloss_halb, korpus_oben, cx + schloss_halb, korpus_oben + px(0.14, kante)),
        radius=px(0.02, kante),
        fill=detail,
    )


def motiv_doktorhut(zeichner: ImageDraw.ImageDraw, rgb: tuple[int, int, int], kante: int, strich: int) -> None:
    """Doktorhut: liegende Raute (Brett) auf Käppchen, Quaste rechts."""
    del rgb
    cx = kante // 2

    brett_cy = px(0.40, kante)
    brett_halb_b = px(0.35, kante)
    brett_halb_h = px(0.155, kante)

    # Käppchen darunter (Trapez, nach unten schmaler)
    kappe_oben = px(0.44, kante)
    kappe_unten = px(0.72, kante)
    zeichner.polygon(
        [
            (cx - px(0.19, kante), kappe_oben),
            (cx + px(0.19, kante), kappe_oben),
            (cx + px(0.145, kante), kappe_unten),
            (cx - px(0.145, kante), kappe_unten),
        ],
        fill=WEISS,
    )

    # Quaste: Faden läuft am rechten Rand des Bretts heraus, Kugel am Ende
    zeichner.line(
        (cx + px(0.255, kante), brett_cy, cx + px(0.255, kante), px(0.63, kante)),
        fill=WEISS,
        width=max(2, px(0.028, kante)),
    )
    ball_radius = px(0.048, kante)
    ball_cy = px(0.675, kante)
    ball_cx = cx + px(0.255, kante)
    zeichner.ellipse(
        (ball_cx - ball_radius, ball_cy - ball_radius, ball_cx + ball_radius, ball_cy + ball_radius),
        fill=WEISS,
    )

    # Brett als Raute zuletzt -> liegt sauber auf dem Käppchen
    zeichner.polygon(
        [
            (cx, brett_cy - brett_halb_h),
            (cx + brett_halb_b, brett_cy),
            (cx, brett_cy + brett_halb_h),
            (cx - brett_halb_b, brett_cy),
        ],
        fill=WEISS,
    )


def motiv_hermes_stab(zeichner: ImageDraw.ImageDraw, rgb: tuple[int, int, int], kante: int, strich: int) -> None:
    """Geflügelter Stab (Hermes, ohne Schlangen): senkrechter Stab mit Kugel
    oben und je einem Flügel aus drei Federspitzen links und rechts.

    Gewählt wurde der Stab statt Helm oder Flügelschuh, weil er auf 64 px am
    klarsten bleibt: Stab, Kugel und die drei Federspitzen sind drei getrennte
    Formen. Der geflügelte Helm verschmilzt klein dagegen zu einer einzigen
    weißen Fläche mit dunklem Querstrich und war in der Gegenprobe bei 64 px
    deutlich schlechter zu erkennen.
    """
    del rgb, strich
    cx = kante // 2

    kugel_cy = px(0.235, kante)
    stab_halb = px(0.048, kante)

    # Stab mit gerundetem Ende, oben die Kugel als Abschluss. Der Stab ist
    # bewusst kräftig: dünner gezeichnet zerfällt das Motiv auf 64 px zu einem
    # Strich, und die Flügel lesen sich dann wie ein Insekt.
    zeichner.rounded_rectangle(
        (cx - stab_halb, kugel_cy, cx + stab_halb, px(0.84, kante)),
        radius=px(0.045, kante),
        fill=WEISS,
    )
    kugel_radius = px(0.085, kante)
    zeichner.ellipse(
        (cx - kugel_radius, kugel_cy - kugel_radius, cx + kugel_radius, kugel_cy + kugel_radius),
        fill=WEISS,
    )

    # Flügel: geschlossene Silhouette mit glatter Oberkante und drei Federspitzen,
    # seitlich am Stab sitzend. Einmal links, einmal gespiegelt rechts.
    fluegel = [
        (0.465, 0.300),  # Ansatz oben am Stab
        (0.330, 0.240),
        (0.200, 0.215),
        (0.130, 0.205),  # vorderste Federspitze
        (0.160, 0.275),  # Federspitze 1
        (0.250, 0.300),  # Einkerbung
        (0.190, 0.365),  # Federspitze 2
        (0.280, 0.385),  # Einkerbung
        (0.240, 0.450),  # Federspitze 3
        (0.360, 0.440),
        (0.465, 0.400),  # Ansatz unten am Stab
    ]
    for spiegel in (False, True):
        punkte = [
            (px(x if not spiegel else 1.0 - x, kante), px(y, kante)) for x, y in fluegel
        ]
        zeichner.polygon(punkte, fill=WEISS)


def motiv_hermes_helm(zeichner: ImageDraw.ImageDraw, rgb: tuple[int, int, int], kante: int, strich: int) -> None:
    """Geflügelter Helm (Petasos) des Hermes: hohe Kuppel, dunkles Hutband,
    breite Krempe und links wie rechts ein Flügel mit drei Federspitzen.

    Aufbau von oben nach unten: die Kuppel sitzt als schmale, hohe Halbellipse
    auf der Krempe und **überragt die Flügel deutlich** — nur so bleibt „Helm"
    auch bei 64 px erkennbar (gleich hohe Flügel verschmelzen mit der Kuppel zu
    einer breiten Masse, das Motiv liest sich dann als Fledermaus). Darunter
    läuft das **dunkle Hutband** quer über die volle Krempenbreite; ohne es
    verschmelzen Kuppel und Krempe zu einer einzigen Fläche und das Motiv liest
    sich als Pilz. Die Flügel wurzeln unter der Kuppel und fächern nach außen
    auf; ihre drei Federspitzen bleiben als Zacken abgesetzt, damit die
    Silhouette nicht zu einem Balken zerfällt.

    Der geflügelte **Stab** (``motiv_hermes_stab``) bleibt als gezeichnete
    Zweitfassung im Skript — er war die frühere Wahl.
    """
    del strich
    cx = kante // 2
    detail = (*abdunkeln(rgb, 0.5), 255)

    # Krempe: breite, flach gerundete Scheibe. Ihre Oberkante trägt die Kuppel.
    krempe_halb = px(0.305, kante)
    krempe_oben = px(0.615, kante)
    krempe_radius = px(0.035, kante)

    # Flügel zuerst — ihre Wurzel verschwindet anschließend unter der Kuppel.
    # Sie sitzen tiefer als die Kuppelspitze und bleiben flacher als sie, damit
    # die Kuppel das Bild oben bestimmt.
    fluegel = [
        (0.400, 0.475),  # Wurzel oben (liegt unter der Kuppel)
        (0.330, 0.445),
        (0.250, 0.430),
        (0.175, 0.430),  # vorderste Federspitze
        (0.205, 0.483),  # Einkerbung
        (0.155, 0.505),  # Federspitze 1
        (0.230, 0.530),  # Einkerbung
        (0.215, 0.570),  # Federspitze 2
        (0.300, 0.565),  # Einkerbung
        (0.400, 0.550),  # Wurzel unten (liegt unter der Kuppel)
    ]
    for spiegel in (False, True):
        punkte = [
            (px(x if not spiegel else 1.0 - x, kante), px(y, kante)) for x, y in fluegel
        ]
        zeichner.polygon(punkte, fill=WEISS)

    zeichner.rounded_rectangle(
        (cx - krempe_halb, krempe_oben, cx + krempe_halb, px(0.705, kante)),
        radius=krempe_radius,
        fill=WEISS,
    )

    # Hutband: dunkler Streifen an der Krempen-Oberkante. Seine oberen Rundungen
    # sind dieselben wie die der Krempe, es tritt also nirgends darüber hinaus.
    zeichner.rounded_rectangle(
        (cx - krempe_halb, krempe_oben, cx + krempe_halb, px(0.660, kante)),
        radius=krempe_radius,
        fill=detail,
    )

    # Kuppel: obere Hälfte einer Ellipse, auf der Krempen-Oberkante aufsitzend —
    # schmal und hoch, damit sie die Flügel überragt.
    kuppel_halb = px(0.155, kante)
    kuppel_hoehe = px(0.33, kante)
    zeichner.pieslice(
        (
            cx - kuppel_halb,
            krempe_oben - kuppel_hoehe,
            cx + kuppel_halb,
            krempe_oben + kuppel_hoehe,
        ),
        start=180,
        end=360,
        fill=WEISS,
    )


# ---------------------------------------------------------------------------
# Tabelle der Icons:
# (Dateiname ohne .png, Workspace-Farbe, Motiv, Beschreibung, Quellbild)
# Das Motiv wird gezeichnet; ist es None, entsteht das Icon stattdessen aus dem
# Quellbild in ``assets/ntfy-icons/src/``. Bei einem Zeichen als Form ist die
# Farbe die des Rahmens, bei einem eingepassten Logo nur Dokumentation.
# ---------------------------------------------------------------------------

ICONS: list[tuple[str, str, Motiv | None, str, Quellbild | None]] = [
    (
        "server-management",
        "#2563eb",
        motiv_kistenstapel,
        "Kisten-Stapel ohne Standortnadel, groß (Innenstandort)",
        None,
    ),
    (
        "homeassistant",
        "#18bcf2",
        None,
        "Home-Assistant-Zeichen in Weiß auf Home-Assistant-Blau",
        Quellbild("home-assistant.png", als_form=True),
    ),
    ("cleve", "#64748b", motiv_container, "Kisten-Stapel mit Standortnadel (Auswärtsstandort)", None),
    ("general", "#334155", motiv_kommandozeile, "Kommandozeilen-Fenster mit Prompt und Cursor", None),
    ("minijob", "#0d9488", motiv_werkzeugkasten, "Werkzeugkasten mit Bügel und Schloss", None),
    ("uni", "#7c3aed", motiv_doktorhut, "Doktorhut mit Quaste", None),
    ("svelte", "#ea580c", motiv_browser, "Browserfenster mit Erdkugel", None),
    ("hermes", "#ca8a04", motiv_hermes_helm, "Geflügelter Helm mit Hutband und Flügeln (Boten-Attribut)", None),
]


# ---------------------------------------------------------------------------
# Ablauf
# ---------------------------------------------------------------------------


def quell_verzeichnis() -> Path:
    """Ordner mit den Quellbildern (echte Zeichen): <repo>/assets/ntfy-icons/src.

    Bewusst unabhängig vom Ausgabeordner: die Quellbilder gehören ins Repo, das
    Skript liest sie von dort und braucht dafür kein Netz.
    """
    return Path(__file__).resolve().parent.parent / "assets" / "ntfy-icons" / "src"


def quellbild_laden(quelle: str) -> Image.Image:
    """Lädt ein Quellbild aus ``assets/ntfy-icons/src/`` als RGBA-Bild."""
    with Image.open(quell_verzeichnis() / quelle) as bild:
        bild.load()
        return bild.convert("RGBA")


def logo_erzeugen(quelle: str) -> Image.Image:
    """Setzt ein Quellbild (echtes Logo) mittig auf eine transparente Zielfläche.

    Eingepasst wird seitenverhältnistreu (siehe ``einpassen``), verzerrt wird
    also nichts. Der Rest der Zielfläche bleibt transparent, ein transparenter
    Rand des Quellbilds ebenso. Derzeit nutzt das kein Icon — der Weg bleibt für
    ein Logo erhalten, das unverändert gezeigt werden soll.
    """
    logo = einpassen(quellbild_laden(quelle), KANTE - 2 * px(LOGO_RAND, KANTE))

    ziel = Image.new("RGBA", (KANTE, KANTE), (0, 0, 0, 0))
    ziel.alpha_composite(logo, ((KANTE - logo.width) // 2, (KANTE - logo.height) // 2))
    return ziel


def zeichen_erzeugen(quelle: str, farbe: str, anteil: float) -> Image.Image:
    """Setzt ein echtes Zeichen in **Weiß** auf den farbigen Rahmen.

    Die Form kommt aus den hellen Partien des Quellbilds (siehe
    ``form_aus_bild``): bei ``src/home-assistant.png`` ist das die weiße
    Zeichnung auf der blauen Originalkachel. Der Alphakanal dieser Kachel wäre
    als Form unbrauchbar — er umreißt das ganze abgerundete Quadrat. Das Zeichen
    wird seitenverhältnistreu auf ``anteil`` der Fläche eingepasst, mittig
    gesetzt und mit denselben Rahmen-, Rand- und Überabtastungswerten wie die
    gezeichneten Motive hinterlegt.
    """
    bild, _, _, kante = hintergrund(farbe)

    form = einpassen(form_aus_bild(quellbild_laden(quelle)), px(anteil, kante))
    weiss = Image.new("RGBA", form.size, WEISS)
    weiss.putalpha(form)
    bild.alpha_composite(weiss, ((kante - form.width) // 2, (kante - form.height) // 2))

    return bild.resize((KANTE, KANTE), Image.LANCZOS)


def icon_erzeugen(farbe: str, motiv: Motiv | None, quelle: Quellbild | None = None) -> Image.Image:
    """Baut ein einzelnes Icon als 256x256-PNG-taugliches RGBA-Bild.

    Ist ``quelle`` gesetzt, entsteht das Icon aus diesem Quellbild statt aus
    einem gezeichneten Piktogramm: entweder als **weiße Form** auf dem farbigen
    Rahmen (``Quellbild.als_form``) oder als unverändert eingepasstes Logo.
    """
    if quelle is not None:
        if quelle.als_form:
            return zeichen_erzeugen(quelle.datei, farbe, ZEICHEN_ANTEIL)
        return logo_erzeugen(quelle.datei)
    if motiv is None:
        raise ValueError(f"Icon ohne Motiv und ohne Quellbild: {farbe!r}")

    bild, zeichner, rgb, kante = hintergrund(farbe)
    strich = max(2, px(STRICH_ANTEIL, kante))
    motiv(zeichner, rgb, kante, strich)

    # Herunterskalieren -> weiche Kanten, feste Zielgröße
    return bild.resize((KANTE, KANTE), Image.LANCZOS)


def ziel_verzeichnis(argumente: list[str]) -> Path:
    """Ermittelt den Ausgabeordner (Standard: <repo>/assets/ntfy-icons)."""
    if argumente:
        return Path(argumente[0]).expanduser().resolve()
    repo_wurzel = Path(__file__).resolve().parent.parent
    return repo_wurzel / "assets" / "ntfy-icons"


def schrift(groesse: int) -> ImageFont.ImageFont:
    """Liefert eine Schrift in der gewünschten Größe (ohne Systemdateien, ohne Netz).

    Pillow bringt sie mit. Ältere Pillow-Fassungen (< 10.1) kennen den
    Größenparameter nicht — dann bleibt es bei der eingebauten Bitmap-Schrift
    und die Übersicht ist nur kleiner beschriftet; das Bild selbst bleibt gleich.
    """
    try:
        return ImageFont.load_default(size=groesse)
    except TypeError:
        return ImageFont.load_default()


def uebersicht_erzeugen(ziel: Path, icons: list[tuple[str, Image.Image]]) -> Path:
    """Schreibt ``uebersicht.png``: je Workspace 160 px und 56 px mit Namen.

    Reine Sichtprüfung für die Motivwahl (kein Symbol für Meldungen). Aufbau:
    heller Grund, oben die Titelzeile, darunter die Motive in vier Spalten und
    zwei Zeilen — je Feld das Bild in 160 px, darunter dasselbe in 56 px
    (Handy-Vorschau), ganz unten der Name. Jedes Bild wird vollständig
    platziert, die Felder sind so bemessen, dass sich nichts überlappt.
    """
    schrift_titel = schrift(22)
    schrift_name = schrift(17)

    spalten = UEBERSICHT_SPALTEN
    zeilen = (len(icons) + spalten - 1) // spalten
    feld_breite = UEBERSICHT_GROSS + 24
    feld_hoehe = UEBERSICHT_GROSS + 12 + UEBERSICHT_KLEIN + 10 + 22
    kopf = 62

    breite = 2 * UEBERSICHT_RAND + spalten * feld_breite
    hoehe = kopf + zeilen * feld_hoehe + UEBERSICHT_RAND

    bild = Image.new("RGBA", (breite, hoehe), (*hex_zu_rgb(UEBERSICHT_GRUND), 255))
    zeichner = ImageDraw.Draw(bild)
    zeichner.text((UEBERSICHT_RAND, 22), UEBERSICHT_TITEL, fill=UEBERSICHT_TEXT, font=schrift_titel)

    for nummer, (name, icon) in enumerate(icons):
        links = UEBERSICHT_RAND + (nummer % spalten) * feld_breite
        oben = kopf + (nummer // spalten) * feld_hoehe

        gross = icon.resize((UEBERSICHT_GROSS, UEBERSICHT_GROSS), Image.LANCZOS)
        klein = icon.resize((UEBERSICHT_KLEIN, UEBERSICHT_KLEIN), Image.LANCZOS)
        bild.alpha_composite(gross, (links + (feld_breite - UEBERSICHT_GROSS) // 2, oben))
        bild.alpha_composite(
            klein,
            (links + (feld_breite - UEBERSICHT_KLEIN) // 2, oben + UEBERSICHT_GROSS + 12),
        )

        name_breite = zeichner.textlength(name, font=schrift_name)
        zeichner.text(
            (
                int(links + (feld_breite - name_breite) / 2),
                oben + UEBERSICHT_GROSS + 12 + UEBERSICHT_KLEIN + 10,
            ),
            name,
            fill=UEBERSICHT_TEXT,
            font=schrift_name,
        )

    pfad = ziel / "uebersicht.png"
    bild.convert("RGB").save(pfad, format="PNG", optimize=True)
    return pfad


def main(argv: list[str]) -> int:
    schalter = {argument for argument in argv[1:] if argument.startswith("--")}
    argumente = [argument for argument in argv[1:] if not argument.startswith("--")]
    unbekannt = sorted(schalter - {"--uebersicht"})
    if unbekannt:
        print(f"Unbekannter Schalter: {', '.join(unbekannt)}", file=sys.stderr)
        print("Aufruf: python3 tools/make-ntfy-icons.py [zielordner] [--uebersicht]", file=sys.stderr)
        return 2

    ziel = ziel_verzeichnis(argumente)
    ziel.mkdir(parents=True, exist_ok=True)

    print(f"Ausgabeordner: {ziel}")
    print()

    erzeugt: list[tuple[str, Image.Image]] = []
    for name, farbe, motiv, beschreibung, quelle in ICONS:
        datei = ziel / f"{name}.png"
        icon = icon_erzeugen(farbe, motiv, quelle)
        icon.save(datei, format="PNG", optimize=True)
        erzeugt.append((name, icon))

        groesse = datei.stat().st_size
        breite, hoehe = icon.size
        print(
            f"  {datei.name:<24} {breite}x{hoehe}  {groesse:>6} B "
            f"({groesse / 1024:.1f} KB)  {farbe}  {beschreibung}"
        )

    print()
    if "--uebersicht" in schalter:
        pfad = uebersicht_erzeugen(ziel, erzeugt)
        print(f"Übersicht: {pfad.name} ({pfad.stat().st_size} B)")
        print()

    print(f"{len(ICONS)} Icons erzeugt.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
