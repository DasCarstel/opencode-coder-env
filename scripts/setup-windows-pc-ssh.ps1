# setup-windows-pc-ssh.ps1 – Einmal-Setup für den ssh-mcp-Zugang zum Carsten-PC
#
# Auf dem Windows-PC **als Administrator** ausführen, z. B.:
#
#   powershell -ExecutionPolicy Bypass -File setup-windows-pc-ssh.ps1 `
#     -PublicKey "ssh-ed25519 AAAA... ssh-mcp-carsten-pc" -SetDefaultShellPowerShell
#
# Den einzutragenden Public-Key liefert:
#   bash scripts/bootstrap-hosts.sh   (Abschnitt "Secret ssh-mcp-pc")
#   – oder der Public-Key des Keypairs, das du selbst erzeugt und in OpenBao
#     unter secret/data/mcp/ssh-mcp-pc (Felder private_key/public_key) abgelegt hast.
#
# WICHTIG – wenn es bei "OpenSSH Server wird installiert..." hängt:
#   "Add-WindowsCapability -Online" wartet intern auf Windows Update / die
#   Features-on-Demand-Quelle und kann dadurch dauerhaft hängen. Dieses Skript
#   bricht den Capability-Weg nach -CapabilityTimeoutSec automatisch ab und
#   installiert dann die Standalone-Variante von PowerShell/Win32-OpenSSH
#   (unabhängig von Windows Update). Erzwingen: -SkipCapabilityInstall.
#
# Danach muss der Sanity-Check vom Workspace-Container aus gelingen:
#   bash -c 'cat </dev/null >/dev/tcp/10.0.0.20/22'
#
# Hinweise:
# - Ist der User Administrator, landet der Key pflichtgemäß in
#   C:\ProgramData\ssh\administrators_authorized_keys (nicht in ~/.ssh).
# - Ein DHCP-Reservat für 10.0.0.20 in UniFi hält die Profil-Adresse stabil.

param(
  [Parameter(Mandatory = $true)][string]$PublicKey,
  [string]$User = "carst",
  [switch]$SetDefaultShellPowerShell,
  [switch]$SkipCapabilityInstall,
  [int]$CapabilityTimeoutSec = 300
)

$ErrorActionPreference = "Stop"

