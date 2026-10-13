#!/bin/bash
# Runner-Watchdog (GitHub Actions, self-hosted)
#
# Zweck: Der Self-hosted Runner verliert gelegentlich die Broker-Verbindung zu
# GitHub ("ERR BrokerServer" / "Back off 8,636 seconds"); danach bleiben Jobs
# stundenlang im Status "queued" (der Runner meldet dabei sogar busy, ohne dass
# ein Runner.Worker läuft). Dieser Watchdog erkennt das im Diag-Log und startet
# den Runner-Service neu; der Cooldown verhindert Restart-Schleifen.
#
# Installation (auf dem Docker-Host, einmalig):
#   install -m 755 runner-watchdog.sh /opt/docker-compose/github-runner/watchdog.sh
#   # /etc/systemd/system/runner-watchdog.service:
#   #   [Unit]
#   #   Description=GitHub Actions Runner Watchdog
#   #   After=network-online.target
#   #   [Service]
#   #   Type=oneshot
#   #   ExecStart=/opt/docker-compose/github-runner/watchdog.sh
#   # /etc/systemd/system/runner-watchdog.timer:
#   #   [Unit]
#   #   Description=GitHub Actions Runner Watchdog Timer
#   #   [Timer]
#   #   OnBootSec=3min
#   #   OnUnitActiveSec=5min
#   #   Persistent=true
#   #   [Install]
#   #   WantedBy=timers.target
#   systemctl daemon-reload && systemctl enable --now runner-watchdog.timer
#
# Env-Overrides: RUNNER_SERVICE, RUNNER_DIAG_DIR, RUNNER_WATCHDOG_STAMP,
#                RUNNER_WATCHDOG_COOLDOWN (Sekunden, Default 1800)
set -uo pipefail

SERVICE="${RUNNER_SERVICE:-actions.runner.DasCarstel-opencode-coder-env.docker-host-hestia.service}"
DIAG_DIR="${RUNNER_DIAG_DIR:-/opt/docker-compose/github-runner/_diag}"
STAMP="${RUNNER_WATCHDOG_STAMP:-/run/runner-watchdog.stamp}"
COOLDOWN="${RUNNER_WATCHDOG_COOLDOWN:-1800}"

LOG="$(ls -t "$DIAG_DIR"/Runner_*.log 2>/dev/null | head -1 || true)"
[ -n "${LOG:-}" ] || exit 0

if tail -n 150 "$LOG" 2>/dev/null | grep -qE 'ERR +BrokerServer|Back off'; then
  NOW="$(date +%s)"
  LAST=0
  [ -f "$STAMP" ] && LAST="$(stat -c %Y "$STAMP" 2>/dev/null || echo 0)"
  if [ $((NOW - LAST)) -ge "$COOLDOWN" ]; then
    if systemctl restart "$SERVICE"; then
      date +%s > "$STAMP"
      logger -t runner-watchdog "Runner neu gestartet (Broker-Fehler)" 2>/dev/null || true
    fi
  fi
fi
