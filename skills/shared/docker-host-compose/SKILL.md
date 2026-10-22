---
name: docker-host-compose
description: Docker Compose on a Docker host via ssh-mcp. Load for up/down/pull/logs of Compose services, and when adding a new application (checklist incl. mandatory Homer dashboard tile). The concrete SSH profile comes from the workspace's docker-host-location skill.
license: MIT
---

# Docker Compose on the Docker Host

All commands go through `ssh-mcp` with the profile from the workspace's
`docker-host-location` skill (written below as `profile:"<docker-profil>"`).
Compose files live in `/opt/docker-compose/<service>/docker-compose.yml`.

## Quick reference

| Task | Command (via `run-command`) |
|------|------------------------------|
| List projects | `docker compose ls -a` |
| Status | `docker compose -f /opt/docker-compose/<service>/docker-compose.yml ps` |
| Start | `docker compose -f /opt/docker-compose/<service>/docker-compose.yml up -d` |
| Stop | `docker compose -f /opt/docker-compose/<service>/docker-compose.yml down` |
| Restart | `docker compose -f /opt/docker-compose/<service>/docker-compose.yml restart` |
| Pull | `docker compose -f /opt/docker-compose/<service>/docker-compose.yml pull` |
| Logs | `docker compose -f /opt/docker-compose/<service>/docker-compose.yml logs --tail 100` |

## Workflows

### Deploy / update a service
```
1. run-command({profile:"<docker-profil>", command:"docker compose -f /opt/docker-compose/traefik/docker-compose.yml pull"})
2. run-command({profile:"<docker-profil>", command:"docker compose -f /opt/docker-compose/traefik/docker-compose.yml up -d"})
3. run-command({profile:"<docker-profil>", command:"docker compose -f /opt/docker-compose/traefik/docker-compose.yml logs --tail 100"})
```

### Debug a service
```
1. run-command({profile:"<docker-profil>", command:"docker compose -f /opt/docker-compose/<service>/docker-compose.yml ps"})
2. run-command({profile:"<docker-profil>", command:"docker compose -f /opt/docker-compose/<service>/docker-compose.yml logs --tail 200"})
3. run-command({profile:"<docker-profil>", command:"docker exec <container> sh"})
```

### Add a new application (checklist)

**Rule: no new Docker application without a Homer tile.** The tile is part of the
deployment, not an optional extra — otherwise the app exists but nobody finds it.

```
1. Stack: /opt/docker-compose/<service>/ (docker-compose.yml + .env)
2. Traefik labels (router/middleware, SSO class per authentik-integration)
3. Homer tile: /etc/docker/homer/config.yml  → services[].items[]
4. Icon: /etc/docker/homer/tools/<name>.png  (referenced as assets/tools/<name>.png;
   source e.g. homarr-labs/dashboard-icons)
5. Version it: git -C /etc/docker add homer/config.yml homer/tools/<name>.png
6. Check: https://homer.mueller-nas.de (behind authentik@docker)
```

Example tile (inside the group that fits, e.g. `- name: "Service"`):

```yaml
      - name: "MyApp"
        subtitle: "short, optional"
        logo: "assets/tools/myapp.png"
        url: "https://myapp.mueller-nas.de/"
```

`config.yml` is fetched by the browser at page load, so edits go live without a
container restart. `/etc/docker` is a Git repo — commit the config and icon there.

## Directory structure

```
/opt/docker-compose/<service>/
├── docker-compose.yml
├── .env
└── .env.example
```

## Related

- Parent: `docker-host`
- `docker-host-location` – concrete profile per location
- `docker-host-filesystem`, `docker-host-git`
