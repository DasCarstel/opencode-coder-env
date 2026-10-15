---
name: fernuni-stack
description: Betrieb des FernUni-Docker-Stacks (Deploy-DeDs-*.sh) im datascience-Workspace. Load bei Docker-, Compose-, Jupyter- oder Port-Fragen im uni-Workspace.
license: MIT
---

# FernUni-Stack (Data Engineering) im datascience-Workspace

Dieser Workspace ist ein **Docker-in-Docker**-Container: `dockerd` läuft im
Workspace selbst (privileged, `cgroupns_mode = host`). Der Host-Socket ist
bewusst **nicht** gemountet.

## Wichtige Pfade

| Pfad | Inhalt | persistent? |
|------|--------|-------------|
| `~/uni/workspace` | `workspace/`-Ordner des FernUni-Stacks (Bind-Mount-Ziel) | ja (Volume `…-uni`) |
| `~/uni/data-engineering-infrastructure` | geklonter FernUni-Klon (`workspace` ist ein Symlink auf `~/uni/workspace`) | ja |
| `~/uni/envs/ml`, `~/uni/envs/dl` | die zwei uv-Umgebungen | ja |
| `~/uni/logs` | `dockerd.log`, `jupyter.log`, `env-setup.log`, `sync.log`, `uni-bootstrap.log` | ja |
| `/var/lib/docker` | Images/Container des inneren Daemons | ja (Volume `…-docker`) |

## Ports (nicht verwechseln!)

| Port | Dienst |
|------|--------|
| **8890** | unser JupyterLab (Kernel „Python (ml)" / „Python (dl)") – läuft immer |
| **8888** | Jupyter **des FernUni-Stacks** – erst nach `./Deploy-DeDs-*.sh` |
| **8088** | Hadoop ResourceManager – erst nach Stack-Start |

Unser JupyterLab weicht bewusst auf 8890 aus, weil der FernUni-Stack
`127.0.0.1:8888` selbst belegt.

## Stack starten

```bash
cd ~/uni/data-engineering-infrastructure
git checkout "$(git tag | sort -V | tail -1)"     # neuestes Release
./Deploy-DeDs-Candle.sh    # nur Jupyter-/PySpark-Client (kleinster Test)
./Deploy-DeDs-Rocket.sh    # voller Stack (Hadoop + 2 Worker)
./Deploy-DeDs-Firework.sh  # Variante für kleine Maschinen
```

`Candle` = kleinster Test (ein Container), `Rocket`/`Firework` = voller Stack
(PySpark-Client + Hadoop-Edge + 2 Worker). Danach:

```bash
docker ps                              # erwartet: dbis-pyspark-client-node (+ Hadoop/Worker)
docker compose ls                      # laufende Compose-Projekte
docker compose -f <stack> down         # Stack wieder abbauen
```

Jupyter des Stacks: `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8888`
(erwartet 200). Den Token zeigt `docker logs dbis-pyspark-client-node`.

## Diagnose

```bash
docker info | grep -E 'Storage Driver|Server Version|Total Memory'
docker compose version                 # muss ≥ 2.32 sein (Kursanforderung)
cat /etc/opencode/env 2>/dev/null || true
tail -n 50 ~/uni/logs/dockerd.log      # Storage-Driver, Compose-Version, cgroup-Check
docker info 2>&1 | grep -i 'swap limit'   # muss LEER sein, sonst wirken Limits nicht
```

- Startet `dockerd` nicht, hat die Treiber-Probe (`overlay2` → `fuse-overlayfs`
  → `vfs`) keinen Treiber gefunden: `/var/log/dockerd.log` zeigt den echten Fehler.
- Images/Container sind persistent: nach einem Coder-Neustart zeigt
  `docker images` die FernUni-Images wieder, ohne erneuten Pull.
- Neu nachladen der Umgebungen: Marker `~/uni/envs/<name>/.ready` löschen und
  `uni-envs-setup.sh` im Hintergrund laufen lassen (Log: `~/uni/logs/env-setup.log`).
