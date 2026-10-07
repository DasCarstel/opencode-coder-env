---
name: docker-host-filesystem
description: File operations on a Docker host via ssh-mcp. Load to read, write, search or list files under /etc/docker and /opt/docker-compose. The concrete SSH profile comes from the workspace's docker-host-location skill.
license: MIT
---

# Docker Host File Operations

Use the `ssh-mcp` SFTP tools with the profile from the workspace's
`docker-host-location` skill (written below as `profile:"<docker-profil>"`).

| Task | Tool |
|------|------|
| List a directory | `sftp-list({profile:"<docker-profil>", remotePath:"/etc/docker"})` |
| Read a file (text) | `sftp-download({profile:"<docker-profil>", remotePath:"/etc/docker/<service>/config.yml"})` |
| Write a file (text) | `sftp-upload({profile:"<docker-profil>", remotePath:"...", content:"..."})` |
| Upload a local file | `sftp-upload-file({profile:"<docker-profil>", localPath:"<inside /root/.ssh-mcp-transfers>", remotePath:"...", overwrite:true})` |
| Download a remote file | `sftp-download-file({profile:"<docker-profil>", remotePath:"...", localPath:"<inside /root/.ssh-mcp-transfers>"})` |
| Search by name | `run-command({profile:"<docker-profil>", command:"find /etc/docker -name '*.yml'"})` |
| Show a file | `run-command({profile:"<docker-profil>", command:"sed -n '1,80p' /etc/docker/<service>/config.yml"})` |

> `sftp-upload-file` / `sftp-download-file` require the local path to be inside
> `transferRoot` (`/root/.ssh-mcp-transfers`). For short text, `sftp-download` /
> `sftp-upload` are simpler.

## Workflows

### Read a config
```
sftp-download({profile:"<docker-profil>", remotePath:"/etc/docker/traefik/traefik.yml"})
```

### Update a config
```
1. sftp-download({profile:"<docker-profil>", remotePath:"/etc/docker/<service>/config.yml"})
2. sftp-upload({profile:"<docker-profil>", remotePath:"/etc/docker/<service>/config.yml", content:"<full new content>"})
3. sftp-download({profile:"<docker-profil>", remotePath:"/etc/docker/<service>/config.yml"})   # verify
```

### Search for files
```
run-command({profile:"<docker-profil>", command:"find /etc/docker -name '*.yml'"})
```

## Important paths

| Path | Description |
|------|-------------|
| `/opt/docker-compose/<service>/` | Compose files |
| `/etc/docker/<service>/` | Service config + data |
| Location-specific extras | see `docker-host-location` skill |

## Related

- Parent: `docker-host`
- `docker-host-location` – concrete profile per location
- `docker-host-compose`, `docker-host-git`
