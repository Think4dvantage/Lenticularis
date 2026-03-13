#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Lenticularis remote deployment and management script.

.DESCRIPTION
    Manages the Lenticularis stack running on a remote Docker host via SSH.
    Requires only the built-in Windows SSH client (OpenSSH, shipped with
    Windows 10 1803+ and Windows 11).

    SSH alias   : xpsex  (defined in ~/.ssh/config — hostname, user and
                          identity file are all resolved from there)
    Remote path : ~/lenticularis

.EXAMPLE
    # First-time setup — copy SSH key & create remote directory
    .\scripts\remote.ps1 setup

    # Push code and (re)build + start services
    .\scripts\remote.ps1 deploy

    # Just sync code without restarting services
    .\scripts\remote.ps1 sync

    # Start / stop / restart services
    .\scripts\remote.ps1 up
    .\scripts\remote.ps1 down
    .\scripts\remote.ps1 restart

    # Tail live logs (Ctrl+C to exit)
    .\scripts\remote.ps1 logs
    .\scripts\remote.ps1 logs lenticularis   # single service

    # View running containers
    .\scripts\remote.ps1 status

    # Open a shell on the remote host
    .\scripts\remote.ps1 shell

    # Open a shell inside the app container
    .\scripts\remote.ps1 exec
#>

