# Architektur

Systemüberblick über die OpenCode-Fleet: Coder-Host, Workspace-Container, Netzwerke,
Herdr als Einstiegspunkt, Secret-Flow, Persistenz und Update-Verhalten.

## Bausteine

| Baustein | Ort | Aufgabe |
|----------|-----|---------|
| **Coder** | `10.0.10.17` (CT 120) | provisioniert die Workspace-Container per Docker-Provider |
| **Coder-Server (Host-Compose)** | `/opt/docker-compose/coder/` auf `10.0.10.17`, Port **7080** | die Coder-Instanz selbst als Compose-Stack (`coder/` im Repo): Container `coder` plus `database` (`postgres:17`, Daten unter `/etc/docker/coder/postgres`) — wird **manuell** deployt, siehe [`coder/README.md`](../coder/README.md) |
| **Workspace-Container** | Docker auf dem Coder-Host | betreiben OpenCode, MCP-Server, Herdr |
| **Herdr** | `/opt/opencode-bin/herdr` (Bind-Mount) | Einstiegspunkt der Coder-App; verwaltet die Workspaces/Panes |
| **OpenBao** | `openbao.mueller-nas.de` | Secret-Quelle (KV v2) + SSH-CA |
| **Hestia** | Compose-Stack auf dem Coder-Host | Model-Panel, SOT-Verwaltung, Rollout (siehe [`hestia.md`](hestia.md)) |

Ein Workspace ist ein einzelner Docker-Container. Der Coder-Agent verbindet sich
direkt mit diesem Container; die eigentliche Arbeitsfläche ist Herdr, das im
Container mehrere OpenCode-Instanzen (je ein Workspace) in einem persistenten
Server verwaltet.

> **Der Coder-Server selbst ist auch nur ein Compose-Stack:** Er läuft unter
> `/opt/docker-compose/coder/` auf dem Coder-Host (Port 7080, Postgres daneben),
> ist die **Steuerzentrale** der Fleet und wird deshalb **nicht** per Push,
> sondern ausschließlich **manuell** über `.github/workflows/deploy-coder.yml`
> deployt — nach Validierung des Compose-Stands und nur für den Service `coder`,
> nie für die Datenbank. Details: [`coder/README.md`](../coder/README.md).

## Netzwerke

| Netz | Container | Charakter |
|------|-----------|-----------|
| `coder-infra` | `infrastructure`, `hermes`¹ | Zugriff auf das interne VLAN |
| `coder-gen` | `general` | isoliert, kein internes VLAN |
| `coder-mc` | `muellerconnect` | isoliert, kein internes VLAN |

¹ `hermes` ist kein Coder-Template, sondern der Hermes-Agent als Host-Deployment
([`hermes.md`](hermes.md)) — er hängt als einziger Nicht-Template-Container in
`coder-infra`.

Die Trennung ist hart: `general` und `muellerconnect` hängen **nicht** in
`coder-infra` und bekommen weder `ssh-mcp` noch SSH-Keys — nur den oCIS-MCP.

### Segment Coder ↔ Docker (`10.99.0.0/30`)

