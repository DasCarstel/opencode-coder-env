FROM nousresearch/hermes-agent:latest

USER root

# OpenCode-MCP installieren
RUN npm install -g opencode-mcp@3.0.0

# OCIS-MCP-Server installieren
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL -o /tmp/ocis-mcp.tar.gz https://github.com/owncloud/ocis-mcp-server/releases/download/v1.1.0/ocis-mcp-server_1.1.0_linux_amd64.tar.gz \
    && tar -xzf /tmp/ocis-mcp.tar.gz -C /usr/local/bin \
    && chmod +x /usr/local/bin/ocis-mcp-server \
    && rm /tmp/ocis-mcp.tar.gz

USER hermes