param(
    [Parameter(Position = 0)]
    [ValidateSet("setup", "sync", "deploy", "up", "down", "restart", "logs", "status", "shell", "exec", "help")]
    [string]$Command = "help",

    [Parameter(Position = 1)]
    [string]$Arg = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
# SSH alias defined in ~/.ssh/config — hostname, user and identity file are
# all resolved from there, so no -i flag or user@host is needed here.
$SSH_TARGET  = "xpsex"
$REMOTE_DIR  = "~/lenticularis"

# Docker Compose command — always layered: base + dev overlay
$COMPOSE_CMD = "docker compose -f docker-compose.yml -f docker-compose.dev.yml"
$APP_CONTAINER = "lenticularis-dev"

# Files/directories to exclude when syncing to the remote host
$SYNC_EXCLUDES = @(
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "*.pyc",
    "data",
    "logs",
    ".ruff_cache",
    ".mypy_cache",
    ".pytest_cache",
    ".vscode"
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

function Write-Header([string]$msg) {
    Write-Host ""
    Write-Host "==> $msg" -ForegroundColor Cyan
}

function Invoke-SSH([string]$remoteCmd, [switch]$Interactive) {
    if ($Interactive) {
        ssh -o ServerAliveInterval=15 -o ServerAliveCountMax=4 -t "$SSH_TARGET" $remoteCmd
    } else {
        ssh -o ServerAliveInterval=15 -o ServerAliveCountMax=4 "$SSH_TARGET" $remoteCmd
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Error "SSH command failed (exit $LASTEXITCODE): $remoteCmd"
    }
}

function Sync-Files {
    Write-Header "Syncing project files → ${SSH_TARGET}:${REMOTE_DIR}"

    $localDir = (Get-Location).Path
    Write-Host "  Source : $localDir" -ForegroundColor Gray
    Write-Host "  Target : ${SSH_TARGET}:${REMOTE_DIR}" -ForegroundColor Gray

    # Build tar exclude arguments
    $excludeArgs = ($SYNC_EXCLUDES | ForEach-Object { "--exclude=./$_" })

    # Create local archive
    $tmpTar = [System.IO.Path]::GetTempFileName() -replace '\.tmp$', '.tar.gz'
    try {
        $tarArgs = @("-czf", $tmpTar) + $excludeArgs + @("-C", $localDir, ".")
        & tar @tarArgs
        if ($LASTEXITCODE -ne 0) { Write-Error "tar failed creating archive" }

        $tmpSize = [math]::Round((Get-Item $tmpTar).Length / 1MB, 1)
        Write-Host "  Archive: $tmpSize MB" -ForegroundColor Gray

        # Stream archive directly through a single SSH connection.
        # Using Start-Process with -RedirectStandardInput avoids the two-connection
        # problem (scp upload + separate ssh extract) that fails on flaky WiFi.
        # /usr/bin/tar is used explicitly because the interactive PATH on the
        # remote host has a broken 'tar' alias (resolves to 'ar').
        Write-Host "  Uploading and extracting…" -ForegroundColor Gray
        $proc = Start-Process -FilePath "ssh" `
            -ArgumentList @(
                "-o", "ServerAliveInterval=15",
                "-o", "ServerAliveCountMax=4",
                "$SSH_TARGET",
                "mkdir -p $REMOTE_DIR && /usr/bin/tar xzf - -C $REMOTE_DIR"
            ) `
            -RedirectStandardInput $tmpTar `
            -NoNewWindow -Wait -PassThru

        if ($proc.ExitCode -ne 0) {
            Write-Error "SSH stream-extract failed (exit $($proc.ExitCode))"
        }
    } finally {
        Remove-Item $tmpTar -ErrorAction SilentlyContinue
    }

    Write-Host "  Done." -ForegroundColor Green
}

function Test-SSHConnectivity {
    Write-Header "Testing SSH connectivity to $SSH_TARGET"
    $result = ssh -o ConnectTimeout=10 -o ServerAliveInterval=15 -o BatchMode=yes "$SSH_TARGET" "echo ok" 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  Cannot connect. Check:" -ForegroundColor Yellow
        Write-Host "    1. SSH alias exists : entry 'Host xpsex' in ~/.ssh/config" -ForegroundColor Yellow
        Write-Host "    2. Key is authorised: ssh-copy-id -i <pub key> <user@host>" -ForegroundColor Yellow
        Write-Host "    3. Host is reachable from this network" -ForegroundColor Yellow
        return $false
    }
    Write-Host "  SSH OK" -ForegroundColor Green
    return $true
}

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

function Invoke-Setup {
    Write-Header "First-time setup"

    Write-Host "  SSH alias : $SSH_TARGET" -ForegroundColor Gray
    Write-Host "  Ensure 'Host $SSH_TARGET' is defined in ~/.ssh/config with" -ForegroundColor Gray
    Write-Host "  HostName, User, and IdentityFile set correctly." -ForegroundColor Gray
    Write-Host ""

    if (-not (Test-SSHConnectivity)) {
        exit 1
    }

    # Create remote directory structure and ensure Docker is available
    Write-Header "Preparing remote host"
    Invoke-SSH "mkdir -p $REMOTE_DIR/data $REMOTE_DIR/logs"
    Invoke-SSH "docker --version && docker compose version"

    Write-Host ""
    Write-Host "Setup complete. Run:" -ForegroundColor Green
    Write-Host "  .\scripts\remote.ps1 deploy" -ForegroundColor White
}

function Invoke-Deploy {
    if (-not (Test-SSHConnectivity)) { exit 1 }
    Sync-Files

    Write-Header "Building and starting DEV services on $SSH_TARGET"
    Invoke-SSH "cd $REMOTE_DIR && $COMPOSE_CMD up --build -d"
    Write-Host ""
    Write-Host "Services started. Dashboard : http://${SSH_TARGET}:8000" -ForegroundColor Green
    Write-Host "API docs    : http://${SSH_TARGET}:8000/docs" -ForegroundColor Green
    Write-Host "InfluxDB UI : http://${SSH_TARGET}:8086  (shared homelab instance)" -ForegroundColor Gray
    Write-Host ""
    Write-Host "Tail logs with: .\scripts\remote.ps1 logs" -ForegroundColor Gray
}

function Invoke-Sync {
    if (-not (Test-SSHConnectivity)) { exit 1 }
    Sync-Files
    Write-Host ""
    Write-Host "Files synced. To apply changes: .\scripts\remote.ps1 restart" -ForegroundColor Gray
}

function Invoke-Up {
    if (-not (Test-SSHConnectivity)) { exit 1 }
    Write-Header "Starting services"
    Invoke-SSH "cd $REMOTE_DIR && $COMPOSE_CMD up -d"
    Write-Host "  Dashboard: http://${SSH_TARGET}:8000" -ForegroundColor Green
}

function Invoke-Down {
    if (-not (Test-SSHConnectivity)) { exit 1 }
    Write-Header "Stopping services"
    Invoke-SSH "cd $REMOTE_DIR && $COMPOSE_CMD down"
}

function Invoke-Restart {
    if (-not (Test-SSHConnectivity)) { exit 1 }
    Write-Header "Restarting services"
    Invoke-SSH "cd $REMOTE_DIR && $COMPOSE_CMD restart"
    Write-Host "  Dashboard: http://${SSH_TARGET}:8000" -ForegroundColor Green
}

function Invoke-Logs([string]$service = "") {
    if (-not (Test-SSHConnectivity)) { exit 1 }
    Write-Header "Streaming logs (Ctrl+C to stop)"
    $svcArg = if ($service) { " $service" } else { "" }
    Invoke-SSH "cd $REMOTE_DIR && $COMPOSE_CMD logs -f --tail=100$svcArg" -Interactive
}

function Invoke-Status {
    if (-not (Test-SSHConnectivity)) { exit 1 }
    Write-Header "Container status on $SSH_TARGET"
    Invoke-SSH "cd $REMOTE_DIR && $COMPOSE_CMD ps"
}

function Invoke-Shell {
    if (-not (Test-SSHConnectivity)) { exit 1 }
    Write-Header "Opening shell on $SSH_TARGET"
    ssh -t "$SSH_TARGET" "bash -l"
}

function Invoke-Exec {
    if (-not (Test-SSHConnectivity)) { exit 1 }
    Write-Header "Opening shell inside $APP_CONTAINER container"
    Invoke-SSH "docker exec -it $APP_CONTAINER bash" -Interactive
}

function Show-Help {
    Write-Host @"

Lenticularis Remote Management
  SSH alias : $SSH_TARGET  (resolved via ~/.ssh/config)
  Remote dir: $REMOTE_DIR

Commands:
  setup     Verify SSH connectivity and prepare remote directory
  sync      Push code to remote (no service restart)
  deploy    sync + docker compose up --build  (full redeploy)
  up        Start services (no rebuild)
  down      Stop and remove containers
  restart   Restart running containers
  logs      Tail compose logs  (optional: logs lenticularis)
  status    Show container status
  shell     SSH into the remote host
  exec      Open bash inside lenticularis-app container

Note: hostname, user and identity file are all defined in ~/.ssh/config
under 'Host $SSH_TARGET'. The script passes no -i flag or user@host.
"@ -ForegroundColor White
}

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
Push-Location $PSScriptRoot\..

switch ($Command) {
    "setup"   { Invoke-Setup }
    "sync"    { Invoke-Sync }
    "deploy"  { Invoke-Deploy }
    "up"      { Invoke-Up }
    "down"    { Invoke-Down }
    "restart" { Invoke-Restart }
    "logs"    { Invoke-Logs $Arg }
    "status"  { Invoke-Status }
    "shell"   { Invoke-Shell }
    "exec"    { Invoke-Exec }
    default   { Show-Help }
}

Pop-Location
