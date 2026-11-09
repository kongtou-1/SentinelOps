param(
    [string]$SourceRoot = (Join-Path $PSScriptRoot '..\..'),
    [Parameter(Mandatory=$true)][string]$OutputDirectory,
    [string]$DockerTemporaryDirectory
)
. (Join-Path $PSScriptRoot 'Common.ps1')
$backupStarted = [DateTime]::UtcNow
$SourceRoot = (Resolve-Path -LiteralPath $SourceRoot).Path
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
if (Test-Path -LiteralPath $OutputDirectory) { throw 'Output directory already exists; choose a new one' }
Assert-Docker
$parent = Split-Path -Parent $OutputDirectory
[void](New-Item -ItemType Directory -Path $parent -Force)
$work = $OutputDirectory + '.work'
if (Test-Path -LiteralPath $work) { throw 'Staging directory already exists' }
[void](New-Item -ItemType Directory -Path $work)
[void](New-Item -ItemType Directory -Path $OutputDirectory)
$project = Join-Path $work 'project'
$data = Join-Path $work 'data'
[void](New-Item -ItemType Directory -Path $data)
$bundleId = 'sentinelops-transfer-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmss').ToLower()
$environment = Read-Environment (Join-Path $SourceRoot '.env')
$dbUser = $environment.POSTGRES_USER; $dbName = $environment.POSTGRES_DB
if (-not $dbUser) { $dbUser='ops_platform' }; if (-not $dbName) { $dbName='ops_platform' }
$backendPort=8000; if ($environment.BACKEND_PORT) { $backendPort=[int]$environment.BACKEND_PORT }
$base = 'http://127.0.0.1:' + $backendPort
$token = Login $base $SourceRoot
$inventory = @()
$ids = @((Run-Docker @('ps','-q')) -split "`n" | Where-Object { $_ })
$all = @(Decode-Json (Run-Docker (@('inspect') + $ids)))
$live = @($all | Where-Object { $_.Config.Labels.'com.docker.compose.project' -in @('sentinelops','sentinelops-testlab') })
foreach ($service in (Expected-MainServices $SourceRoot)) {
    if (-not @($live | Where-Object {$_.Config.Labels.'com.docker.compose.project' -eq 'sentinelops' -and $_.Config.Labels.'com.docker.compose.service' -eq $service}).Count) {throw "Required service is not running: $service"}
}
$pg = ($live | Where-Object { $_.Config.Labels.'com.docker.compose.project' -eq 'sentinelops' -and $_.Config.Labels.'com.docker.compose.service' -eq 'postgres' }).Id
$active = Sql $pg $dbUser $dbName (Active-JobsQuery)
if ($active.Trim() -ne '0') { throw 'Wait for active execution, diagnosis, report, mail or source jobs to finish before export' }
function All-Api([string]$Path) {
    $rows=@(); do { $batch=@(Api $base ($Path+'?limit=100&offset='+$rows.Count) $token); $rows += $batch } while ($batch.Count -eq 100)
    return $rows
}
$metadata = @{
    servers=@(All-Api '/api/v1/servers'); containers=@(All-Api '/api/v1/containers')
    tasks=@(All-Api '/api/v1/prediction-tasks'); rules=@(All-Api '/api/v1/alert-rules')
    endpoints=@(All-Api '/api/v1/llm-endpoints'); applications=@(All-Api '/api/v1/business-services')
    resources=(Read-Json (Join-Path $SourceRoot '.local/testlab/resources.json'))
    runtime=@($live | ForEach-Object { @{ name=$_.Name.TrimStart('/'); id=$_.Id; image=$_.Image; project=$_.Config.Labels.'com.docker.compose.project'; service=$_.Config.Labels.'com.docker.compose.service' } })
}
Write-Json (Join-Path $data 'metadata.json') $metadata
Write-Json (Join-Path $data 'openapi.json') (Api $base '/openapi.json' '')
Write-Host '[1/6] Copying project and portable runtime files.'
& robocopy $SourceRoot $project /E /NFL /NDL /NJH /NJS /NP /XD .local .venv node_modules __pycache__ .pytest_cache .ruff_cache .uv-cache .pnpm-store .python .tools dist deliverables .transfer /XF '*.pyc' | Out-Null
if ($LASTEXITCODE -ge 8) { throw 'Project copy failed' }
[void](New-Item -ItemType Directory -Path (Join-Path $project '.local') -Force)
foreach ($directory in @('embedding-model','monitoring','ai-secrets')) {
    Copy-Item -LiteralPath (Join-Path $SourceRoot ('.local/'+$directory)) -Destination (Join-Path $project '.local') -Recurse
}
foreach ($name in @('credential.key','monitoring.token','alertmanager.token','admin-credentials.txt')) {
    Copy-Item -LiteralPath (Join-Path $SourceRoot ('.local/'+$name)) -Destination (Join-Path $project '.local')
}
$imageMap=@{}; $images=@(); $index=0
foreach ($imageId in @(@($live | ForEach-Object { $_.Image }) + @(Run-Docker @('image','inspect','sentinelops-testlab-business:v1-bug','--format','{{.Id}}')) | Sort-Object -Unique)) {
    $tag=$bundleId+':image-'+$index; $index++
    [void](Run-Docker @('image','tag',$imageId,$tag))
    $imageMap[$imageId]=$tag; $images+=@{id=$imageId;tag=$tag}
}
$serviceImages=@{}
foreach ($container in $live) { $serviceImages[$container.Config.Labels.'com.docker.compose.project'+'/'+$container.Config.Labels.'com.docker.compose.service']=$imageMap[$container.Image] }
$backendImage=$serviceImages['sentinelops/backend']
$faultId=(Run-Docker @('image','inspect','sentinelops-testlab-business:v1-bug','--format','{{.Id}}')).Trim()
$volumes=@('prometheus_data','alertmanager_data','mailpit_data','backend_secrets','prometheus_secrets','alertmanager_secrets','platform_rules','source_store','notification_config','archives')
$manifest=@{
    format=1; bundle_id=$bundleId; exported_at=[DateTime]::UtcNow.ToString('o'); architecture='x86_64'
    source_computer=$env:COMPUTERNAME; source_root=$SourceRoot; images=$images; service_images=$serviceImages
    backend_image=$backendImage; fault_image=$imageMap[$faultId]; main_services=@($metadata.runtime | Where-Object {$_.project -eq 'sentinelops'} | ForEach-Object {$_.service})
    lab_services=@('business','ssh','traffic'); volumes=$volumes; database_user=$dbUser; database_name=$dbName
    application_id=$metadata.resources.application_id; files=@()
}
Write-Host '[2/6] Saving pinned images (the current platform remains running).'
$imageTar=Join-Path $OutputDirectory 'images.tar'
[void](Docker-ImageArchive (@('image','save','--output',$imageTar)+@($images | ForEach-Object {$_.tag})) $DockerTemporaryDirectory)
[SentinelTransferCrypto]::Gzip($imageTar,(Join-Path $OutputDirectory 'images.tar.gz'),$false)
Remove-Item -LiteralPath $imageTar
$stopped=$false
try {
    Write-Host '[3/6] Maintenance: stopping traffic and writers for a consistent backup.'
    $stopped=$true
    [void](Run-Docker (@('stop')+@($live | Where-Object {$_.Config.Labels.'com.docker.compose.service' -in @('frontend','traffic')} | ForEach-Object {$_.Id})))
    [void](Run-Docker (@('stop')+@($live | Where-Object {$_.Id -ne $pg -and $_.Config.Labels.'com.docker.compose.service' -notin @('frontend','traffic')} | ForEach-Object {$_.Id})))
    if ((Sql $pg $dbUser $dbName (Active-JobsQuery)).Trim() -ne '0') { throw 'A job was queued during maintenance; export aborted' }
    Write-Json (Join-Path $data 'database-fingerprint.json') @(Database-Fingerprint $pg $dbUser $dbName)
    foreach ($pair in @(@('servers','servers'),@('containers','containers'),@('tasks','prediction_tasks'),@('rules','alert_rules'),@('endpoints','llm_endpoints'),@('applications','applications'))) {
        $metadata[$pair[0]]=@(Decode-Json (Sql $pg $dbUser $dbName ("SELECT COALESCE(json_agg(t),'[]'::json) FROM "+$pair[1]+" t WHERE deleted_at IS NULL")))
    }
    Write-Json (Join-Path $data 'metadata.json') $metadata
    [void](Run-Docker @('exec',$pg,'pg_dump','-U',$dbUser,'-d',$dbName,'-Fc','-f','/tmp/sentinel-transfer.dump'))
    [void](Run-Docker @('cp',($pg+':/tmp/sentinel-transfer.dump'),(Join-Path $data 'postgres.dump')))
    [void](Run-Docker @('exec',$pg,'rm','/tmp/sentinel-transfer.dump'))
    $volumeDirectory=Join-Path $data 'volumes'; [void](New-Item -ItemType Directory -Path $volumeDirectory)
    $volumeHashes=@{}
    foreach ($volume in $volumes) {
        [void](Run-Docker @('run','--rm','--user','0:0','--entrypoint','/bin/tar','--mount',('type=volume,source=sentinelops_'+$volume+',target=/volume,readonly'),'--mount',('type=bind,source='+$volumeDirectory+',target=/backup'),$backendImage,'-cpf',('/backup/'+$volume+'.tar'),'-C','/volume','.'))
        $volumeHashes[$volume]=(Run-Docker @('run','--rm','--user','0:0','--entrypoint','/bin/bash','--mount',('type=volume,source=sentinelops_'+$volume+',target=/volume,readonly'),$backendImage,'-euc','set -o pipefail; cd /volume; tar --sort=name -cf - . | sha256sum')).Split(' ')[0]
        Write-Host ('  Backed up '+$volume)
    }
    Write-Json (Join-Path $data 'volume-fingerprints.json') $volumeHashes
    $labDirectory=Join-Path $project '.local/testlab'; [void](New-Item -ItemType Directory -Path $labDirectory)
    foreach ($name in @('state','ssh','source-bug','runtime.env','provider.key','gateway.key','resources.json','source-versions.json')) {
        $path=Join-Path $SourceRoot ('.local/testlab/'+$name)
        if (Test-Path -LiteralPath $path) { Copy-Item -LiteralPath $path -Destination $labDirectory -Recurse }
    }
} finally {
    if ($stopped) {
        Write-Host '[4/6] Restarting the original platform and testlab.'
        [void](Run-Docker (@('start')+@($live | Where-Object {$_.Id -ne $pg} | ForEach-Object {$_.Id})))
        Wait-Until 'original platform ready' { (Api $base '/health/ready' '').status -eq 'ok' }
    }
}
Write-Host '[5/6] Encrypting the backup and producing checksums.'
$tar=Join-Path $parent ($bundleId+'.payload.tar'); Run-Tar @('-cf',$tar,'-C',$work,'.')
$compressed=$tar+'.gz'; [SentinelTransferCrypto]::Gzip($tar,$compressed,$false); Remove-Item -LiteralPath $tar
$password=[SentinelTransferCrypto]::Password()
$passwordFile=$OutputDirectory+'.password.txt'; Write-Utf8 $passwordFile $password
[SentinelTransferCrypto]::Encrypt($compressed,(Join-Path $OutputDirectory 'payload.enc'),$password)
Remove-Item -LiteralPath $compressed
foreach ($file in Get-ChildItem -LiteralPath $PSScriptRoot -File) { Copy-Item -LiteralPath $file.FullName -Destination $OutputDirectory }
foreach ($file in Get-ChildItem -LiteralPath $OutputDirectory -File) {
    $manifest.files+=@{path=$file.Name;bytes=$file.Length;sha256=(Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash}
}
$manifest.completed_at=[DateTime]::UtcNow.ToString('o')
$manifest.backup_duration_seconds=([DateTime]::UtcNow-$backupStarted).TotalSeconds
Write-Json (Join-Path $OutputDirectory 'manifest.json') $manifest
try {
    $record = Register-TransferBackup $base (Login $base $SourceRoot) $manifest ([DateTime]::UtcNow-$backupStarted).TotalSeconds
    Write-Json (Join-Path $OutputDirectory 'backup-registration.json') $record
} catch {Write-Warning 'Backup artifacts are complete; platform registration failed. Register the artifact hashes manually in Platform health.'}
Assert-Under $work $parent
Remove-Item -LiteralPath $work -Recurse -Force
Write-Host '[6/6] Export complete. Copy the bundle directory and keep its password file separately.'
Write-Host $OutputDirectory
Write-Host ('Password file: '+$passwordFile)
