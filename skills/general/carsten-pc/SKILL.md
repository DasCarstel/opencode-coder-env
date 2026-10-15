---
name: carsten-pc
description: Resolves the ssh-mcp profile for Carsten's Windows PC. In the general workspace the only ssh-mcp target is profile "carsten-pc" (10.0.0.20). Load before running any command on the local PC.
license: MIT
---

# Carsten-PC (Windows) – ssh-mcp

This workspace (`general`) has exactly **one** ssh-mcp target: Carsten's Windows
PC. Use the profile name below wherever an ssh-mcp tool asks for `profile`.

## Mapping

| Target | Profile | Host | Notes |
|--------|---------|------|-------|
| **Carsten-PC** (this workspace) | `carsten-pc` | `10.0.0.20` | Windows, user `carst`, VLAN `10.0.0.0/24` |

No other hosts exist here: `config-general.toml` contains only this profile
(hard boundary — Krefeld/Kleve are simply not defined, like `config-cleve.toml`
omits Krefeld).

## Windows caveats

- The remote shell is `cmd.exe` unless PowerShell is configured as the default
  shell. Prefer explicit PowerShell for anything non-trivial.
- POSIX helpers (`ls`, `cat`, `grep`, `find`, `sed`) are **unreliable** on
  Windows — under PowerShell they are aliases with different semantics. Use
  `run-command` with PowerShell cmdlets (`Get-ChildItem`, `Get-Content`,
  `Select-String`) instead.
- There is **no `sudo`**: the `privileged-command` tool (which pipes a sudo
  password) does not work. Use `run-command`; interactive UAC elevation is not
  available over SSH.
- Use Windows paths (`C:\Users\carst\...`) for run commands and SFTP.

## Rule

- Always use profile **`carsten-pc`** and state it before running commands.
- If a task needs another host (Krefeld, Kleve, …), stop and tell the user — it
  is intentionally not reachable from this workspace.
