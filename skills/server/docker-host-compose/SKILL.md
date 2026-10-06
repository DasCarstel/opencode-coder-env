---
name: docker-host-compose
description: Docker Compose on the Docker host via ssh-mcp (profile "docker-host"). Load for up/down/pull/logs of Compose services.
license: MIT
---

# Docker Compose on the Docker Host

All commands go through `ssh-mcp` with `profile: "docker-host"`. Compose files
live in `/opt/docker-compose/<service>/docker-compose.yml`.

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
1. run-command({profile:"docker-host", command:"docker compose -f /opt/docker-compose/traefik/docker-compose.yml pull"})
2. run-command({profile:"docker-host", command:"docker compose -f /opt/docker-compose/traefik/docker-compose.yml up -d"})
3. run-command({profile:"docker-host", command:"docker compose -f /opt/docker-compose/traefik/docker-compose.yml logs --tail 100"})
```

### Debug a service
```
1. run-command({profile:"docker-host", command:"docker compose -f /opt/docker-compose/<service>/docker-compose.yml ps"})
2. run-command({profile:"docker-host", command:"docker compose -f /opt/docker-compose/<service>/docker-compose.yml logs --tail 200"})
3. run-command({profile:"docker-host", command:"docker exec <container> sh"})
```

## Directory structure

```
/opt/docker-compose/<service>/
├── docker-compose.yml
├── .env
└── .env.example
```

## Related

- Parent: `docker-host`
- `docker-host-filesystem`, `docker-host-git`
