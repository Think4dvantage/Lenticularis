#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Lenticularis remote deployment and management script.

.DESCRIPTION
    Manages the Lenticularis stack running on a remote Docker host via SSH.
    Requires only the built-in Windows SSH client (OpenSSH, shipped with
    Windows 10 1803+ and Windows 11).

    Remote host : 172.18.10.50
    SSH user    : raphael
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
$REMOTE_HOST = "172.18.10.50"
$REMOTE_USER = "raphael"
$REMOTE_DIR  = "~/lenticularis"
$SSH_TARGET  = "${REMOTE_USER}@${REMOTE_HOST}"
$SSH_KEY     = "$HOME\.ssh\id_lg4"         # identity file used for all SSH operations

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
        ssh -i "$SSH_KEY" -t "$SSH_TARGET" $remoteCmd
    } else {
        ssh -i "$SSH_KEY" "$SSH_TARGET" $remoteCmd
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Error "SSH command failed (exit $LASTEXITCODE): $remoteCmd"
    }
}

function Sync-Files {
    Write-Header "Syncing project files to ${SSH_TARGET}:${REMOTE_DIR}"

    $localDir = (Get-Location).Path
    Write-Host "  Source : $localDir" -ForegroundColor Gray
    Write-Host "  Target : ${SSH_TARGET}:${REMOTE_DIR}" -ForegroundColor Gray

    # Build tar exclude arguments
    $excludeArgs = ($SYNC_EXCLUDES | ForEach-Object { "--exclude=./$_" })

    # Write tar to a temp file so Windows binary pipeline issues are avoided
    $tmpTar = [System.IO.Path]::GetTempFileName() -replace '\.tmp$', '.tar.gz'
    try {
        # Use tar.exe (ships with Windows 10 1803+)
        $tarArgs = @("-czf", $tmpTar) + $excludeArgs + @("-C", $localDir, ".")
        & tar @tarArgs
        if ($LASTEXITCODE -ne 0) { Write-Error "tar failed creating archive" }

        $tmpSize = [math]::Round((Get-Item $tmpTar).Length / 1MB, 1)
        Write-Host "  Archive: $tmpSize MB" -ForegroundColor Gray

        # Copy archive to remote and extract
        scp -i "$SSH_KEY" -q "$tmpTar" "${SSH_TARGET}:/tmp/lenti-sync.tar.gz"
        if ($LASTEXITCODE -ne 0) { Write-Error "scp failed" }

        Invoke-SSH "mkdir -p $REMOTE_DIR && tar xzf /tmp/lenti-sync.tar.gz -C $REMOTE_DIR && rm /tmp/lenti-sync.tar.gz"
    } finally {
        Remove-Item $tmpTar -ErrorAction SilentlyContinue
    }

    Write-Host "  Done." -ForegroundColor Green
}

