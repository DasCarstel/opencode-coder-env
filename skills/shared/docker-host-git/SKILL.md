---
name: docker-host-git
description: Git operations on a Docker host /etc/docker repo via ssh-mcp. Load for version-controlling config changes. The concrete SSH profile comes from the workspace's docker-host-location skill.
license: MIT
---

# Docker Host Git Operations

`/etc/docker/` on the Docker host is a Git repository for hand-made config
files. Use `ssh-mcp` with the profile from the workspace's
`docker-host-location` skill (written below as `profile:"<docker-profil>"`).

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
1. run-command({profile:"<docker-profil>", command:"git -C /etc/docker status --short"})
2. run-command({profile:"<docker-profil>", command:"git -C /etc/docker add traefik/traefik.yml"})
3. run-command({profile:"<docker-profil>", command:"git -C /etc/docker commit -m 'Update Traefik config'"})
4. run-command({profile:"<docker-profil>", command:"git -C /etc/docker push"})
```

## Notes

- **Repository location:** `/etc/docker/`
- **Track:** only hand-made config files
- **Do not track:** auto-generated configs, secrets (use untracked `.env` files), large data

## Related

- Parent: `docker-host`
- `docker-host-location` – concrete profile per location
- `docker-host-filesystem`, `docker-host-compose`
