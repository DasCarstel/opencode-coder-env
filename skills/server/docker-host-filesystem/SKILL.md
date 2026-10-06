---
name: docker-host-filesystem
description: File operations on the Docker host via ssh-mcp (profile "docker-host"). Load to read, write, search or list files under /etc/docker and /opt/docker-compose.
license: MIT
---

# Docker Host File Operations

Use the `ssh-mcp` SFTP tools with `profile: "docker-host"`.

| Task | Tool |
|------|------|
| List a directory | `sftp-list({profile:"docker-host", remotePath:"/etc/docker"})` |
| Read a file (text) | `sftp-download({profile:"docker-host", remotePath:"/etc/docker/<service>/config.yml"})` |
| Write a file (text) | `sftp-upload({profile:"docker-host", remotePath:"...", content:"..."})` |
| Upload a local file | `sftp-upload-file({profile:"docker-host", localPath:"<inside /root/.ssh-mcp-transfers>", remotePath:"...", overwrite:true})` |
| Download a remote file | `sftp-download-file({profile:"docker-host", remotePath:"...", localPath:"<inside /root/.ssh-mcp-transfers>"})` |
| Search by name | `run-command({profile:"docker-host", command:"find /etc/docker -name '*.yml'"})` |
| Show a file | `run-command({profile:"docker-host", command:"sed -n '1,80p' /etc/docker/<service>/config.yml"})` |

> `sftp-upload-file` / `sftp-download-file` require the local path to be inside
> `transferRoot` (`/root/.ssh-mcp-transfers`). For short text, `sftp-download` /
> `sftp-upload` are simpler.

## Workflows

### Read a config
```
sftp-download({profile:"docker-host", remotePath:"/etc/docker/traefik/traefik.yml"})
```

### Update a config
```
1. sftp-download({profile:"docker-host", remotePath:"/etc/docker/<service>/config.yml"})
2. sftp-upload({profile:"docker-host", remotePath:"/etc/docker/<service>/config.yml", content:"<full new content>"})
3. sftp-download({profile:"docker-host", remotePath:"/etc/docker/<service>/config.yml"})   # verify
```

### Search for files
```
run-command({profile:"docker-host", command:"find /etc/docker -name '*.yml'"})
```

## Important paths

| Path | Description |
|------|-------------|
| `/opt/docker-compose/<service>/` | Compose files |
| `/etc/docker/<service>/` | Service config + data |
| `/etc/docker/traefik/` | Traefik config |
| `/etc/docker/pihole/config/` | Pi-hole config |

## Related

- Parent: `docker-host`
- `docker-host-compose`, `docker-host-git`
