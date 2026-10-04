FROM buildpack-deps:22.04-curl

# Zusätzliche Tools installieren
RUN apt-get update && \
    apt-get install -y \
      gnupg \
      git \
    && rm -rf /var/lib/apt/lists/*
