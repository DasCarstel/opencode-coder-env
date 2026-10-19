---
name: cloudflare
description: Cloudflare-Tunnel und DNS für neue Dienste auf mueller-nas.de (OpenCode-Workspace-URLs, Apps hinter Traefik) — Tunnel-Ingress per API, DNS wetten separat, Free-Plan-SSL-Falle beachten. Nur laden, wenn ein neuer Dienst via Tunnel/DNS veröffentlicht oder ein Zugriff von außen debugged wird.
license: MIT
---

# Cloudflare – Tunnel & DNS für neue Dienste (Server Management)

## IDs (nicht geheim, dürfen referenziert werden)

| Objekt | ID |
|---|---|
| Account | `cb0e5ba424f00c26ec24cfdaca49680a` |
| Zone `mueller-nas.de` | `553ddf41be798b568b8e58a473794274` |
| Tunnel `mueller-nas` | `9bb7fb4d-2b17-4ed6-98a4-b316c6566b1f` |

## Tokens (nur in OpenBao bzw. auf dem Host, nie ausgeben)

- **Tunnel-API-Token**: OpenBao `secret/mcp/cloudflare`
  (Felder: `account_id`, `zone_id`, `api_token`, `r2_access_key_id`,
  `r2_secret_access_key`, `r2_endpoint`, `note`).
  ⚠️ Dieser Token kann **Tunnel, aber kein DNS** (DNS-Records → 403 `code=10000`).
- **DNS-Token (DNS-01, von Traefik)**: `/opt/docker-compose/traefik/.env`,
  Variable `CF_DNS_API_TOKEN` — hat Zone-DNS-Edit für `mueller-nas.de`.
  Lokal auf dem Host lesen, nie im Chat zeigen.

## Tunnel-Ingress (remotely-managed)

`GET/PUT https://api.cloudflare.com/client/v4/accounts/<acc>/cfd_tunnel/<id>/configurations`
mit Body `{"config": {...}}`.

- Regeln sind eine Liste; die **letzte** ist der Catch-all
  `{"service":"http_status:404"}` → neue Regeln **davor** einfügen.
- Muster je Regel:

```json
{"hostname":"<host>","service":"https://traefik","originRequest":{"http2Origin":true,"originServerName":"<host>"}}
```

- Immer: erst `GET` + Sicherung nach `/root/cfd-tunnel-config-backup-<zeit>.json`,
  nachher `GET` und Regelzahl + alte Hostnamen gegenprüfen.

## Wichtig: DNS entsteht NICHT automatisch per API

DNS-Records entstehen nur im **Zero-Trust-Dashboard** automatisch. Über die API
legt die Tunnel-Konfiguration **keinen** Record an (praktisch belegt: 30 s nach
dem PUT löste der Hostname weiter nur über die Wildcard). Deshalb DNS separat:

```sh
curl -s -X POST \
  -H "Authorization: Bearer $CF_DNS_API_TOKEN" -H 'Content-Type: application/json' \
  -d '{"type":"CNAME","name":"<host>","content":"<tunnel-id>.cfargotunnel.com","proxied":true,"ttl":1}' \
  https://api.cloudflare.com/client/v4/zones/<zone>/dns_records
```

## Free-Plan-Falle (SSL)

Universal SSL deckt nur **eine** Ebene (`*.mueller-nas.de`). Zwei Ebenen
(`*.apps.mueller-nas.de`) bräuchten kostenpflichtiges ACM → **keine**
`X.apps.…`-Schemata.

## Fehlerbilder (belegt)

| Fehler | Ursache |
|---|---|
| `403 code=10000` „Authentication error" | fehlendes Recht (z. B. Tunnel-Token für DNS) |
| `403 code=9109` | fehlendes Recht (allgemein) |
| `401 code=1001` | Tunnel ohne passende Freigabe |
| `/user/tokens/verify` → 401 | bei **Account-Tokens normal**; gültige Prüfung: `/accounts/<acc>/tokens/verify` |

## Verifikation von außen

```sh
# Über Cloudflare gelaufen? → `server: cloudflare` UND `cf-ray`-Header müssen da sein
curl -s -o /dev/null -D - https://<host>/
# Ursprung (LAN) direkt prüfen
curl -k -s -o /dev/null -w '%{http_code}' --resolve <host>:443:127.0.0.1 https://<host>/…
```

Ohne den `cf-ray`-Check testet man u. U. nur den LAN-Pfad.

## Traefik

- Liefert die Zertifikate (`certResolver: cloudflare`, `mueller-nas.de` +
  `*.mueller-nas.de`)
- Router-Datei: `/etc/docker/traefik/rules/coder.yml`
- File-Provider lädt Änderungen selbst — **kein** Traefik-Neustart nötig

## Ablauf „neuen Dienst veröffentlichen"

1. Dienst hinter Traefik bringen (Router + Service, YAML validieren, Sicherung)
2. DNS proxied CNAME (siehe oben)
3. Tunnel-Route auf `https://traefik` mit `originServerName`
4. Von außen mit `cf-ray` prüfen
5. Rollback-Weg notieren

## Tiefer

Details samt Port-App-Format (`<port>--<agent>--<workspace>--<owner>-coder.mueller-nas.de`,
Zugriff von außen): `docs/kopplung.md` im Repo-Root.

## When to use me

- Ein neuer Dienst (OpenCode-Workspace-URL, App) soll von außen erreichbar sein
- Ein Host löst nicht über den Tunnel / ein 401/403 der Cloudflare-API taucht auf
- Hintergrund-Debugging, ob ein Call über Cloudflare lief
