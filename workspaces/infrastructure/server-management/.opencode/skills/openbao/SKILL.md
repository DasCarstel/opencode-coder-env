---
name: openbao
description: OpenBao-Zugriff für Server Management — wo der Root-Token liegt (nur auf dem Docker-Host) und wie man KV-v2-Secrets liest/schreibt. Token des Workspace-Agents ist read-only. Nur laden, wenn tatsächlich Secrets aus OpenBao gelesen/geschrieben werden.
license: MIT
---

# OpenBao – Secrets lesen & schreiben (Server Management)

## Wo OpenBao läuft

- Compose-Stack auf dem Docker-Host (Profil `docker-host`, 10.0.10.10) unter
  `/opt/docker-compose/openbao/`
- Erreichbar unter `https://openbao.mueller-nas.de` (KV v2, Pfade der Form `secret/<pfad>`)

## Root-Token (nur auf dem Host!)

- `/opt/docker-compose/openbao/bootstrap.env` → Zeile `OPENBAO_ROOT_TOKEN=…`
  (Rechte 600, **nicht** versioniert)
- Zusätzlich steht „Root token" in `/opt/docker-compose/openbao/unseal-keys.txt`
  (Unseal-Keys, ebenfalls 600)

⚠️ Root-Token **nie** im Chat ausgeben, in Commits oder Repos ablegen — nur lokal
auf dem Host verwenden. SSH immer über `ssh-mcp`, nie als Raw-SSH.

## Agent-Token ist read-only

Der Token des Workspace-Agents darf **nur lesen**. Schreiben und
`sys/capabilities-self` sind verweigert (403).

## Lesen (KV v2, Agent-Token reicht)

```sh
curl -s -H "X-Vault-Token: $TOKEN" https://openbao.mueller-nas.de/v1/secret/data/<pfad>
```

→ Werte liegen unter `.data.data`.

## Schreiben (grob, Root-Token nötig)

```sh
curl -s -X POST -H "X-Vault-Token: $TOKEN" -H 'Content-Type: application/json' \
  -d '{"data":{...}}' https://openbao.mueller-nas.de/v1/secret/data/<pfad>
```

Policies/Token: `bao policy write`, `bao token create -policy=…`
(nur als Verweis, im Zweifel Doku konsultieren).

## Bewährtes Muster für den Agenten

`run-command` hat 60 s Timeout und mag keine Mehrzeiler. Deshalb:

1. Python-Skript auf dem Docker-Host ablegen: `sftp-upload-file` → `/root/<script>`
2. `run-command` → `python3 /root/<script>`
3. Das Skript liest den Root-Token **lokal** aus der Datei, macht den API-Aufruf
   und gibt **nur Status / „gesetzt" / Länge / Präfix** aus — niemals Werte.

## When to use me

- Ein Secret aus `secret/…` muss gelesen oder geschrieben werden
- Neue Credentials für einen MCP-Server oder Dienst hinterlegt werden sollen
- Nachweis nötig, wo ein bestimmter Token/Key hinterlegt ist
