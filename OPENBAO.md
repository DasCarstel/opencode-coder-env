# OpenBao – zentraler Secret-Store

OpenBao (Vault-Fork) verwaltet alle Secrets der Coder-Infrastruktur und stellt
sie den Workspaces zur Laufzeit bereit. Diese Datei enthält bewusst **nur
allgemeine Informationen** – dieses Repository ist öffentlich.

**Adresse:** https://openbao.mueller-nas.de

## Grundprinzipien

- Secrets liegen in einem verschachtelten KV-v2-Speicher und werden pro
  Workspace über eigene AppRoles (Role-ID + Secret-ID) gelesen. Jede Rolle darf
  ausschließlich die Pfade lesen, die sie wirklich braucht (Least Privilege).
- Automatische Systeme (Workspaces, Runner, Hestia) authentifizieren sich per
  AppRole, Menschen per OIDC gegen Authentik. Rechte werden über Policies
  vergeben; geteilte Root-Credentials liegen nicht im Repo und nicht im Image.
- Die Instanz läuft als Container auf dem Docker-Host; TLS und Netz-Zugriff
  regelt der Reverse-Proxy davor.
- Root- und Unseal-Material liegt ausschließlich auf dem Docker-Host (0600,
  außerhalb von Git). Es gehört nie in Repo, Image, Log oder Chat.
- Der Bootstrap ist idempotent: `scripts/bootstrap-hosts.sh` legt bzw.
  aktualisiert Policies und AppRoles, verteilt den SSH-CA-Key und erzeugt das
  Schlüsselmaterial für den Carsten-PC. Role-IDs gibt das Skript am Ende aus.

## Betrieb

- Verbindliche Detail-Dokumentation (Secret-Landkarte ohne Werte,
  Policy-Stand, Token-Übersicht, Audit-Bericht vom 2026-10-18) liegt **intern**
  im Vault unter
  `07-Projekte/Server/PRO-302 – Coder Code Server erstellen/Berichte/`.
- Wird eine AppRole neu angelegt, ändert sich ihre Role-ID. Alle betroffenen
  Workspaces brauchen dann den neuen Wert als Coder-Parameter, sonst bricht
  `startup.sh` fail-closed ab (kein OpenCode, keine Secrets, kein Sync).
  Ablauf und Prüfbefehle stehen im internen Bericht.
- Nach jeder Änderung an Policies, Rollen oder Audit-Einstellungen gilt:
  Gegentest mit einem echten Workspace-Token durchführen, niemals nur „gesetzt".

> Hinweis: Frühere Fassungen dieser Datei enthielten Pfad-, Feld- und
> Policy-Tabellen. Sie bleiben in der Git-Historie sichtbar; dieses Repo ist
> öffentlich.
