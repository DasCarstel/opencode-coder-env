---
name: create-skill
description: Create new OpenCode skills with proper structure and frontmatter. Load when creating or modifying skills.
license: MIT
---

# Creating OpenCode Skills

Skills are reusable instructions loaded on-demand via the `skill` tool.

## File Locations

Create skills in one of these locations:

- **Project-local:** `.opencode/skills/<id>/SKILL.md`
- **Global:** `~/.config/opencode/skills/<id>/SKILL.md`
- **Repo group:** `skills/<group>/<id>/SKILL.md` (e.g. `skills/server/docker-host-git/`)

Repo groups are wired up per workspace through the `skills` array in
`opencode.json` (paths are relative to the workspace working directory):

```json
{ "skills": ["../../../skills/server", "../../../skills/shared"] }
```

A source root (e.g. `skills/server`) contributes every `<id>/SKILL.md` below it.

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

## ID Rules

In OpenCode V2 the skill **ID comes from the file path** (the directory that
contains `SKILL.md`). The frontmatter `name` is only a display label.

- Keep the directory lowercase kebab-case so the ID is portable: `^[a-z0-9]+(-[a-z0-9]+)*$`
- Avoid generic directory names (`compose`, `git`, `filesystem`) — prefix them
  (`docker-host-compose`) to prevent ID collisions.
- Prefer flat directories over nesting: `docker-host/compose/SKILL.md` yields the
  ID `compose`, **not** `docker-host/compose`.

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
skill({ id: "skill-id" })
```

Available skills appear in the tool description for the agent to discover.
