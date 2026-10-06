---
name: github
description: GitHub operations via official MCP server. CRITICAL: NEVER create issues, comments, or reviews without explicit user confirmation.
license: MIT
---

# GitHub MCP Server

## CRITICAL Safety Rule

**NEVER create, modify, or delete without EXPLICIT user confirmation:**

- Issues, PRs, comments, gists
- Project items, labels, milestones
- Reviews, reactions

This rule applies in **ALL modes**. When in doubt, ask.

**Allowed without confirmation (read-only):**
- Reading issues, PRs, repos, files
- Listing workflows, runs, jobs, logs
- Viewing gists, discussions, projects

## MCP Server Configuration

The official GitHub MCP server is configured in `opencode.json`:

```json
{
  "mcp": {
    "servers": {
      "github": {
        "type": "remote",
        "url": "https://api.githubcopilot.com/mcp/"
      }
    }
  }
}
```

Authentication: OAuth 2.1 (first use opens browser for GitHub login).

## Available Tool Groups

| Toolset | Operations |
|---------|-----------|
| repos | Browse/search repos, read files |
| issues | Read/create issues, comments, labels |
| pull_requests | PR lifecycle, reviews, merging |
| actions | Workflows, runs, jobs, logs |
| gists | Create/read/list gists |
| projects | Project management |
| code_security | Code scanning alerts |
| dependabot | Dependabot alerts |
| discussions | Discussion CRUD |

## When to Load This Skill

Load when working with:
- GitHub Issues, Pull Requests, Actions
- Repository browsing or code search
- GitHub Projects or Discussions
- CI/CD workflows or security alerts