$principal = New-Object Security.Principal.WindowsPrincipal(
  [Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  throw "Bitte als Administrator ausführen."
}

$sshdExe = Join-Path $env:SystemRoot "System32\OpenSSH\sshd.exe"
function Test-SshdPresent {
  (Test-Path $sshdExe) -or [bool](Get-Service sshd -ErrorAction SilentlyContinue)
}

# 1. OpenSSH Server installieren (capability zuerst, sonst standalone)
if (-not (Test-SshdPresent)) {
  $installed = $false

  if (-not $SkipCapabilityInstall) {
    Write-Host "-> Optional Feature 'OpenSSH.Server' installieren (max ${CapabilityTimeoutSec}s)..."
    foreach ($svc in @('wuauserv', 'TrustedInstaller')) {
      try { Start-Service $svc -ErrorAction SilentlyContinue } catch {}
    }
    $job = Start-Job -ScriptBlock {
      Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0 | Out-Null
      (Get-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0).State
    }
    if (Wait-Job $job -Timeout $CapabilityTimeoutSec) {
      Write-Host "OK Capability-Status: $(Receive-Job $job)"
    } else {
      Write-Warning "Add-WindowsCapability reagiert nicht (> ${CapabilityTimeoutSec}s) – Job wird beendet, Standalone-Fallback folgt."
    }
    Stop-Job $job -ErrorAction SilentlyContinue
    Remove-Job $job -Force -ErrorAction SilentlyContinue
    $installed = Test-SshdPresent
  }

  if (-not $installed) {
    Write-Host "-> Standalone-Installation (PowerShell/Win32-OpenSSH, GitHub)..."
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $rel = Invoke-RestMethod -UseBasicParsing `
      -Uri "https://api.github.com/repos/PowerShell/Win32-OpenSSH/releases/latest"
    $msi = $rel.assets | Where-Object { $_.name -like 'OpenSSH-Win64-*.msi' } | Select-Object -First 1
    $zip = $rel.assets | Where-Object { $_.name -like 'OpenSSH-Win64*.zip' } | Select-Object -First 1

    if ($msi) {
      $p = Join-Path $env:TEMP $msi.name
      Invoke-WebRequest -UseBasicParsing -Uri $msi.browser_download_url -OutFile $p
      Start-Process msiexec.exe -ArgumentList "/i `"$p`" /qn" -Wait
      Write-Host "OK Standalone-OpenSSH per MSI installiert"
    } elseif ($zip) {
      $p = Join-Path $env:TEMP $zip.name
      Invoke-WebRequest -UseBasicParsing -Uri $zip.browser_download_url -OutFile $p
      $ex = Join-Path $env:TEMP "openssh-extract"
      if (Test-Path $ex) { Remove-Item -Recurse -Force $ex }
      Expand-Archive -Path $p -DestinationPath $ex -Force
      $src = Get-ChildItem $ex -Directory | Select-Object -First 1
      $dest = Join-Path $env:ProgramFiles "OpenSSH"
      if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
      Move-Item $src.FullName $dest
      & (Join-Path $dest "install-sshd.ps1")
      Write-Host "OK Standalone-OpenSSH per ZIP installiert ($dest)"
    } else {
      throw "Im GitHub-Release weder MSI noch ZIP gefunden – bitte OpenSSH manuell installieren."
    }
  }
}
if (-not (Test-SshdPresent)) {
  throw "OpenSSH Server konnte nicht installiert werden – bitte manuell prüfen."
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

# 4. Public-Key ablegen – an BEIDEN möglichen Stellen:
#    - %ProgramData%\ssh\administrators_authorized_keys  (sshd nutzt das für
#      Administratoren-Konten; die Datei im Benutzerprofil wird dann ignoriert)
#    - %USERPROFILE%\.ssh\authorized_keys                (nicht-administrative Konten)
#    Damit funktioniert es unabhängig davon, ob der User Admin ist – und ohne die
#    lokalisierten Gruppennamen ("Administrators" vs. "Administratoren") zu raten.
$keyLine = $PublicKey.Trim()

function Set-AuthorizedKey {
  param([string]$Path)
  New-Item -ItemType Directory -Force -Path (Split-Path $Path) | Out-Null
  $existing = if (Test-Path $Path) { [System.IO.File]::ReadAllText($Path) } else { "" }
  $has = @($existing -split "`n" | Where-Object { $_.Trim() -eq $keyLine }).Count -gt 0
  if ($has) {
    Write-Host "OK Public-Key war bereits eingetragen: $Path"
  } else {
    if ($existing -and -not $existing.EndsWith("`n")) { $existing += "`n" }
    # UTF-8 ohne BOM + LF-Zeilenenden: Windows-sshd lehnt CRLF in authorized_keys häufig ab.
    [System.IO.File]::WriteAllText($Path, $existing + $keyLine + "`n", (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "OK Public-Key eingetragen (UTF-8 ohne BOM, LF): $Path"
  }
}

$userKeys  = "C:\Users\$User\.ssh\authorized_keys"
$adminKeys = Join-Path $env:ProgramData "ssh\administrators_authorized_keys"

Set-AuthorizedKey -Path $userKeys
Set-AuthorizedKey -Path $adminKeys

# administrators_authorized_keys braucht restriktive ACLs, sonst ignoriert sshd sie.
# Grants über wohlbekannte SIDs -> lokalisierungsunabhängig (SYSTEM=S-1-5-18,
# Administratoren=S-1-5-32-544).
icacls $adminKeys /inheritance:r /grant "*S-1-5-18:F" /grant "*S-1-5-32-544:F" | Out-Null
Write-Host "OK ACLs für administrators_authorized_keys gesetzt (über SIDs)"

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
