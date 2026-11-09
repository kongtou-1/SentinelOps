param(
    [switch]$InspectionSandbox,
    [switch]$LocalCollectors,
    [switch]$MailTest,
    [switch]$LocalSources,
    [switch]$NoBuild
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    $composeOptions = @('compose', '-f', 'compose.yaml')
    if ($InspectionSandbox) { $composeOptions += @('-f', 'compose.inspection-agent.yaml') }
    if ($LocalSources) { $composeOptions += @('-f', 'compose.sources-local.yaml') }
    $composeOptions += @('--profile', 'monitoring', '--profile', 'execution')
    if ($InspectionSandbox) { $composeOptions += @('--profile', 'inspections') }
    if ($LocalCollectors) { $composeOptions += @('--profile', 'local-collectors') }
    if ($MailTest) { $composeOptions += @('--profile', 'mail-test') }

    function Invoke-Compose([string[]]$Command) {
        & docker @composeOptions @Command
        if ($LASTEXITCODE -ne 0) { throw "Compose failed: $($Command[0])" }
    }

    # Validate secrets, optional mounts and profiles before interrupting any service.
    & docker @composeOptions config --quiet
    if ($LASTEXITCODE -ne 0) { throw 'Invalid Compose configuration' }
    if (-not $NoBuild) { Invoke-Compose @('build', 'backend', 'frontend') }
    Invoke-Compose @('up', '-d', '--wait', 'postgres')

    $backupDirectory = Join-Path $projectRoot ('.local/deployment-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
    New-Item -ItemType Directory -Path $backupDirectory | Out-Null
    Invoke-Compose @('exec', '-T', 'postgres', 'sh', '-c', 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f /tmp/sentinelops-before-deploy.dump')
    Invoke-Compose @('cp', 'postgres:/tmp/sentinelops-before-deploy.dump', (Join-Path $backupDirectory 'database.dump'))

    $guard = 'from sqlalchemy import select,func; from app.db.session import SessionLocal; from app.models import SkillExecution; from app.services.executions.actions import ACTIVE; db=SessionLocal(); count=db.scalar(select(func.count()).select_from(SkillExecution).where(SkillExecution.status.in_(ACTIVE),SkillExecution.deleted_at.is_(None))); db.close(); raise SystemExit(1 if count else 0)'
    $executor = @(& docker @composeOptions ps -q execution-worker) | Select-Object -First 1
    if ($executor) {
        & docker exec $executor /app/.venv/bin/python -c $guard
        if ($LASTEXITCODE -ne 0) { throw 'An execution is active; wait for it to finish before deployment' }
    }

    $legacy = @('operations-worker', 'prediction-worker', 'source-worker', 'inspection-worker', 'ai-worker', 'report-worker')
    $stopServices = @('frontend', 'backend') + $legacy
    $oldContainers = @()
    foreach ($serviceName in $stopServices) {
        $oldContainers += @(& docker ps -q --filter 'label=com.docker.compose.project=sentinelops' --filter "label=com.docker.compose.service=$serviceName")
    }
    $oldContainers = @($oldContainers | Where-Object { $_ })
    if ($oldContainers.Count) {
        & docker stop --time 35 @oldContainers
        if ($LASTEXITCODE -ne 0) { throw 'Could not stop previous API/consumers' }
    }
    if ($executor) {
        & docker exec $executor /app/.venv/bin/python -c $guard
        if ($LASTEXITCODE -ne 0) { throw 'Execution became active; API intake is stopped, wait before retrying' }
        Invoke-Compose @('stop', '-t', '35', 'execution-worker')
    }

    Invoke-Compose @('run', '--rm', '--no-deps', 'migrate')
    Invoke-Compose @('run', '--rm', '--no-deps', 'prepare-volumes')
    Invoke-Compose @('up', '-d', '--wait', '--wait-timeout', '180')

    # Remove only the replaced consumers and old successful setup containers; retain volumes.
    foreach ($serviceName in ($legacy + @('migrate', 'prepare-volumes'))) {
        $obsolete = @(& docker ps -aq --filter 'label=com.docker.compose.project=sentinelops' --filter 'status=exited' --filter "label=com.docker.compose.service=$serviceName")
        $obsolete = @($obsolete | Where-Object { $_ })
        if ($obsolete.Count) {
            & docker rm @obsolete
            if ($LASTEXITCODE -ne 0) { throw "Could not remove obsolete $serviceName container" }
        }
    }
    Write-Host "Platform ready. Database backup: $backupDirectory"
} finally {
    Pop-Location
}
