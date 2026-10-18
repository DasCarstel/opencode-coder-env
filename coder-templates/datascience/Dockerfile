# Coder Template: datascience – Docker-in-Docker-Workspace (FernUni Data Engineering)
#
# Basis wie general (buildpack-deps:22.04-curl = Ubuntu 22.04), aber MIT Docker:
# Der Container startet seinen eigenen dockerd (privileged, cgroupns=host) und
# führt damit den FernUni-Compose-Stack aus. Der Host-Socket wird nicht genutzt.

FROM buildpack-deps:22.04-curl

# ── Bake-Baseline: Toolchain ins Image (Kaltstart-Verkürzung) ───────────────
# Ohne diese Layer installiert der Container bei JEDER Neuerstellung alles
# nach: OpenCode-CLI (~500 MB), DCP (~96 MB), ocis-mcp-server – plus npm-Cache.
# Der Node-Teil lief dabei *blockierend vor* dem Coder-Agenten, der Workspace
# wurde also erst nach 20–40 s als „verbunden“ angezeigt.
#
# ⚠️ Reihenfolge: ALLE Install-Layer stehen VOR dem Cache-Buster
# `ARG CACHE_DATE` (jetzt am Dateiende). Stünde der Buster oben, würde er bei
# jedem Apply/Neustart genau diese Layers wieder aufreißen – main.tf setzt ihn
# auf timestamp(), also bei jedem Workspace-Start.
#
# Die ARG-Defaults müssen mit coder-templates/versions.env übereinstimmen.
# Maßgeblich bleibt trotzdem shared/startup.sh: es prüft die Stempel-Dateien in
# /root/.local/bin und installiert bei Abweichung zur Laufzeit nach.
# Drift anzeigen: scripts/check-updates.sh
# ssh-mcp wird hier bewusst NICHT gebacken (Profil datascience braucht es nicht).
ARG NODE_MAJOR=22
ARG OPENCODE_VERSION=2.0.32
ARG DCP_VERSION=3.2.0
ARG OCIS_MCP_VERSION=1.1.0

ENV DEBIAN_FRONTEND=noninteractive
# Interpreter, die `uv` installiert (im Image, nicht im ephemeren /root)
ENV UV_PYTHON_INSTALL_DIR=/opt/uv-python

# ── Docker CE aus dem offiziellen apt-Repo + DinD-Werkzeuge ─────────────────
# docker-compose-plugin liefert `docker compose` (≥ 2.32, Kursanforderung).
# fuse-overlayfs/iptables/uidmap: Rückfallebene und Netzwerk des inneren Daemons.
# cgroup-tools: Nachweis, dass die Speicher-Limits der inneren Container wirken.
RUN install -m 0755 -d /etc/apt/keyrings \
 && curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc \
 && chmod a+r /etc/apt/keyrings/docker.asc \
 && printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu jammy stable\n' "$(dpkg --print-architecture)" \
      > /etc/apt/sources.list.d/docker.list \
 && printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d \
 && apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends \
      docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin \
      iptables fuse-overlayfs uidmap cgroup-tools kmod iproute2 procps \
      ca-certificates curl gnupg git python3 python3-pip python3-venv pandoc \
 && rm -f /usr/sbin/policy-rc.d \
 && rm -rf /var/lib/apt/lists/*

# ── Node.js 22 (NodeSource) – wie general ──────────────────────────────────
RUN curl -fsSL "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" | bash - \
 && apt-get install -y -qq nodejs \
 && rm -rf /var/lib/apt/lists/*

# ── uv + Python 3.12 + JupyterLab (eigene venv unter /opt/jupyter) ─────────
# Unser JupyterLab läuft auf 8890 (der FernUni-Stack belegt 127.0.0.1:8888).
RUN curl -LsSf https://astral.sh/uv/install.sh | sh 2>&1 | tail -2 \
 && ln -sf /root/.local/bin/uv /usr/local/bin/uv \
 && ln -sf /root/.local/bin/uvx /usr/local/bin/uvx \
 && uv python install 3.12 \
 && uv venv --python 3.12 /opt/jupyter \
 && uv pip install --python /opt/jupyter/bin/python jupyterlab ipykernel \
 && /opt/jupyter/bin/jupyter --version

# ── npm-Toolchain (OpenCode, DCP) ──────────────────────────────────────────
# Der npm-Cache wird NICHT ins Image geschrieben, sondern per BuildKit-Cache-
# Mount gehalten: er liegt einmal im Build-Cache des Hosts (von allen vier
# Templates geteilt) statt 4× à ~1 GB im Image. Zur Laufzeit bindet main.tf
# denselben Cache als Host-Ordner /etc/npm-cache nach /root/.npm ein.
RUN --mount=type=cache,target=/root/.npm,sharing=shared \
    npm install -g "@opencode/cli@${OPENCODE_VERSION}" \
 && npm install -g "@tarquinen/opencode-dcp@${DCP_VERSION}" \
 && echo "${DCP_VERSION}" > /root/.local/bin/.dcp-version \
 && opencode --version

# ── ocis-mcp-server (ownCloud, Go-Binary aus den GitHub-Releases) ──────────
RUN curl -fsSL -o /tmp/ocis-mcp.tar.gz \
      "https://github.com/owncloud/ocis-mcp-server/releases/download/v${OCIS_MCP_VERSION}/ocis-mcp-server_${OCIS_MCP_VERSION}_linux_amd64.tar.gz" \
 && tar xzf /tmp/ocis-mcp.tar.gz -C /tmp \
 && install -m 0755 /tmp/ocis-mcp-server /root/.local/bin/ocis-mcp-server \
 && echo "${OCIS_MCP_VERSION}" > /root/.local/bin/.ocis-mcp-server-version \
 && rm -f /tmp/ocis-mcp.tar.gz /tmp/ocis-mcp-server

# ── Entrypoint-Wrapper + DinD-Bootstrap ────────────────────────────────────
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
COPY uni-bootstrap.sh /usr/local/bin/uni-bootstrap.sh
COPY uni-envs-setup.sh /usr/local/bin/uni-envs-setup.sh
COPY uni-git-setup.sh /usr/local/bin/uni-git-setup.sh
RUN chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/uni-bootstrap.sh \
             /usr/local/bin/uni-envs-setup.sh /usr/local/bin/uni-git-setup.sh

# ENTRYPOINT: Wrapper startet DinD + Uni-Setup im Hintergrund, dann exec "$@"
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

# ── Cache-Buster für das Image (erzwingt Rebuild beim Template-Push) ────────
# ⚠️ MUSS am Dateiende stehen (siehe oben): nur so bleiben die Install-Layers
# über Apply/Neustarts hinweg im Docker-Layer-Cache.
ARG CACHE_DATE=1
