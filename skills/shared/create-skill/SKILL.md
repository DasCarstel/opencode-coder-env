---
name: create-skill
description: Create new OpenCode skills with proper structure and frontmatter. Load when creating or modifying skills.
license: MIT
---

# Creating OpenCode Skills

Skills are reusable instructions loaded on-demand via the `skill` tool.

## File Locations

Create skills in one of these locations:

- **Project-local:** `.opencode/skills/<name>/SKILL.md`
- **Global:** `~/.config/opencode/skills/<name>/SKILL.md`
- **Shared (in repo):** `skills/shared/<name>/SKILL.md` or `skills/<workspace>/<name>/SKILL.md`

## Required Structure

```markdown
---
name: <skill-name>
description: <1-1024 chars description>
license: MIT
---

## What I do
<instructions>

## When to use me
<trigger conditions>
```

## Name Rules

- 1-64 characters
- Lowercase alphanumeric with hyphens
- No leading/trailing hyphens
- No consecutive hyphens
- Must match directory name

Regex: `^[a-z0-9]+(-[a-z0-9]+)*$`

## Creating a Skill

1. Create directory: `mkdir -p <location>/<name>`
2. Write `SKILL.md` with frontmatter
3. Commit to repository

## Example Skill

```markdown
---
name: docker-deploy
description: Deploy Docker containers with proper Traefik routing
license: MIT
---

## What I do
- Build and deploy Docker containers
- Configure Traefik labels for routing
- Set up SSL certificates via Cloudflare

## When to use me
Use when deploying new services or updating existing Docker deployments.
```

## Frontmatter Fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Skill identifier (must match directory) |
| `description` | Yes | Short description for tool listing |
| `license` | No | License identifier (e.g., MIT) |

## Best Practices

1. Keep descriptions specific enough for agents to choose correctly
2. Include concrete examples and commands
3. Document prerequisites and setup steps
4. Group related instructions under clear headings
5. Use markdown formatting for readability

## Using Skills

Agents load skills via the tool:

```
skill({ name: "skill-name" })
```

Available skills appear in the tool description for the agent to discover.
