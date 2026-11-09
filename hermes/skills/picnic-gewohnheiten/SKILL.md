---
name: picnic-gewohnheiten
version: 1.1
author: carsten
license: MIT
metadata:
  hermes:
    tags: [shopping, picnic, cart, groceries]
    related_skills: []
description: Use before adding any product to the Picnic cart - checks the user's purchase history so the usual product is picked instead of searching, and enforces the minimum order value.
---

# Picnic-Gewohnheiten (Stammartikel aus der Kaufhistorie)

Der Nutzer kauft fast immer dieselben Artikel. Bevor du bei Picnic suchst, pruefe
deshalb die Kaufhistorie. Das ist deutlich billiger und trifft den Geschmack
besser als eine freie Suche.

**Wichtig:** Haeufig gekauft heisst nicht automatisch "will ich wieder". Bei
ungenauen Wuenschen wird nicht geraten, sondern ausgewaehlt.

## Ablauf bei "pack X in den Warenkorb" / "fuege X hinzu"

1. **Historie verdichten** (immer zuerst, ein einziger Aufruf, dauert ~6 s):

   ```sh
   python3 /opt/data/skills/shopping/picnic-gewohnheiten/scripts/gewohnheiten.py
   ```

   Standard sind die letzten 30 Lieferungen. Ausgabe: eine Zeile je Artikel im
   Format `Produkt-ID | Name | Einheit | Kaeufe | letzter Preis | zuletzt gekauft`.

2. **Passenden Artikel waehlen — nicht raten**
   - **Eindeutige Angabe** (Marke und/oder Variante genannt, z. B. "Milram
     Rahmkäse", "Volvic 6x1,5L"): gibt es genau einen passenden Treffer in der
     Historie, direkt mit `picnic_add_to_cart(product_id, count)` hinzufuegen.
   - **Oberbegriff** ("Butter", "Hähnchen", "Reis", "Wasser", "Brot"):
     **niemals** automatisch den haeufigsten Treffer nehmen. Zeige die 2–3
     naechstliegenden Zeilen aus der Historie mit Name, Einheit, Preis und
     Kaeufe und frage, welchen er will. Erst nach seiner Antwort hinzufuegen.
   - Genau ein einziger Historientreffer fuer den Begriff und Name/Einheit passen
     offensichtlich: direkt nehmen, aber im Bericht sagen, welcher es war.
   - Kein Treffer oder letzter Preis `99999` (Picnic-Platzhalter fuer "nicht
     bestellbar") -> Schritt 3.

3. **Fallback: Suche** — nur wenn Schritt 2 nichts Brauchbares liefert:
   `picnic_search`, danach `picnic_get_product_details` zur Kontrolle. Bei
   mehreren aehnlichen Treffern wieder 2–3 zur Auswahl stellen.

4. **In den Warenkorb** mit `picnic_add_to_cart(product_id, count)`.

5. **Bericht**: welcher Artikel gewaehlt wurde, Preis, Menge — und ob er aus der
   Historie stammt oder aus einer Suche.

## Mindestbestellwert (harte Sperre)

Picnic liefert erst ab einem Mindestbestellwert (aktuell 45,00 EUR; der Wert
haengt am Liefer-Slot und kommt live aus der API).

Vor **jeder** Bestellung, jedem Checkout und jeder Aussage wie "das kann ich
bestellen" oder "ich buche den Slot":

```sh
python3 /opt/data/skills/shopping/picnic-gewohnheiten/scripts/korb.py
```

- Status `NICHT ERREICHT` -> dem Nutzer die fehlende Summe nennen und **nicht
  weitermachen**. Kein Slot buchen, keine Bestellung, kein Checkout.
- Status `erreicht` -> Bestellvorgang darf weitergehen, aber weiterhin nur nach
  ausdruecklicher Bestaetigung im selben Turn.
- Das Skript prueft nur; es bestellt nichts.

## Grenzen

- Die Historie ist keine Bestaetigung, dass der Artikel heute lieferbar ist. Bei
  `99999` oder einem Fehler beim Hinzufuegen: Suche als Fallback, dem Nutzer sagen.
- Beide Skripte lesen nur. Sie aendern nichts am Warenkorb und bestellen nichts.
- Bestellen, Liefer-Slot buchen, Storno und Rechnungs-Mail weiterhin nur nach
  ausdruecklicher Bestaetigung im selben Turn (siehe SOUL.md).
- Meldet ein Skript 2FA/Session: dem Nutzer sagen und nach dem SMS-Code fragen,
  niemals raten.
