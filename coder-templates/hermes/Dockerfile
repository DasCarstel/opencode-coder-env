FROM node:22-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    jq \
    python3 \
    python3-pip \
    openssl \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install --no-cache-dir uv

RUN curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash -s -- --non-interactive --prefix /usr/local

RUN mkdir -p /root/.hermes

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
