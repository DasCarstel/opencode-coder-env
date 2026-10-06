---
name: github
description: GitHub operations via the gh CLI and git (no GitHub MCP is configured). Load for issues, PRs, Actions or repository work. CRITICAL: never create or modify anything without explicit confirmation.
license: MIT
---

# GitHub (gh CLI + git)

There is **no GitHub MCP server** in this environment. Use the official `gh`
CLI and `git` through the shell — the agent handles them directly, with no
tool-definition overhead. (If a community GitHub MCP is added later, prefer it
and update this skill.)

## CRITICAL safety rule

**Never create, modify or delete anything without explicit user confirmation:**
issues, PRs, comments, gists, releases, labels, milestones, reviews, reactions.
This applies in **all** modes. When in doubt, ask.

**Read-only, allowed without confirmation:**
`gh issue list`, `gh pr list`, `gh pr view`, `gh run list`, `gh run view`,
`gh repo view`, and `gh api` GET requests.

## Authentication

- `gh` uses `GH_TOKEN` or `gh auth login`.
- If a token is needed, it lives in OpenBao — never in the repo or in shell history.

## Common operations

| Task | Command |
|------|---------|
| Clone | `git clone https://github.com/<owner>/<repo>.git` |
| List issues | `gh issue list --repo <owner>/<repo>` |
| View a PR | `gh pr view <n> --repo <owner>/<repo>` |
| Create a PR | `gh pr create --repo <owner>/<repo> ...` *(confirm first)* |
| Actions runs | `gh run list` / `gh run view <id>` |
| Repo view | `gh repo view <owner>/<repo>` |

If `gh` is not installed: `gh` is a single binary — install it, or fall back to
`git` plus the GitHub REST API via `curl` with a token from OpenBao.

## When to load this skill

- GitHub issues, PRs, Actions
- Repository browsing / code search
- CI/CD workflows
