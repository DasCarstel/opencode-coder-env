#!/usr/bin/env python3
"""openScale-CSV in die Tabelle body_measurement importieren.

Einmaliger Import der Historie aus dem CSV-Export der openScale-App
(Einstellungen -> Daten exportieren). Spaltennamen werden tolerant zugeordnet,
fehlende Spalten bleiben leer.

Aufruf:
    import_openscale_csv.py /pfad/openscale.csv
    import_openscale_csv.py /pfad/openscale.csv --dry-run
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime

import exporter as yz

COLUMNS = {
    "weight": "weight_kg",
    "gewicht": "weight_kg",
    "fat": "body_fat_pct",
    "bodyfat": "body_fat_pct",
    "body_fat": "body_fat_pct",
    "körperfett": "body_fat_pct",
    "koerperfett": "body_fat_pct",
    "water": "water_pct",
    "wasser": "water_pct",
    "muscle": "muscle_pct",
    "muskel": "muscle_pct",
    "muskelmasse": "muscle_pct",
    "lbm": "lbm_pct",
    "bone": "bone_pct",
    "knochen": "bone_pct",
}

DATE_COLUMNS = ("datetime", "date", "datum", "messzeitpunkt", "timestamp")
ID_COLUMNS = ("id",)


def to_number(value: str | None) -> float | None:
    if value is None:
        return None
    text = value.strip().replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def to_moment(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    for pattern in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%d.%m.%Y %H:%M:%S",
        "%d.%m.%Y %H:%M",
        "%d.%m.%Y",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("file")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--delimiter", default=None)
    args = parser.parse_args()

    with open(args.file, newline="", encoding="utf-8-sig") as handle:
        sample = handle.read(4096)
        handle.seek(0)
        delimiter = args.delimiter or (";" if sample.count(";") > sample.count(",") else ",")
        rows = list(csv.DictReader(handle, delimiter=delimiter))

    if not rows:
        sys.exit("Keine Zeilen gelesen")

    header = {name.strip().lower(): name for name in rows[0].keys() if name}
    date_key = next((header[c] for c in DATE_COLUMNS if c in header), None)
    id_key = next((header[c] for c in ID_COLUMNS if c in header), None)
    if date_key is None:
        sys.exit("Keine Datumsspalte gefunden. Vorhanden: %s" % ", ".join(header))

    mapping = {col: header[key] for key, col in COLUMNS.items() if key in header}
    print("Datumsspalte: %s" % date_key)
    print("Erkannte Spalten: %s" % ", ".join("%s<- %s" % (c, k) for c, k in mapping.items()))
    unknown = set(header) - set(mapping.values()) - {date_key.lower(), (id_key or "").lower()}
    if unknown:
        print("Ignoriert: %s" % ", ".join(sorted(unknown)))

    conn = yz.connect()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS body_measurement (
            id bigserial PRIMARY KEY, openscale_id bigint, measured_at timestamptz NOT NULL,
            weight_kg numeric(6,2), body_fat_pct numeric(5,2), water_pct numeric(5,2),
            muscle_pct numeric(5,2), lbm_pct numeric(5,2), bone_pct numeric(5,2),
            source text NOT NULL DEFAULT 'openscale', raw jsonb,
            created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE UNIQUE INDEX IF NOT EXISTS body_measurement_openscale_id
            ON body_measurement (openscale_id) WHERE openscale_id IS NOT NULL;
        """
    )

    written = 0
    skipped = 0
    for row in rows:
        moment = to_moment(row.get(date_key))
        values = {col: to_number(row.get(col_key)) for col, col_key in mapping.items()}
        if moment is None or values.get("weight_kg") is None:
            skipped += 1
            continue
        if args.dry_run:
            print("  %s  %s kg" % (moment.isoformat(), values["weight_kg"]))
            written += 1
            continue
        conn.execute(
            """
            INSERT INTO body_measurement (openscale_id, measured_at, weight_kg, body_fat_pct,
                                          water_pct, muscle_pct, lbm_pct, bone_pct, source, raw)
            VALUES (%(id)s, %(moment)s, %(weight_kg)s, %(body_fat_pct)s, %(water_pct)s,
                    %(muscle_pct)s, %(lbm_pct)s, %(bone_pct)s, 'openscale-import', %(raw)s)
            ON CONFLICT (openscale_id) WHERE openscale_id IS NOT NULL DO UPDATE
               SET measured_at = EXCLUDED.measured_at,
                   weight_kg = EXCLUDED.weight_kg,
                   body_fat_pct = EXCLUDED.body_fat_pct,
                   water_pct = EXCLUDED.water_pct,
                   muscle_pct = EXCLUDED.muscle_pct,
                   lbm_pct = EXCLUDED.lbm_pct,
                   bone_pct = EXCLUDED.bone_pct,
                   updated_at = now()
            """,
            {
                "id": row.get(id_key) if id_key else None,
                "moment": moment,
                "raw": json.dumps({k: v for k, v in row.items() if v}, ensure_ascii=False),
                **values,
            },
        )
        written += 1

    print("%s %s Messungen, %s Zeilen uebersprungen" % ("Wuerde schreiben:" if args.dry_run else "Geschrieben:", written, skipped))


if __name__ == "__main__":
    main()
