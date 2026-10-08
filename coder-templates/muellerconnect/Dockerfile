FROM buildpack-deps:22.04-curl

# Cache-Buster für das Image (erzwingt Rebuild beim Template-Push)
ARG CACHE_DATE=1

# Entrypoint-Wrapper: wird bei JEDEM Container-Start ausgeführt
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# ENTRYPOINT: Wrapper macht Setup im Hintergrund, dann exec "$@"
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

# ENTRYPOINT: Wrapper wird bei JEDEM Container-Start ausgeführt
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
