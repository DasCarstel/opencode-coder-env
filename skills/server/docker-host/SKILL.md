---
name: docker-host
description: Docker Host operations via SSH at 10.0.10.10. Load for Docker container, Compose, file, or Git operations on the host.
license: MIT
---

# Docker Host Skill

## Connection Details

- **Host:** 10.0.10.10
- **User:** root
- **SSH:** `ssh Docker`

## Available MCP Servers

| MCP Server | Purpose |
|-----------|---------|
| `docker` | Container operations (run, stop, logs, etc.) |
| `compose` | Compose project management (up, down, pull, etc.) |
| `filesystem` | File operations (read, write, search, etc.) |
| `git-docker-host` | Git operations on /etc/docker |

## Quick Reference

### Container Operations
- List: `docker_ps()`
- Run: `docker_run({image, name, ...})`
- Stop: `docker_stop({container})`
- Logs: `docker_logs({container})`

### Compose Operations
- Up: `compose_up({service, ...})`
- Down: `compose_down({service, ...})`
- Pull: `compose_pull({service, ...})`
- Logs: `compose_logs({service, ...})`

### File Operations
- Read: `read_text_file({path})`
- Write: `write_text_file({path, content})`
- Search: `search_files({pattern, path})`
- List: `list_directory({path})`

### Git Operations
- Status: `git_status()`
- Add: `git_add({files})`
- Commit: `git_commit({message})`
- Push: `git_push()`

## Directory Structure

```
/opt/docker-compose/<service>/
├── docker-compose.yml
├── .env
└── .env.example

/etc/docker/<service>/
├── config/
└── data/
```

## Related Skills

- **Sub-Skills:**
  - `docker-host/compose` - Detailed Compose operations
  - `docker-host/filesystem` - Detailed file operations
  - `docker-host/git` - Detailed Git operations

- **Other Skills:**
  - `authentik` - SSO configuration
  - `home-assistant` - HA configuration