function Test-SSHConnectivity {
    Write-Header "Testing SSH connectivity to $SSH_TARGET (key: $SSH_KEY)"
    $result = ssh -i "$SSH_KEY" -o ConnectTimeout=5 -o BatchMode=yes "$SSH_TARGET" "echo ok" 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  Cannot connect. Check:" -ForegroundColor Yellow
        Write-Host "    1. Key file exists  : $SSH_KEY" -ForegroundColor Yellow
        Write-Host "    2. Key is authorised: ssh-copy-id -i '$SSH_KEY.pub' $SSH_TARGET" -ForegroundColor Yellow
        Write-Host "    3. Host is reachable: ping $REMOTE_HOST" -ForegroundColor Yellow
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

    # Verify the configured key exists
    if (-not (Test-Path $SSH_KEY)) {
        Write-Error "SSH key not found: $SSH_KEY`nUpdate `$SSH_KEY in this script to point to your private key."
        exit 1
    }
    $pubKeyFile = "${SSH_KEY}.pub"
    if (-not (Test-Path $pubKeyFile)) {
        Write-Error "SSH public key not found: $pubKeyFile"
        exit 1
    }

    $pubKey = Get-Content $pubKeyFile
    Write-Host "  Using key: $SSH_KEY" -ForegroundColor Gray
    Write-Host ""

    # Attempt key-based auth first — if it already works, skip the manual step
    $already = ssh -i "$SSH_KEY" -o ConnectTimeout=5 -o BatchMode=yes "$SSH_TARGET" "echo ok" 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  Key already authorised on $REMOTE_HOST — skipping key copy step." -ForegroundColor Green
    } else {
        Write-Host "  Copying public key to $SSH_TARGET (password required once)..." -ForegroundColor Cyan
        # Push key via password-authenticated SSH (last time password is needed)
        ssh -i "$SSH_KEY" "$SSH_TARGET" "mkdir -p ~/.ssh && chmod 700 ~/.ssh && echo '$pubKey' >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys && echo 'Key added'"
        if ($LASTEXITCODE -ne 0) {
            Write-Error "Failed to copy key. Authorise it manually:`n  ssh-copy-id -i '$pubKeyFile' $SSH_TARGET"
            exit 1
        }
    }

    $ready = "" # no prompt needed — auto-continue

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

    Write-Header "Building and starting DEV services on $REMOTE_HOST"
    Invoke-SSH "cd $REMOTE_DIR && $COMPOSE_CMD up --build -d"
    Write-Host ""
    Write-Host "Services started. Dashboard : http://${REMOTE_HOST}:8000" -ForegroundColor Green
    Write-Host "API docs    : http://${REMOTE_HOST}:8000/docs" -ForegroundColor Green
    Write-Host "InfluxDB UI : http://${REMOTE_HOST}:8086  (shared homelab instance)" -ForegroundColor Gray
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
    Write-Host "  Dashboard: http://${REMOTE_HOST}:8000" -ForegroundColor Green
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
    Write-Host "  Dashboard: http://${REMOTE_HOST}:8000" -ForegroundColor Green
}

function Invoke-Logs([string]$service = "") {
    if (-not (Test-SSHConnectivity)) { exit 1 }
    Write-Header "Streaming logs (Ctrl+C to stop)"
    $svcArg = if ($service) { " $service" } else { "" }
    Invoke-SSH "cd $REMOTE_DIR && $COMPOSE_CMD logs -f --tail=100$svcArg" -Interactive
}

function Invoke-Status {
    if (-not (Test-SSHConnectivity)) { exit 1 }
    Write-Header "Container status on $REMOTE_HOST"
    Invoke-SSH "cd $REMOTE_DIR && $COMPOSE_CMD ps"
}

function Invoke-Shell {
    if (-not (Test-SSHConnectivity)) { exit 1 }
    Write-Header "Opening shell on $SSH_TARGET"
    ssh -i "$SSH_KEY" -t "$SSH_TARGET" "bash -l"
}

function Invoke-Exec {
    if (-not (Test-SSHConnectivity)) { exit 1 }
    Write-Header "Opening shell inside $APP_CONTAINER container"
    Invoke-SSH "docker exec -it $APP_CONTAINER bash" -Interactive
}

function Show-Help {
    Write-Host @"

Lenticularis Remote Management
  Target: ${SSH_TARGET}:${REMOTE_DIR}

Commands:
  setup     First-time: generate SSH key instructions, verify connectivity
  sync      Push code to remote (no service restart)
  deploy    sync + docker compose up --build  (full redeploy)
  up        Start services (no rebuild)
  down      Stop and remove containers
  restart   Restart running containers
  logs      Tail compose logs  (optional: logs lenticularis)
  status    Show container status
  shell     SSH into the remote host
  exec      Open bash inside lenticularis-app container

URLs after deploy:
  Dashboard  http://${REMOTE_HOST}:8000
  API docs   http://${REMOTE_HOST}:8000/docs
  InfluxDB   http://${REMOTE_HOST}:8086
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
