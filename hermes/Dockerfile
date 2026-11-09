FROM nousresearch/hermes-agent:latest

USER root

# OpenCode-MCP installieren
RUN npm install -g opencode-mcp@3.0.0

# --- Picnic-MCP (ivo-toby/mcp-picnic) ----------------------------------------
# Supermarkt-Bestellungen ueber die Picnic-API (DE/NL/FR). Laeuft per stdio wie
# spotify/yazio; gestartet wird ueber /opt/data/picnic/run.sh, das die
# Zugangsdaten aus OpenBao (secret/data/mcp/picnic) laedt.
# Achtung: das npm-Bin heisst irrtuemlich "mcp-server-template" (Template-Rest
# des Upstreams) — deshalb spricht run.sh den echten Entrypoint
# bin/mcp-server.js direkt an, statt sich auf den Bin-Namen zu verlassen.
# Solo-gepflegtes Projekt: fester Pin, kein Auto-Update.
ARG PICNIC_MCP_VERSION=1.15.1
RUN npm install -g "mcp-picnic@${PICNIC_MCP_VERSION}"

# --- Google-Calendar-MCP (self-hosted, @cocal/google-calendar-mcp) -----------
# Ersatz fuer den offiziellen Google-Calendar-MCP-Server
# (https://calendarmcp.googleapis.com/mcp/v1): der verlangt eine Enrollment im
# Google Workspace Developer Preview Program und ist damit fuer private
# @gmail.com-Konten nicht nutzbar (Tool-Aufruf -> "requires that your Google
# Cloud project is enrolled in the Google Workspace Developer Preview Program").
# Node >= 20 ist erfuellt (Basis-Image bringt 26.7.0). Laeuft per stdio wie
# spotify und die oCIS-Server; Tokens/Credentials liegen ausserhalb des Repos
# unter /opt/data/gcal/ (siehe hermes/scripts/gcal_auth.py).
ARG GCAL_MCP_VERSION=2.7.0
RUN npm install -g "@cocal/google-calendar-mcp@${GCAL_MCP_VERSION}"

# OCIS-MCP-Server installieren
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL -o /tmp/ocis-mcp.tar.gz https://github.com/owncloud/ocis-mcp-server/releases/download/v1.1.0/ocis-mcp-server_1.1.0_linux_amd64.tar.gz \
    && tar -xzf /tmp/ocis-mcp.tar.gz -C /usr/local/bin \
    && chmod +x /usr/local/bin/ocis-mcp-server \
    && rm /tmp/ocis-mcp.tar.gz

# --- Spotify-MCP-Server (marcelmarais/spotify-mcp-server) --------------------
# Der Server verlangt Node >= 26.8.1; das Basis-Image bringt 26.7.0 mit. Deshalb
# eine eigene Node-Version unter /opt/node. Bewusst NICHT /usr/local/bin/node
# ueberschreiben: das ist ein Symlink in den von Hermes selbst verwalteten
# Baum /opt/hermes/tools und wuerde beim naechsten Hermes-Update zurueckfallen.
ARG NODE_VERSION=26.12.1
# Gepinnter Commit (es gibt kein npm-Release). Stand 2026-10-22.
ARG SPOTIFY_MCP_COMMIT=ceb6ba7c62ac94cf4580d7a66459e6aef2e081a8
RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
      amd64) node_arch="x64" ;; \
      arm64) node_arch="arm64" ;; \
      *) echo "nicht unterstuetzte Architektur: $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o /tmp/node.tar.gz "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${node_arch}.tar.gz"; \
    mkdir -p /opt/node; \
    tar -xzf /tmp/node.tar.gz -C /opt/node --strip-components=1; \
    rm -f /tmp/node.tar.gz; \
    /opt/node/bin/node --version

# Build aus dem gepinnten Commit: npm ci (inkl. devDependencies fuer tsc),
# dann npm run build nach /opt/spotify-mcp/build und Runtime-Deps behalten.
RUN set -eux; \
    curl -fsSL -o /tmp/spotify-mcp.tar.gz "https://github.com/marcelmarais/spotify-mcp-server/archive/${SPOTIFY_MCP_COMMIT}.tar.gz"; \
    tar -xzf /tmp/spotify-mcp.tar.gz -C /tmp; \
    mkdir -p /opt/spotify-mcp; \
    cp -a "/tmp/spotify-mcp-server-${SPOTIFY_MCP_COMMIT}/." /opt/spotify-mcp/; \
    rm -rf /tmp/spotify-mcp.tar.gz "/tmp/spotify-mcp-server-${SPOTIFY_MCP_COMMIT}"; \
    cd /opt/spotify-mcp; \
    PATH="/opt/node/bin:$PATH" npm ci; \
    PATH="/opt/node/bin:$PATH" npm run build; \
    PATH="/opt/node/bin:$PATH" npm prune --omit=dev; \
    rm -rf src test .github

# Der Server liest seine Zugangsdaten aus ../spotify-config.json relativ zu
# build/ — also /opt/spotify-mcp/spotify-config.json. Der Symlink zeigt ins
# Docker-Volume /opt/data, damit Client-Secret und Refresh-Token Deploys und
# Image-Rebuilds ueberleben (die Datei selbst liegt bewusst ausserhalb des Repos).
RUN ln -sfn /opt/data/spotify/spotify-config.json /opt/spotify-mcp/spotify-config.json

# --- YAZIO-MCP (Fork DasCarstel/yazio-mcp von tomastaker/yazio-mcp) ---------
# Upstream ist ein Ein-Personen-Projekt (letzter Upstream-Commit 2026-10-14).
# Deshalb wird nicht "latest" gebaut, sondern ein fester Commit unseres Forks.
# Gestartet wird NICHT direkt, sondern ueber /opt/data/yazio/run.sh (aus
# hestia/scripts/yazio_env.py): der Wrapper laedt die Secrets aus
# /opt/data/yazio/.env und setzt sie nur fuer diesen Prozess. Damit stehen in
# der config.yaml keine YAZIO-Zugangsdaten.
# Node >= 20 ist erfuellt; gebaut wird mit /opt/node (siehe Spotify oben).
ARG YAZIO_MCP_COMMIT=d326b1bc70ce2e214be3f412f9a887277fc73ef4
RUN set -eux; \
    curl -fsSL -o /tmp/yazio-mcp.tar.gz "https://github.com/DasCarstel/yazio-mcp/archive/${YAZIO_MCP_COMMIT}.tar.gz"; \
    tar -xzf /tmp/yazio-mcp.tar.gz -C /tmp; \
    mkdir -p /opt/yazio-mcp; \
    cp -a "/tmp/yazio-mcp-${YAZIO_MCP_COMMIT}/." /opt/yazio-mcp/; \
    rm -rf /tmp/yazio-mcp.tar.gz "/tmp/yazio-mcp-${YAZIO_MCP_COMMIT}"; \
    cd /opt/yazio-mcp; \
    PATH="/opt/node/bin:$PATH" npm ci; \
    PATH="/opt/node/bin:$PATH" npm run build; \
    PATH="/opt/node/bin:$PATH" npm prune --omit=dev; \
    rm -rf src tests scripts .github

USER hermes
