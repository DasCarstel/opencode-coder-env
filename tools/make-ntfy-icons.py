#!/usr/bin/env python3
"""Erzeugt die ntfy-Icons (PNG) für alle Workspaces und für Hermes.

Die Icons werden im Repo abgelegt und von ntfy über eine öffentliche
Roh-URL geladen, damit auf dem Handy sofort erkennbar ist, woher eine
Meldung kommt:

    https://raw.githubusercontent.com/DasCarstel/opencode-coder-env/master/assets/ntfy-icons/<name>.png

Jedes Icon ist ein abgerundetes Quadrat in der Workspace-Farbe, darin
1-2 weiße Großbuchstaben sowie ein dünner heller Rand.

Aufruf:

    python3 tools/make-ntfy-icons.py

Das Skript ist deterministisch: es enthält weder Zeitstempel noch
Zufallswerte, ein erneuter Lauf erzeugt also byte-identische Dateien.
Neue Workspaces einfach unten in ICONS ergänzen.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

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

# Anteil der Kantenlänge, den die Buchstaben höchstens einnehmen dürfen
TEXT_BREITE_ANTEIL = 0.60
TEXT_HOEHE_ANTEIL = 0.46

# Tabelle der Icons: (Name, Farbe, Kürzel).
# Der Name bestimmt den Dateinamen assets/ntfy-icons/<name>.png.
ICONS: list[tuple[str, str, str]] = [
    ("server-management", "#2563eb", "SM"),
    ("homeassistant", "#f59e0b", "HA"),
    ("cleve", "#16a34a", "CL"),
    ("general", "#64748b", "GE"),
    ("minijob", "#0d9488", "MJ"),
    ("uni", "#7c3aed", "UN"),
    ("svelte", "#ea580c", "SV"),
    ("hermes", "#ca8a04", "HE"),
]


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


def schrift_fuer(text: str, max_breite: int, max_hoehe: int) -> ImageFont.ImageFont:
    """Sucht die größte Pillow-Standardschrift, die in den erlaubten Rahmen passt.

    Verwendet werden soll die eingebaute Schrift (kein Systemfont nötig).
    Der Rahmen wird von groß nach klein durchprobiert -> deterministisch.
    """
    groesse = max_hoehe * 3
    while groesse > 6:
        font = ImageFont.load_default(size=groesse)
        box = font.getbbox(text)
        if (box[2] - box[0]) <= max_breite and (box[3] - box[1]) <= max_hoehe:
            return font
        groesse -= 1
    return ImageFont.load_default(size=6)


def text_maske(text: str, font: ImageFont.ImageFont) -> Image.Image:
    """Rendert den Text in eine auf die Tinten-Box zugeschnittene Graustufenmaske."""
    box = font.getbbox(text)
    breite = box[2] - box[0]
    hoehe = box[3] - box[1]

    # Etwas Luft, damit nichts am Rand abgeschnitten wird
    polster = 8
    ebene = Image.new("L", (breite + 2 * polster, hoehe + 2 * polster), 0)
    zeichner = ImageDraw.Draw(ebene)
    zeichner.text((polster - box[0], polster - box[1]), text, fill=255, font=font)

    return ebene.crop(ebene.getbbox())  # type: ignore[return-value]


def icon_erzeugen(farbe: str, kuerzel: str) -> Image.Image:
    """Baut ein einzelnes Icon als 256x256-PNG-taugliches RGBA-Bild."""
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

    max_breite = int(kante * TEXT_BREITE_ANTEIL)
    max_hoehe = int(kante * TEXT_HOEHE_ANTEIL)
    font = schrift_fuer(kuerzel, max_breite, max_hoehe)
    maske = text_maske(kuerzel, font)

    # Buchstaben rein weiß, mittig platziert
    weiss = Image.new("RGBA", maske.size, (255, 255, 255, 0))
    weiss.putalpha(maske)
    position = ((kante - maske.width) // 2, (kante - maske.height) // 2)
    bild.alpha_composite(weiss, position)

    # Herunterskalieren -> weiche Kanten, feste Zielgröße
    return bild.resize((KANTE, KANTE), Image.LANCZOS)


# ---------------------------------------------------------------------------
# Ablauf
# ---------------------------------------------------------------------------


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

    for name, farbe, kuerzel in ICONS:
        datei = ziel / f"{name}.png"
        icon = icon_erzeugen(farbe, kuerzel)
        icon.save(datei, format="PNG", optimize=True)

        groesse = datei.stat().st_size
        breite, hoehe = icon.size
        print(
            f"  {datei.name:<24} {breite}x{hoehe}  {groesse:>6} B "
            f"({groesse / 1024:.1f} KB)  {farbe}  {kuerzel}"
        )

    print()
    print(f"{len(ICONS)} Icons erzeugt.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
