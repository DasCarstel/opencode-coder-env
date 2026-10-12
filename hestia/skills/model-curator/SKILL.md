---
name: model-curator
description: Modellverwaltung für das OpenCode-Fleet über das Hestia-Panel — Live-Katalog, Preise, aktives Tier-Setup und KI-Vorschlag abfragen. Nur LESER-Endpunkte; nur im Hermes-Standardprofil installiert (kleve-Profil bewusst ohne).
---

# Hestia — Model Fleet Curator

Hestia (`http://hestia:8000`, coder-infra-Netz, HTTP Basic Auth) verwaltet die
Model-Router-Tiers (Fast/Medium/Heavy) aller OpenCode-Coder-Container.

## Authentifizierung

Basic-Auth via `curl -u "hestia:$HESTIA_PASSWORD"`. Das Passwort liegt im
Runtime-Env des Containers (`HESTIA_PASSWORD` in `/opt/data/.env`).

## Werkzeuge

Beobachter-Modus (kein Schreiben!). Alle Ausgaben dem Nutzer übersichtlich
zusammenfassen — nie Roh-JSON dumpen:

1. **Status + aktive Tiers** (häufigste Frage: „Welche Modelle nutzen wir?"):

```bash
curl -s -u "hestia:$HESTIA_PASSWORD" http://hestia:8000/api/config
curl -s -u "hestia:$HESTIA_PASSWORD" http://hestia:8000/api/health
```

2. **Modell-Listing mit Preisen** („Welche Modelle gibt es?"):

```bash
curl -s -u "hestia:$HESTIA_PASSWORD" http://hestia:8000/api/catalog
```

Antwort (`models[]`): `ref`, `name`, `cost.input`/`cost.output` (USD pro Mio
Token), `limit.context`, `variants`, `status`. Ausgabe begrenzen (Top 12 nach
Neuheit oder Preis) — nicht alles ausspucken.

3. **KI-Vorschlag** („Welche Modelle soll ich nutzen?") — nur auf ausdrückliche
   Aufforderung; dauert 1–5 Minuten:

```bash
curl -s -X POST -u "hestia:$HESTIA_PASSWORD" http://hestia:8000/api/suggest -m 600
```

Ergebnis (`decision.{fast,medium,heavy}` + `reasoning`) als kleine Tabelle
präsentieren. **Der Nutzer entscheidet**, ob er es im Panel übernimmt
(hestia.mueller-nas.de). `/api/apply` gehört NICHT in dieses Skill — es pusht
Git-Commits und läuft bewusst nur über das Panel.

## Fehlerbehandlung

- `connection refused` → Hestia-Container prüfen (Coder-Host: `docker logs hestia`)
- `401` → `HESTIA_PASSWORD` in der Runtime-.env mit OpenBao abgleichen.

## Regeln

- Nur die GET-/suggest-Werkzeuge dieses Skills; `/api/apply` und `/api/rollout`
  sind vom Skill ausgesperrt.
- kleve-Profil: dieses Skill existiert dort nicht (Papa-Instanz bleibt read-only
  ohne Modell-Zugriff).
- Antworten auf Deutsch in einfacher Struktur: Modellname, Preis, Rolle.
