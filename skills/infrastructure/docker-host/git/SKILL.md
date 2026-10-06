---
name: docker-host/git
description: Git operations on Docker Host /etc/docker. Load for version controlling config changes.
license: MIT
---

# Docker Host Git Operations

## Overview

The `/etc/docker/` directory on the Docker Host (10.0.10.10) is a Git repository for tracking configuration changes.

## Quick Reference

| Operation | MCP Call |
|-----------|----------|
| Check status | `git_status()` |
| Add files | `git_add({files: ["path/to/file"]})` |
| Commit | `git_commit({message: "..."})` |
| Push | `git_push()` |
| View diff | `git_diff()` |
| View log | `git_log()` |

## Common Workflows

### Commit Config Changes
```
1. git_status()  # Check what changed
2. git_add({files: ["traefik/traefik.yml"]})
3. git_commit({message: "Update Traefik config"})
4. git_push()
```

### Review Changes
```
1. git_diff()  # See what changed
2. git_log()   # See commit history
```

## Important Notes

- **Repository location:** `/etc/docker/`
- **What to track:** Only hand-made config files
- **What NOT to track:**
  - Auto-generated configs
  - Secrets (use .env files, not tracked)
  - Large data files

## Related Skills

- **Parent:** `docker-host` - General Docker operations
- **Related:** `docker-host/filesystem` - File operations
- **Related:** `docker-host/compose` - Compose operations
