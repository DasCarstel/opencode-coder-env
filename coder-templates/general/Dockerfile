FROM buildpack-deps:22.04-curl

# ── Bake-Baseline: Toolchain ins Image (Kaltstart-Verkürzung) ───────────────
# Ohne diese Layer installiert der Container bei JEDER Neuerstellung alles
# nach: Node 22, uv, OpenCode-CLI (~500 MB), DCP (~96 MB), ssh-mcp (~78 MB),
# ocis-mcp-server – plus ~500 MB npm-Cache. Der Node-Teil lief dabei sogar
# *blockierend vor* dem Coder-Agenten, der Workspace wurde also erst nach
# 20–40 s als „verbunden“ angezeigt.
#
# ⚠️ Reihenfolge: ALLE Install-Layer stehen VOR dem Cache-Buster
# `ARG CACHE_DATE` (jetzt am Dateiende). Stünde der Buster oben, würde er bei
# jedem Apply/Neustart genau diese Layers wieder aufreißen – main.tf setzt ihn
# auf timestamp(), also bei jedem Workspace-Start.
#
# Die ARG-Defaults müssen mit coder-templates/versions.env übereinstimmen.
# Maßgeblich bleibt trotzdem shared/startup.sh: es prüft die Stempel-Dateien in
# /root/.local/bin und installiert bei Abweichung zur Laufzeit nach (z. B.
# wenn OpenCode in npm neuer ist als der Bake). Drift anzeigen:
# scripts/check-updates.sh
ARG NODE_MAJOR=22
ARG OPENCODE_VERSION=2.0.32
ARG DCP_VERSION=3.2.0
ARG SSH_MCP_VERSION=2.19.1
ARG OCIS_MCP_VERSION=1.1.0

ENV DEBIAN_FRONTEND=noninteractive

# ── 1. Grund-Tools + Node.js 22 (NodeSource) ────────────────────────────────
RUN apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends \
      ca-certificates curl git gnupg python3 python3-pip \
 && curl -fsSL "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" | bash - \
 && apt-get install -y -qq nodejs \
 && rm -rf /var/lib/apt/lists/* \
 && node --version && npm --version

# ── 2. uv / uvx (Astral) – von uvx-basierten MCP-Servern (authentik) genutzt ─
RUN curl -LsSf https://astral.sh/uv/install.sh | sh 2>&1 | tail -2 \
 && ln -sf /root/.local/bin/uv  /usr/local/bin/uv \
 && ln -sf /root/.local/bin/uvx /usr/local/bin/uvx \
 && uv --version

# ── 3. npm-Toolchain (OpenCode, DCP, ssh-mcp) ───────────────────────────────
# Die Stempel-Dateien .dcp-version / .ssh-mcp-version sind genau die, die
# startup.sh prüft – ohne sie würde es trotz Bake erneut installieren.
# Der npm-Cache wird NICHT ins Image geschrieben, sondern per BuildKit-Cache-
# Mount gehalten: er liegt einmal im Build-Cache des Hosts (von allen vier
# Templates geteilt) statt 4× à ~1 GB im Image. Zur Laufzeit bindet main.tf
# denselben Cache als Host-Ordner /etc/npm-cache nach /root/.npm ein — so bleibt
# die Nachinstallation bei veraltetem Bake schnell, ohne das Image zu vergrößern.
RUN --mount=type=cache,target=/root/.npm,sharing=shared \
    npm install -g "@opencode/cli@${OPENCODE_VERSION}" \
 && npm install -g "@tarquinen/opencode-dcp@${DCP_VERSION}" \
 && echo "${DCP_VERSION}" > /root/.local/bin/.dcp-version \
 && npm install -g "ssh-mcp@${SSH_MCP_VERSION}" \
 && echo "${SSH_MCP_VERSION}" > /root/.local/bin/.ssh-mcp-version \
 && opencode --version

# ── 4. ocis-mcp-server (ownCloud, Go-Binary aus den GitHub-Releases) ────────
RUN curl -fsSL -o /tmp/ocis-mcp.tar.gz \
      "https://github.com/owncloud/ocis-mcp-server/releases/download/v${OCIS_MCP_VERSION}/ocis-mcp-server_${OCIS_MCP_VERSION}_linux_amd64.tar.gz" \
 && tar xzf /tmp/ocis-mcp.tar.gz -C /tmp \
 && install -m 0755 /tmp/ocis-mcp-server /root/.local/bin/ocis-mcp-server \
 && echo "${OCIS_MCP_VERSION}" > /root/.local/bin/.ocis-mcp-server-version \
 && rm -f /tmp/ocis-mcp.tar.gz /tmp/ocis-mcp-server

# ── 5. Cache-Buster für das Image (erzwingt Rebuild beim Template-Push) ─────
# ⚠️ MUSS am Dateiende stehen (siehe oben): nur so bleiben die Install-Layers
# über Apply/Neustarts hinweg im Docker-Layer-Cache.
ARG CACHE_DATE=1

# Entrypoint-Wrapper: wird bei JEDEM Container-Start ausgeführt
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# ENTRYPOINT: Wrapper macht Setup im Hintergrund, dann exec "$@"
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
