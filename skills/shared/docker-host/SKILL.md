---
name: docker-host
description: Docker Host operations via the ssh-mcp server. Load for containers, Compose, files or Git on any Docker host. The concrete SSH profile comes from the workspace's docker-host-location skill — never hardcode a profile.
license: MIT
---

# Docker Host Skill

## Rule: resolve the profile first

This skill is **location-independent**. The concrete ssh-mcp profile (and host)
is defined in the workspace's `docker-host-location` skill. Read that first:

- Find the location skill, take its `profile` value and use it in **every**
  command below (written as `profile:"<docker-profil>"`).
- If the user names a location (e.g. "Krefeld", "Kleve"), pick the matching
  profile from the location skill's table.
- **Never** hardcode a profile or IP here. If no location skill is loaded or
  the profile is ambiguous, ask the user which host is meant.

## Rule: never raw SSH

Never call `ssh` from the local shell. Use the `ssh-mcp` tools with
`profile:"<docker-profil>"`:

| Task | Tool |
|------|------|
| Run any command | `run-command({profile:"<docker-profil>", command:"..."})` |
| Read-only command | `read-command({profile:"<docker-profil>", command:"..."})` |
| Command with sudo | `privileged-command({profile:"<docker-profil>", command:"..."})` |
| List a directory | `sftp-list({profile:"<docker-profil>", remotePath:"/etc/docker"})` |
| Read / write a small text file | `sftp-download` / `sftp-upload` |
| Large or binary file | `sftp-download-file` / `sftp-upload-file` |

The docker profiles run with `role=admin`, `approvalPolicy=auto`, so
`run-command` covers everything. Prefer it; use `read-command` only when a
read-only allowlisted command is enough.

## Quick reference

### Containers
```
run-command({profile:"<docker-profil>", command:"docker ps -a --format '{{.Names}}\t{{.Status}}'"})
run-command({profile:"<docker-profil>", command:"docker logs --tail 100 <name>"})
run-command({profile:"<docker-profil>", command:"docker inspect <name>"})
run-command({profile:"<docker-profil>", command:"docker exec <name> <cmd>"})
```

### Compose
```
run-command({profile:"<docker-profil>", command:"docker compose -f /opt/docker-compose/<service>/docker-compose.yml ps"})
```

### Files
```
sftp-list({profile:"<docker-profil>", remotePath:"/etc/docker/<service>"})
sftp-download({profile:"<docker-profil>", remotePath:"/etc/docker/<service>/config.yml"})
```

### Git (`/etc/docker` is a Git repository)
```
run-command({profile:"<docker-profil>", command:"git -C /etc/docker status --short"})
```

## Directory structure

```
/opt/docker-compose/<service>/   # docker-compose.yml (+ .env)
/etc/docker/<service>/           # service config + data
/etc/docker/                     # Git repository (hand-made configs)
```

## Sub-skills

- `docker-host-location` – concrete profile + host per location (load this too!)
- `docker-host-compose` – Compose project management
- `docker-host-filesystem` – file operations
- `docker-host-git` – Git in `/etc/docker`
