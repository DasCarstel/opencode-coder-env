---
name: docker-host/filesystem
description: File operations on Docker Host. Load for reading, writing, searching files on the host at 10.0.10.10.
license: MIT
---

# Docker Host Filesystem Operations

## Quick Reference

| Operation | MCP Call |
|-----------|----------|
| Read file | `read_text_file({path: "/path/to/file"})` |
| Write file | `write_text_file({path: "/path/to/file", content: "..."})` |
| Edit file | `edit_file({path, oldText, newText})` |
| Search files | `search_files({pattern: "*.yml", path: "/etc/docker"})` |
| List directory | `list_directory({path: "/etc/docker"})` |
| Get file info | `get_file_info({path: "/path/to/file"})` |

## Common Workflows

### Read a Config File
```
read_text_file({path: "/etc/docker/traefik/traefik.yml"})
```

### Update a Config
```
1. read_text_file({path: "/etc/docker/service/config.yml"})
2. edit_file({path: "/etc/docker/service/config.yml", oldText: "...", newText: "..."})
3. read_text_file({path: "/etc/docker/service/config.yml"})  # Verify
```

### Search for Files
```
search_files({pattern: "*.yml", path: "/etc/docker"})
```

### Check Directory Structure
```
list_directory({path: "/etc/docker/traefik"})
```

## Important Paths

| Path | Description |
|------|-------------|
| `/opt/docker-compose/<service>/` | Compose files |
| `/etc/docker/<service>/` | Service configs |
| `/etc/docker/traefik/` | Traefik config |
| `/etc/docker/pihole/config/` | Pi-hole config |

## Related Skills

- **Parent:** `docker-host` - General Docker operations
- **Related:** `docker-host/compose` - Compose operations
- **Related:** `docker-host/git` - Git operations
