# Coder Template: datascience – Docker-in-Docker-Workspace (FernUni Data Engineering)
#
# Basis wie general (buildpack-deps:22.04-curl = Ubuntu 22.04), aber MIT Docker:
# Der Container startet seinen eigenen dockerd (privileged, cgroupns=host) und
# führt damit den FernUni-Compose-Stack aus. Der Host-Socket wird nicht genutzt.

FROM buildpack-deps:22.04-curl

# Cache-Buster für das Image (erzwingt Rebuild beim Template-Push)
ARG CACHE_DATE=1

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
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
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

# ── Entrypoint-Wrapper + DinD-Bootstrap ────────────────────────────────────
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
COPY uni-bootstrap.sh /usr/local/bin/uni-bootstrap.sh
COPY uni-envs-setup.sh /usr/local/bin/uni-envs-setup.sh
RUN chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/uni-bootstrap.sh \
             /usr/local/bin/uni-envs-setup.sh

# ENTRYPOINT: Wrapper startet DinD + Uni-Setup im Hintergrund, dann exec "$@"
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
