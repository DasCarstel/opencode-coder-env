# setup-windows-pc-ssh.ps1 – Einmal-Setup für den ssh-mcp-Zugang zum Carsten-PC
#
# Auf dem Windows-PC **als Administrator** ausführen, z. B.:
#
#   powershell -ExecutionPolicy Bypass -File setup-windows-pc-ssh.ps1 `
#     -PublicKey "ssh-ed25519 AAAA... ssh-mcp-carsten-pc" -SetDefaultShellPowerShell
#
# Den einzutragenden Public-Key liefert:
#   bash scripts/bootstrap-hosts.sh   (Abschnitt "Secret ssh-mcp-pc")
#
# Danach muss der Sanity-Check vom Workspace-Container aus gelingen:
#   bash -c 'cat </dev/null >/dev/tcp/10.0.0.20/22'
#
# Hinweise:
# - Ist der User Administrator, landet der Key pflichtgemäß in
#   C:\ProgramData\ssh\administrators_authorized_keys (nicht in ~/.ssh).
# - Ein DHCP-Reservat für 10.0.0.20 in UniFi halten die Profil-Adresse stabil.

param(
  [Parameter(Mandatory = $true)][string]$PublicKey,
  [string]$User = "carst",
  [switch]$SetDefaultShellPowerShell
)

$ErrorActionPreference = "Stop"

$principal = New-Object Security.Principal.WindowsPrincipal(
  [Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  throw "Bitte als Administrator ausführen."
}

# 1. OpenSSH Server installieren (idempotent)
$cap = Get-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
if ($cap.State -ne "Installed") {
  Write-Host "-> OpenSSH Server wird installiert..."
  Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0 | Out-Null
}
Write-Host "OK OpenSSH Server vorhanden"

# 2. sshd-Dienst automatisch starten
Set-Service -Name sshd -StartupType Automatic
if ((Get-Service sshd).Status -ne "Running") { Start-Service sshd }
Write-Host "OK sshd-Dienst: $((Get-Service sshd).Status) (Autostart)"

# 3. Firewall-Regel für TCP/22
if (-not (Get-NetFirewallRule -Name sshd -ErrorAction SilentlyContinue)) {
  New-NetFirewallRule -Name sshd -DisplayName "OpenSSH Server (sshd)" `
    -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 | Out-Null
  Write-Host "OK Firewall-Regel für Port 22 erstellt"
} else {
  Write-Host "OK Firewall-Regel für Port 22 existiert bereits"
}

# 4. Public-Key in die richtige authorized_keys legen
$isAdmin = $false
try {
  $isAdmin = @(Get-LocalGroupMember -Group "Administrators" |
    Where-Object { $_.Name -match "\\$([regex]::Escape($User))$" }).Count -gt 0
} catch {}

if ($isAdmin) {
  $authKeys = Join-Path $env:ProgramData "ssh\administrators_authorized_keys"
  Write-Host "-> User '$User' ist Administrator → administrators_authorized_keys"
} else {
  $authKeys = "C:\Users\$User\.ssh\authorized_keys"
}

New-Item -ItemType Directory -Force -Path (Split-Path $authKeys) | Out-Null
if (-not (Test-Path $authKeys) -or -not (Select-String -Path $authKeys -SimpleMatch $PublicKey -Quiet)) {
  Add-Content -Path $authKeys -Value $PublicKey
  Write-Host "OK Public-Key eingetragen: $authKeys"
} else {
  Write-Host "OK Public-Key war bereits eingetragen: $authKeys"
}

# administrators_authorized_keys braucht restriktive ACLs, sonst ignoriert sshd sie.
if ($isAdmin) {
  icacls $authKeys /inheritance:r /grant "SYSTEM:F" /grant "BUILTIN\Administrators:F" | Out-Null
  Write-Host "OK ACLs für administrators_authorized_keys gesetzt"
}

# 5. Optional: PowerShell als Default-Shell (statt cmd.exe)
if ($SetDefaultShellPowerShell) {
  New-Item -Path "HKLM:\SOFTWARE\OpenSSH" -Force | Out-Null
  New-ItemProperty -Path "HKLM:\SOFTWARE\OpenSSH" -Name DefaultShell `
    -Value "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" `
    -PropertyType String -Force | Out-Null
  Write-Host "OK DefaultShell = PowerShell"
}

Write-Host ""
Write-Host "Fertig. Sanity-Check vom Workspace-Container aus:"
Write-Host "  bash -c 'cat </dev/null >/dev/tcp/10.0.0.20/22' && echo PORT22-OPEN"
