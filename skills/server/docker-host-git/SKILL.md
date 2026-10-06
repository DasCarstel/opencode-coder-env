---
name: docker-host-git
description: Git operations on the Docker host /etc/docker repo via ssh-mcp (profile "docker-host"). Load for version-controlling config changes.
license: MIT
---

# Docker Host Git Operations

`/etc/docker/` on the Docker host (`10.0.10.10`) is a Git repository for
hand-made config files. Use `ssh-mcp` with `profile: "docker-host"`.

## Quick reference

| Task | Command (via `run-command`) |
|------|------------------------------|
| Status | `git -C /etc/docker status --short` |
| Diff | `git -C /etc/docker diff` |
| Add | `git -C /etc/docker add <path>` |
| Commit | `git -C /etc/docker commit -m "<message>"` |
| Push | `git -C /etc/docker push` |
| Log | `git -C /etc/docker log --oneline -20` |

## Workflow: commit config changes
```
1. run-command({profile:"docker-host", command:"git -C /etc/docker status --short"})
2. run-command({profile:"docker-host", command:"git -C /etc/docker add traefik/traefik.yml"})
3. run-command({profile:"docker-host", command:"git -C /etc/docker commit -m 'Update Traefik config'"})
4. run-command({profile:"docker-host", command:"git -C /etc/docker push"})
```

## Notes

- **Repository location:** `/etc/docker/`
- **Track:** only hand-made config files
- **Do not track:** auto-generated configs, secrets (use untracked `.env` files), large data

## Related

- Parent: `docker-host`
- `docker-host-filesystem`, `docker-host-compose`