Zusätzlich zu den Docker-Netzen existiert ein **Proxmox-seitiges Segment**, das
ausschließlich diese zwei Hosts verbindet. Es trägt die Absicherung des
Hermes-API-Servers — Begründung und Verifikation in
[`hermes.md`](hermes.md#sicherheit).

| Baustein | Wert |
|----------|------|
| Bridge (Proxmox) | `vmbr10` — `bridge-ports none`, keine IP, **kein Uplink** |
| CT 100 (Docker) | `eth1` = `10.99.0.1/30` |
| CT 120 (Coder) | `eth1` = `10.99.0.2/30` |
| Nutzer | nur der Hermes-API-Server (`10.99.0.2:8642`), Traefik-Regel `hermes-api.yml` |

Im Segment existieren **nur** diese beiden Hosts: kein Uplink auf die Physis,
kein Gateway, keine VLAN-Zuordnung. Weder das LAN noch Tailscale noch der
Proxmox-Host selbst erreichen eine Adresse daraus (verifiziert: `Connection
refused` bzw. `100% packet loss`). Der Hermes-API-Server bindet ausschließlich
hieran — als `"10.99.0.2:8642:8642"` in [`hermes/docker-compose.yml`](../hermes/docker-compose.yml).
Ein blanks `"8642:8642"` würde ihn wieder im ganzen LAN öffnen.

> **Nicht im Repo versioniert.** Die Bridge steht in `/etc/network/interfaces`
> auf dem Proxmox-Host, die NICs in `/etc/pve/lxc/100.conf` und `120.conf`.
> Wiederaufbau:

```bash
# --- Proxmox-Host ---
ip link add name vmbr10 type bridge && ip link set vmbr10 up
# zusätzlich in /etc/network/interfaces:
#   auto vmbr10
#   iface vmbr10 inet manual
#           bridge-ports none
#           bridge-stp off
#           bridge-fd 0
pct set 100 -net1 name=eth1,bridge=vmbr10,ip=10.99.0.1/30
pct set 120 -net1 name=eth1,bridge=vmbr10,ip=10.99.0.2/30

# --- in beiden CTs (PVE legt /etc/systemd/network/eth1.network an) ---
networkctl reload
```

Beide `pct set`-Aufrufe sind **hotplug-fähig** (`net(\d+)` ist in
`PVE::LXC::Config` als hotpluggable behandelt, via `PVE::LXC::update_net`) — es
braucht **keinen** CT-Neustart. `networkctl reload` reicht zum Einlesen der
Adressen und verwirft keine bestehenden Verbindungen.

**Noch nicht verifiziert:** ein echter Reboot-Test. Die Konfiguration ist
persistent (`ifquery vmbr10` parst, `net1` in beiden CT-Configs,
`eth1.network` in beiden Containern), aber das Hochkommen nach einem Neustart
wurde nie durchgespielt. Falls `eth1` danach fehlt: `networkctl reload` in
beiden CTs.

## Herdr als Einstieg

- Das Binary liegt auf dem Coder-Host unter `/opt/opencode-bin/herdr` und wird
  **read-only** in den Container gemountet.
- `startup.sh` installiert daraus (bzw. per Download von `herdr.dev`, siehe
  [`coder-templates.md`](coder-templates.md)) `/usr/local/bin/herdr`, startet
  `herdr server` und legt die Workspaces des Profils an.
- Die Coder-App (`coder_app` in `main.tf`, slug `herdr`) startet `/usr/local/bin/herdr`
  als Terminal-Session.

## Secret-Flow

```
Coder-Parameter openbao_approle_secret_id
  → Env OPENBAO_APPROLE_SECRET_ID im Container
  → startup.sh: AppRole-Login (role mcp-server) → kurzlebiger Token
  → OpenBao KV v2 secret/data/mcp/*
  → /etc/opencode.env, /root/.ssh/*, opencode service set env
  → MCP-Server lesen die Werte per {env:VARIABLE}
```

Details und Pfadliste: [`secrets.md`](secrets.md).

## Persistenz

| Was | Host-Pfad | Container-Pfad |
|-----|-----------|----------------|
| Workspace (Repo, Dateien) | Docker-Volume `coder-<user>-<ws>-data` | `~/opencode-coder-env` |
| OpenCode-Sessions | `/etc/opencode-data/<user>` | `~/.local/share/opencode` |
| Tailscale-Node-Identität | Docker-Volume `coder-<user>-<ws>-tailscale` | `/var/lib/tailscale` |

Die OpenCode-Chats liegen in `~/.local/share/opencode/opencode.db` (SQLite).
Der Host-Pfad `/etc/opencode-data/<user>` hält sie über
Container-Neuerstellungen hinweg. `general` (`…-<user>-general`) und
`muellerconnect` (`…-<user>-mc`) nutzen bewusst je einen eigenen, nicht geteilten
Pfad.

## Update-Verhalten

Zwei Ebenen, bewusst getrennt:

1. **Versions-Pins (`coder-templates/versions.env`)** — Hybrid: `ssh-mcp`,
   `ocis-mcp-server` und `herdr` sind gepinnt; die **OpenCode-CLI ist bewusst
   ungepinnt** und wird bei jedem Start gegen npm-latest geprüft und ggf.
   aktualisiert. Fehlt `versions.env`, warnen die Blöcke und die installierte
   Version bleibt unverändert. Anzeige veralteter Pins: `scripts/check-updates.sh`.
2. **Repo-Inhalt** — der `entrypoint.sh` führt bei **jedem** Container-Start
   `git fetch --depth 1 origin master` + `git -c core.fileMode=false reset --hard FETCH_HEAD`
   aus (bzw. klont beim Erststart). Lokale Abweichungen im Arbeitsbaum werden
   dadurch verworfen; `workspaces/` wird zusätzlich `git clean`-ed.

Ein Rollout der SOT-Änderungen (Hestia → Container) läuft über
`.github/workflows/rollout.yml`; der Container-Start zieht ohnehin frisch nach.

## SSH: interaktiv vs. ssh-mcp

Zwei Wege, zwei Auth-Verfahren:

- **Interaktives `ssh`** (`~/.ssh/config`, nur Profil `infrastructure`):
  `startup.sh` erzeugt bei jedem Start einen ed25519-Key und lässt ihn von der
  OpenBao-SSH-CA über `ssh/sign/host-access` signieren (Principal `root`, TTL 24h).
  Die Zielhosts vertrauen der CA (`TrustedUserCAKeys`).
- **ssh-mcp** (MCP-Zugriff des Agenten): statischer ed25519-Key aus OpenBao
  (`secret/data/mcp/ssh-mcp`) → `/root/.ssh/id_ed25519_mcp`, dessen Public-Key
  in `authorized_keys` der Zielhosts liegt.

> **Grund für den Unterschied:** ssh-mcp nutzt die `ssh2`-Library, die keine
> OpenSSH-CA-Zertifikate unterstützt (`cert = true` schlägt fehl). Deshalb ein
> dauerhafter Key für den MCP-Pfad, kurzlebige Zertifikate für `ssh` von Hand.

Einmalig pro Host (Policy `host-access`, CA-Key verteilen):

```bash
export OPENBAO_ROOT_TOKEN=<root-token>
bash scripts/bootstrap-hosts.sh
```

## Kleve via Tailscale

Kleve (192.168.178.50/51/52) liegt hinter dem internen VLAN und wird über ein
eigenes Tailnet angebunden: Der Workspace ist Tailscale-Node mit Tag `tag:coder`,
ein Subnet-Router-Container auf Docker-Kleve (Tag `tag:kleve`) annonciert
`192.168.178.0/24`; ACLs trennen Workspace und Cleve-Hosts von den privaten
Geräten. Kein Port-Forwarding, kein NAT. Auth-Key: `secret/data/mcp/tailscale`.
Nur das Template `infrastructure` startet Tailscale.

Container **ohne** Tailscale (der Hermes-Agent `hermes`, siehe
[`hermes.md`](hermes.md)) erreichen Home Assistant Kleve über das TCP-Relay
`ha-forward.py` (`127.0.0.1:18123` → HA Kleve) — siehe
[`coder-templates.md`](coder-templates.md).

## Betriebsfallen: nftables und Docker

Zwei Fallen haben am 2026-10-14 auf dem Coder-Host zu einem Ausfall geführt und
einer wirkungslosen Sicherheitsregel. Beide sind kontextbezogen in
[`hermes.md`](hermes.md#sicherheit) beschrieben, hier allgemein — sie können auf
jedem Docker-Host zuschlagen.

**1. `flush ruleset` löscht die Docker-Tabellen mit.**

`flush ruleset` entfernt *alle* Tabellen des Namespace — auch `ip nat`,
`ip filter`, `ip6` und `bridge`, die Docker selbst verwaltet. Danach fehlen
Docker die NAT- und Firewall-Regeln, und die Container verlieren die
Netzverbindung. Eine eigene Regeldatei darf deshalb **nur die eigene Tabelle**
anfassen:

```nft
add table inet filter
delete table inet filter     # nur die eigene Tabelle; Docker bleibt unberührt
```

**2. Regeln in `chain input` sehen veröffentlichte Docker-Ports nie.**

Docker veröffentlicht Ports per DNAT (`ip nat`, chain `DOCKER`). Das passiert in
`PREROUTING`; die Pakete laufen danach durch die **FORWARD**-Kette, **nicht**
durch **INPUT**. Eine Zugriffsbeschränkung in `chain input` ist damit
wirkungslos — sie lädt fehlerfrei und tut nichts.

Die richtige Stelle wäre `DOCKER-USER` (`table ip filter`): Docker wertet sie
vor seinen eigenen Regeln aus und fasst sie nicht selbst an.

```bash
iptables -I DOCKER-USER -p tcp --dport <port> ! -s <erlaubte-quelle> -j DROP
```

**Konsequenz für dieses Setup:** Für den Hermes-API-Server wurde statt einer
Firewall die Netz-Isolation gewählt (Segment `10.99.0.0/30`, siehe oben) — sie
braucht keine Regeln, die man falsch schreiben kann, und ist gegen beide Fallen
immun.

> **Lehre daraus:** nftables-Syntax zu prüfen genügt nicht. Wer eine Regel scharf
> schaltet, muss vorher gegen eine echte Fremd-Quelle testen — „die Regel ist
> geladen" ist nicht „die Regel wirkt".