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

Ausnahme sind die beiden Icons mit einem **echten Logo**: ``server-management``
trägt das Proxmox-Zeichen, ``homeassistant`` das Home-Assistant-Zeichen. Diese
beiden werden nicht gezeichnet, sondern aus einem Quellbild in
``assets/ntfy-icons/src/`` zusammengesetzt (auf die Zielfläche eingepasst,
Seitenverhältnis erhalten, zentriert, alles außerhalb des Motivs bleibt
transparent) — siehe ``logo_erzeugen``. Die Quellbilder liegen im Repo, das
Skript braucht also weiterhin kein Netz.

Motive und ihre Abgrenzung (auch in Graustufen und auf Handygröße):

* ``server-management``  **Proxmox** (echtes Logo, ``src/proxmox.png``): das
  Markenzeichen des Proxmox-Projekts. Wird nicht gezeichnet, sondern aus dem
  Quellbild auf die Zielfläche eingepasst und zentriert.
* ``cleve``              **Container-Stapel**: drei schmalere Kisten mit je
  vier senkrechten dunklen Rillen, treppenförmig nach rechts oben versetzt.
  Ebenfalls Balken, aber Rillen statt Punkte und Versatz statt Flucht.
* ``homeassistant``      **Home Assistant** (echtes Logo,
  ``src/home-assistant.png``): das Zeichen des Home-Assistant-Projekts,
  ebenfalls eingepasst statt gezeichnet.
* ``svelte``             **Blitz**: gezacktes Sechseck. Einziges schräg
  verlaufendes, spitzwinkliges Motiv ohne rechte Winkel.
* ``general``            **Werkzeugkasten**: breiter Korpus, darüber ein
  Bügel (Halbring), dunkle Deckelfuge mit Schloss. Balken MIT Aufsatz.
* ``minijob``            **Münzenstapel**: vier waagerechte Ellipsen mit
  dunklem Rand (oberste mit Innenring). Einziges Motiv aus reinen
  Rundformen, ohne eine einzige gerade Kante.
* ``uni``                **Doktorhut**: liegende Raute auf schmalerem
  Käppchen, dazu eine Quaste rechts. Vier schräge Kanten plus hängender
  Faden — klar getrennt vom Blitz (spitzwinklig, ohne Faden).
* ``hermes``             **Sprechblase**: abgerundetes Rechteck mit Schwanz
  unten links und drei dunklen Punkten. Balken MIT Schwanz (der
  Werkzeugkasten hat stattdessen den Bügel oben und das Schloss).

Gegenprobe der drei "Balken"-Motive: sie teilen die Grundsilhouette, sind
aber über genau ein auffälliges Zusatzmerkmal unterscheidbar, das auch
klein erhalten bleibt — Container: vier **Rillen** und
**Treppenversatz**; Werkzeugkasten: **Bügel oben** und Schloss **in der
Mitte**; Sprechblase: **Schwanz unten links** und drei Punkte **in einer
Reihe über die ganze Breite**. Blitz, Münzen und Hut sind bereits in der
Silhouette eindeutig; die beiden Logos tragen ihre eigenen Farben und sind
damit ohnehin nicht zu verwechseln.

Aufruf:

    python3 tools/make-ntfy-icons.py

Das Skript ist deterministisch: es enthält weder Zeitstempel noch
Zufallswerte, ein erneuter Lauf erzeugt also byte-identische Dateien — auch
für die beiden Logos, deren Quellbilder fest im Repo liegen
(``assets/ntfy-icons/src/``), sodass kein Netz nötig ist. Neue Workspaces
einfach unten in ICONS ergänzen.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw

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

# Transparenter Rand der beiden Logo-Icons, als Anteil der Zielfläche. Das Logo
# wird auf (KANTE - 2 * LOGO_RAND) eingepasst, damit es nicht am Bildrand klebt;
# Seitenverhältnis und Transparenz bleiben dabei erhalten.
LOGO_RAND = 0.05

# Voll deckendes Weiß für das Piktogramm
WEISS = (255, 255, 255, 255)

# Zeichenfunktion eines Motivs: (Zeichner, Hintergrundfarbe, Fläche, Strich).
# In der Tabelle ICONS darf das Motiv None sein: dann entsteht das Icon nicht aus
# einer Zeichenfunktion, sondern aus dem Quellbild in der fünften Spalte (echtes
# Logo, siehe logo_erzeugen).
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


