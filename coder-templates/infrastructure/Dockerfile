FROM ubuntu:22.04

# Installiere curl und andere notwendige Tools
RUN apt-get update && \
    apt-get install -y \
      curl \
      ca-certificates \
      gnupg \
      git \
    && rm -rf /var/lib/apt/lists/*
