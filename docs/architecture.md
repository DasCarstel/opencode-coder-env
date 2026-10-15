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
| `coder-gen` | `general` | isoliert, kein internes VLAN; erreicht nur den Carsten-PC (`10.0.0.20`, VLAN `10.0.0.0/24`) per NAT |
| `coder-mc` | `muellerconnect` | isoliert, kein internes VLAN |

¹ `hermes` ist kein Coder-Template, sondern der Hermes-Agent als Host-Deployment
([`hermes.md`](hermes.md)) — er hängt als einziger Nicht-Template-Container in
`coder-infra`.

Die Trennung ist hart: `general` und `muellerconnect` hängen **nicht** in
`coder-infra`. `muellerconnect` bekommt weiterhin weder `ssh-mcp` noch SSH-Keys — nur den
oCIS-MCP. `general` bekommt `ssh-mcp` mit **genau einem** Ziel, dem Carsten-PC (eigener
exklusiver Key, siehe [Carsten-PC](#carsten-pc-windows)); alle anderen Hosts bleiben unerreichbar.

### Segment Coder ↔ Docker (`10.99.0.0/30`)

Zusätzlich zu den Docker-Netzen existiert ein **Proxmox-seitiges Segment**, das
ausschließlich diese zwei Hosts verbindet. Es trägt die Absicherung des
Hermes-API-Servers — Begründung in [`hermes.md`](hermes.md#sicherheit).

| Baustein | Wert |
|----------|------|
| Bridge (Proxmox) | `vmbr10` — `bridge-ports none`, keine IP, **kein Uplink** |
| CT 100 (Docker) | `eth1` = `10.99.0.1/30` |
| CT 120 (Coder) | `eth1` = `10.99.0.2/30` |
| Nutzer | nur der Hermes-API-Server (`10.99.0.2:8642`), Traefik-Regel `hermes-api.yml` |

Im Segment existieren **nur** diese beiden Hosts: kein Uplink auf die Physis,
kein Gateway, keine VLAN-Zuordnung. Das LAN, Tailscale und der Proxmox-Host
selbst haben keine Adresse darin. Der Hermes-API-Server bindet ausschließlich
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
- **ssh-mcp für den Carsten-PC**: eigener, exklusiver ed25519-Key aus
  `secret/data/mcp/ssh-mcp-pc` → `/root/.ssh/id_ed25519_mcp_pc`. Er wird **nicht** mit dem
  gemeinsamen Key geteilt; er liegt nur in den Profilen `general` und `infrastructure`.

> **Grund für den Unterschied:** ssh-mcp nutzt die `ssh2`-Library, die keine
> OpenSSH-CA-Zertifikate unterstützt (`cert = true` schlägt fehl). Deshalb ein
> dauerhafter Key für den MCP-Pfad, kurzlebige Zertifikate für `ssh` von Hand.

Einmalig pro Host (Policy `host-access`, CA-Key verteilen):

```bash
export OPENBAO_ROOT_TOKEN=<root-token>
bash scripts/bootstrap-hosts.sh
```

## Carsten-PC (Windows)

Der Carsten-PC ist der einzige Host, den der General-Workspace per `ssh-mcp` erreicht.
Er hat die IP `10.0.0.20` (User `carst`) im VLAN `10.0.0.0/24`.

- **Netzweg:** Der Container hängt in `coder-gen` bzw. `coder-infra` und erreicht den PC
  über Inter-VLAN-Routing bzw. NAT über den Docker-Host. Port 22 ist erst offen, wenn
  der OpenSSH-Server auf dem PC aktiv ist (Setup einmalig, siehe unten).
- **Eigener Key:** `secret/data/mcp/ssh-mcp-pc` → `/root/.ssh/id_ed25519_mcp_pc`.
- **Config:** `/root/.config/ssh-mcp/config-general.toml` enthält ausschließlich das
  Profil `carsten-pc` (Gruppe `pc`). Die Profile `config.toml` und `config-full.toml`
  enthalten es ebenfalls, `config-cleve.toml` nicht.
- **Einrichtung:** `scripts/setup-windows-pc-ssh.ps1` als Administrator auf dem PC
  ausführen (OpenSSH-Server, Firewall TCP/22, Public-Key ablegen, optional PowerShell als
  `DefaultShell`).
- **Windows-Einschränkungen:** kein `sudo` (`privileged-command` unbrauchbar), die Shell
  ist `cmd.exe` bzw. PowerShell, POSIX-Helfer wie `ls`/`cat`/`grep` sind unzuverlässig.
  Details: [`mcp-skills.md`](mcp-skills.md#windows-caveats-carsten-pc).
- **Empfehlung:** Für `10.0.0.20` eine DHCP-Reservierung in der UniFi anlegen, damit die
  Adresse stabil bleibt.

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