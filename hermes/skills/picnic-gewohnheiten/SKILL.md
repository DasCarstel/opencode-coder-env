---
name: picnic-gewohnheiten
version: 1.0
author: carsten
license: MIT
metadata:
  hermes:
    tags: [shopping, picnic, cart, groceries]
    related_skills: []
description: Use before adding any product to the Picnic cart - checks the user's purchase history so the usual product is picked instead of searching.
---

# Picnic-Gewohnheiten (Stammartikel aus der Kaufhistorie)

Der Nutzer kauft fast immer dieselben Artikel. Bevor du bei Picnic suchst, pruefe
deshalb die Kaufhistorie. Das ist deutlich billiger und trifft den Geschmack
besser als eine freie Suche.

## Ablauf bei "pack X in den Warenkorb" / "fuege X hinzu"

1. **Historie verdichten** (immer zuerst, ein einziger Aufruf, dauert ~12 s):

   ```sh
   python3 /opt/data/skills/shopping/picnic-gewohnheiten/scripts/gewohnheiten.py
   ```

   Ausgabe: eine Zeile je Artikel im Format
   `Produkt-ID | Name | Einheit | Kaeufe | letzter Preis | zuletzt gekauft`.

2. **Passenden Artikel waehlen**
   - Oberbegriff ("Wasser", "Milch", "Eier"): nimm den haeufigsten Treffer aus der
     Historie.
   - Marke oder Variante genannt: der Name muss dazu passen.
   - Artikel steht in der Historie, letzter Preis ist aber `99999`: das ist der
     Picnic-Platzhalter fuer "nicht bestellbar" -> weiter mit Schritt 3.
   - Kein passender Treffer -> weiter mit Schritt 3.

3. **Fallback: Suche** — nur wenn Schritt 2 nichts Brauchbares liefert:
   `picnic_search`, danach `picnic_get_product_details` zur Kontrolle.

4. **In den Warenkorb** mit `picnic_add_to_cart(product_id, count)`.

5. **Bericht**: welcher Artikel gewaehlt wurde, Preis, Menge — und ob er aus der
   Historie stammt oder aus einer Suche. Wenn du einen anderen Artikel als sonst
   genommen hast, sag das ausdruecklich.

## Grenzen

- Die Historie ist keine Bestaetigung, dass der Artikel heute lieferbar ist. Bei
  `99999` oder einem Fehler beim Hinzufuegen: Suche als Fallback, dem Nutzer sagen.
- Das Skript liest nur. Es aendert nichts am Warenkorb und bestellt nichts.
- Bestellen, Liefer-Slot buchen, Storno und Rechnungs-Mail weiterhin nur nach
  ausdruecklicher Bestaetigung im selben Turn (siehe SOUL.md).
- Meldet das Skript 2FA/Session: dem Nutzer sagen und nach dem SMS-Code fragen,
  niemals raten.
