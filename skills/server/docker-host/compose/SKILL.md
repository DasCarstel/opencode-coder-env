---
name: docker-host/compose
description: Docker Compose operations. Load for detailed Compose project management (up, down, pull, logs, etc.).
license: MIT
---

# Docker Compose Operations

## Quick Reference

| Operation | MCP Call |
|-----------|----------|
| List services | `compose_ps()` |
| Start service | `compose_up({service: "name"})` |
| Stop service | `compose_down({service: "name"})` |
| Restart service | `compose_restart({service: "name"})` |
| Pull images | `compose_pull({service: "name"})` |
| View logs | `compose_logs({service: "name"})` |
| List all projects | `compose_list()` |

## Common Workflows

### Deploy a Service
```
1. compose_pull({service: "traefik"})
2. compose_up({service: "traefik"})
3. compose_logs({service: "traefik"})
```

### Update All Services
```
1. compose_pull()  # Pull all images
2. compose_up()    # Recreate containers
```

### Debug a Service
```
1. compose_ps({service: "name"})  # Check status
2. compose_logs({service: "name"}) # View logs
3. docker_exec({container, command: "sh"})  # Shell into container
```

## Directory Structure

```
/opt/docker-compose/<service>/
├── docker-compose.yml
├── .env
└── .env.example
```

## Related Skills

- **Parent:** `docker-host` - General Docker operations
- **Related:** `docker-host/filesystem` - File operations
- **Related:** `docker-host/git` - Git operations