def motiv_blitz(zeichner: ImageDraw.ImageDraw, rgb: tuple[int, int, int], kante: int, strich: int) -> None:
    """Blitz: gezacktes Sechseck, mittig und spitzwinklig."""
    del rgb, strich
    cx = kante // 2
    cy = kante // 2
    halb_b = px(0.30, kante)
    halb_h = px(0.345, kante)

    umriss = [
        (0.35, -1.00),
        (-0.78, 0.10),
        (-0.05, 0.10),
        (-0.35, 1.00),
        (0.78, -0.20),
        (0.05, -0.20),
    ]
    punkte = [(cx + px(x, halb_b), cy + px(y, halb_h)) for x, y in umriss]
    zeichner.polygon(punkte, fill=WEISS)


def motiv_container(zeichner: ImageDraw.ImageDraw, rgb: tuple[int, int, int], kante: int, strich: int) -> None:
    """Container-Stapel: drei versetzte Kisten mit senkrechten Rillen."""
    del strich
    detail = (*abdunkeln(rgb, 0.45), 255)

    breite = px(0.46, kante)
    hoehe = px(0.18, kante)
    luecke = px(0.032, kante)
    versatz = px(0.055, kante)
    gesamt = 3 * hoehe + 2 * luecke

    x0 = (kante - (breite + 2 * versatz)) // 2
    y0 = (kante - gesamt) // 2

    rillen_breite = max(2, px(0.035, breite))
    for kiste in range(3):
        # unterste Kiste links unten, jede weitere nach rechts oben versetzt
        links = x0 + kiste * versatz
        oben = y0 + (2 - kiste) * (hoehe + luecke)
        zeichner.rounded_rectangle(
            (links, oben, links + breite, oben + hoehe),
            radius=px(0.025, kante),
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


def motiv_muenzen(zeichner: ImageDraw.ImageDraw, rgb: tuple[int, int, int], kante: int, strich: int) -> None:
    """Münzenstapel: vier gestapelte Ellipsen mit dunklem Rand."""
    del strich
    detail = (*abdunkeln(rgb, 0.5), 255)
    cx = kante // 2

    halb_b = px(0.235, kante)
    halb_h = px(0.075, kante)
    abstand = px(0.115, kante)
    unten_mitte = px(0.775, kante)
    rand = px(0.018, kante)

    for muenze in range(4):  # von unten nach oben, jede höhere deckt die tiefere
        cy = unten_mitte - muenze * abstand
        zeichner.ellipse(
            (cx - halb_b, cy - halb_h, cx + halb_b, cy + halb_h),
            fill=WEISS,
            outline=detail,
            width=rand,
        )

    # Innenring auf der obersten Münze -> Münzbild statt bloßer Scheibe
    oben_mitte = unten_mitte - 3 * abstand
    zeichner.ellipse(
        (cx - px(0.135, kante), oben_mitte - px(0.038, kante), cx + px(0.135, kante), oben_mitte + px(0.038, kante)),
        outline=detail,
        width=rand,
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


def motiv_sprechblase(zeichner: ImageDraw.ImageDraw, rgb: tuple[int, int, int], kante: int, strich: int) -> None:
    """Sprechblase: abgerundetes Rechteck mit Schwanz und drei Punkten."""
    del strich
    detail = (*abdunkeln(rgb, 0.55), 255)

    links = px(0.17, kante)
    rechts = px(0.83, kante)
    oben = px(0.17, kante)
    unten = px(0.68, kante)
    radius = px(0.115, kante)

    zeichner.rounded_rectangle((links, oben, rechts, unten), radius=radius, fill=WEISS)

    # Schwanz unten links: Oberkante liegt im Blasenkörper -> verschmilzt
    zeichner.polygon(
        [
            (px(0.30, kante), unten - px(0.03, kante)),
            (px(0.50, kante), unten - px(0.03, kante)),
            (px(0.27, kante), px(0.86, kante)),
        ],
        fill=WEISS,
    )

    # drei Punkte ("Nachricht") in einer Reihe über die Blasenmitte
    punkt_radius = px(0.052, kante)
    mitte_y = (oben + unten) // 2
    for i in (-1, 0, 1):
        cx = kante // 2 + i * px(0.165, kante)
        zeichner.ellipse(
            (cx - punkt_radius, mitte_y - punkt_radius, cx + punkt_radius, mitte_y + punkt_radius),
            fill=detail,
        )


# ---------------------------------------------------------------------------
# Tabelle der Icons:
# (Dateiname ohne .png, Workspace-Farbe, Motiv, Beschreibung, Quellbild)
# Das Motiv wird gezeichnet; ist es None, entsteht das Icon stattdessen aus dem
# Quellbild in ``assets/ntfy-icons/src/`` (die Farbe ist dann nur Dokumentation).
# ---------------------------------------------------------------------------

ICONS: list[tuple[str, str, Motiv | None, str, str | None]] = [
    ("server-management", "#2563eb", None, "Proxmox-Zeichen (Quellbild)", "proxmox.png"),
    ("homeassistant", "#f59e0b", None, "Home-Assistant-Zeichen (Quellbild)", "home-assistant.png"),
    ("cleve", "#16a34a", motiv_container, "Container-Stapel mit Rillen", None),
    ("general", "#64748b", motiv_werkzeugkasten, "Werkzeugkasten mit Bügel und Schloss", None),
    ("minijob", "#0d9488", motiv_muenzen, "Münzenstapel (4 Münzen)", None),
    ("uni", "#7c3aed", motiv_doktorhut, "Doktorhut mit Quaste", None),
    ("svelte", "#ea580c", motiv_blitz, "Blitz", None),
    ("hermes", "#ca8a04", motiv_sprechblase, "Sprechblase mit drei Punkten", None),
]


# ---------------------------------------------------------------------------
# Ablauf
# ---------------------------------------------------------------------------


def quell_verzeichnis() -> Path:
    """Ordner mit den Quellbildern (echte Logos): <repo>/assets/ntfy-icons/src.

    Bewusst unabhängig vom Ausgabeordner: die Quellbilder gehören ins Repo, das
    Skript liest sie von dort und braucht dafür kein Netz.
    """
    return Path(__file__).resolve().parent.parent / "assets" / "ntfy-icons" / "src"


def logo_erzeugen(quelle: str) -> Image.Image:
    """Setzt ein Quellbild (echtes Logo) mittig auf eine transparente Zielfläche.

    Eingepasst wird mit dem kleineren der beiden Faktoren — das Seitenverhältnis
    bleibt also exakt erhalten, verzerrt wird nichts. Der Rest der Zielfläche
    bleibt transparent, ein transparenter Rand des Quellbilds ebenso.
    """
    with Image.open(quell_verzeichnis() / quelle) as bild:
        bild.load()
        vorlage = bild.convert("RGBA")

    nutzbar = KANTE - 2 * px(LOGO_RAND, KANTE)
    faktor = min(nutzbar / vorlage.width, nutzbar / vorlage.height)
    breite = max(1, round(vorlage.width * faktor))
    hoehe = max(1, round(vorlage.height * faktor))
    logo = vorlage.resize((breite, hoehe), Image.LANCZOS)

    ziel = Image.new("RGBA", (KANTE, KANTE), (0, 0, 0, 0))
    ziel.alpha_composite(logo, ((KANTE - breite) // 2, (KANTE - hoehe) // 2))
    return ziel


def icon_erzeugen(farbe: str, motiv: Motiv | None, quelle: str | None = None) -> Image.Image:
    """Baut ein einzelnes Icon als 256x256-PNG-taugliches RGBA-Bild.

    Ist ``quelle`` gesetzt, entsteht das Icon aus diesem Quellbild (echtes Logo)
    statt aus einem gezeichneten Piktogramm; ``farbe`` und ``motiv`` bleiben dann
    unbenutzt.
    """
    if quelle:
        return logo_erzeugen(quelle)
    if motiv is None:
        raise ValueError(f"Icon ohne Motiv und ohne Quellbild: {farbe!r}")

    bild, zeichner, rgb, kante = hintergrund(farbe)
    strich = max(2, px(STRICH_ANTEIL, kante))
    motiv(zeichner, rgb, kante, strich)

    # Herunterskalieren -> weiche Kanten, feste Zielgröße
    return bild.resize((KANTE, KANTE), Image.LANCZOS)


def ziel_verzeichnis(argv: list[str]) -> Path:
    """Ermittelt den Ausgabeordner (Standard: <repo>/assets/ntfy-icons)."""
    if len(argv) > 1:
        return Path(argv[1]).expanduser().resolve()
    repo_wurzel = Path(__file__).resolve().parent.parent
    return repo_wurzel / "assets" / "ntfy-icons"


def main(argv: list[str]) -> int:
    ziel = ziel_verzeichnis(argv)
    ziel.mkdir(parents=True, exist_ok=True)

    print(f"Ausgabeordner: {ziel}")
    print()

    for name, farbe, motiv, beschreibung, quelle in ICONS:
        datei = ziel / f"{name}.png"
        icon = icon_erzeugen(farbe, motiv, quelle)
        icon.save(datei, format="PNG", optimize=True)

        groesse = datei.stat().st_size
        breite, hoehe = icon.size
        print(
            f"  {datei.name:<24} {breite}x{hoehe}  {groesse:>6} B "
            f"({groesse / 1024:.1f} KB)  {farbe}  {beschreibung}"
        )

    print()
    print(f"{len(ICONS)} Icons erzeugt.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
