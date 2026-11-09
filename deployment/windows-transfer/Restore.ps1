param(
    [Parameter(Mandatory=$true)][string]$TargetRoot,
    [string]$PasswordFile,
    [string]$ProjectName='sentinelops',
    [int]$FrontendPort=8080, [int]$BackendPort=8000, [int]$BusinessPort=18080,
    [int]$SshPort=12222, [int]$PrometheusPort=19090, [int]$AlertmanagerPort=19093,
    [int]$MailpitPort=8025, [int]$DatabasePort=5432,
    [switch]$ValidationOnly, [switch]$Resume
)
. (Join-Path $PSScriptRoot 'Common.ps1')
$restoreStarted = [DateTime]::UtcNow
if ($ProjectName -notmatch '^[a-z0-9][a-z0-9-]{1,50}$') { throw 'Invalid Compose project name' }
$TargetRoot=[IO.Path]::GetFullPath($TargetRoot)
if ($ValidationOnly) {
    if ($ProjectName -eq 'sentinelops') { throw 'Validation requires a separate ProjectName' }
    $FrontendPort=28080; $BackendPort=28000; $BusinessPort=28081; $SshPort=22222
    $PrometheusPort=29090; $AlertmanagerPort=29093; $MailpitPort=28025; $DatabasePort=25432
}
Write-Host '[1/7] Checking bundle integrity, Docker and destination.'
$manifest=Check-Bundle $PSScriptRoot
Assert-Docker $manifest.architecture
$statePath=Join-Path $TargetRoot '.transfer/restore-state.json'
if ($Resume) {
    $state=Read-Json $statePath
    if ($state.bundle_id -ne $manifest.bundle_id -or $state.project -ne $ProjectName -or $state.ready) { throw 'Cannot resume this destination' }
} else {
    if ((Test-Path -LiteralPath $TargetRoot) -and @(Get-ChildItem -LiteralPath $TargetRoot -Force).Count) { throw 'TargetRoot must be empty. Use -Resume only for this bundle after a failed restore.' }
    $containers=@((Run-Docker @('ps','-a','--format','{{.Names}}')) -split "`n")
    $volumes=@((Run-Docker @('volume','ls','--format','{{.Name}}')) -split "`n")
    if (@($containers | Where-Object { $_.StartsWith($ProjectName+'-') }).Count -or @($volumes | Where-Object { $_.StartsWith($ProjectName+'_') }).Count) { throw 'Destination Compose resources already exist; refusing to overwrite data' }
    foreach ($port in @($FrontendPort,$BackendPort,$BusinessPort,$SshPort,$PrometheusPort,$AlertmanagerPort,$MailpitPort,$DatabasePort)) {
        $listener=New-Object Net.Sockets.TcpListener([Net.IPAddress]::Loopback,$port)
        try { $listener.Start() } finally { $listener.Stop() }
    }
    [void](New-Item -ItemType Directory -Path $TargetRoot -Force)
    $transfer=Join-Path $TargetRoot '.transfer'; [void](New-Item -ItemType Directory -Path $transfer)
    $password=Read-TransferPassword $PasswordFile
    $compressed=Join-Path $transfer 'payload.tar.gz'; $tar=Join-Path $transfer 'payload.tar'
    [SentinelTransferCrypto]::Decrypt((Join-Path $PSScriptRoot 'payload.enc'),$compressed,$password)
    $password=$null
    [SentinelTransferCrypto]::Gzip($compressed,$tar,$true); Remove-Item -LiteralPath $compressed
    Run-Tar @('-xf',$tar,'-C',$transfer); Remove-Item -LiteralPath $tar
    Get-ChildItem -LiteralPath (Join-Path $transfer 'project') -Force | Move-Item -Destination $TargetRoot
    $emptyProject=Join-Path $transfer 'project'; Assert-Under $emptyProject $TargetRoot
    Remove-Item -LiteralPath $emptyProject
    $environmentFile=Join-Path $TargetRoot '.env'
    $text=[IO.File]::ReadAllText($environmentFile).Replace($manifest.source_root,$TargetRoot).Replace($manifest.source_root.Replace('\','/'),$TargetRoot.Replace('\','/'))
    Write-Utf8 $environmentFile $text
    $database=$manifest.database_name
    if ($ValidationOnly) { $database='sentinelops_transfer_test' }
    $values=@{POSTGRES_DB=$database;POSTGRES_PORT=$DatabasePort;BACKEND_PORT=$BackendPort;FRONTEND_PORT=$FrontendPort;PROMETHEUS_PORT=$PrometheusPort;ALERTMANAGER_PORT=$AlertmanagerPort}
    if ($ValidationOnly) { $values.AI_ENABLED='false' }
    Set-EnvironmentFile $environmentFile $values
    $state=@{bundle_id=$manifest.bundle_id;project=$ProjectName;lab_project=$ProjectName+'-testlab';root=$TargetRoot;database=$database;database_user=$manifest.database_user;backend_port=$BackendPort;frontend_port=$FrontendPort;business_port=$BusinessPort;ssh_port=$SshPort;prometheus_port=$PrometheusPort;mailpit_port=$MailpitPort;validation=[bool]$ValidationOnly;ready=$false;database_restored=$false;imported_volumes=@();main_services=$manifest.main_services;backend_image=$manifest.backend_image}
    Write-Json $statePath $state
    $state | Add-Member NoteProperty restore_started_at $restoreStarted.ToString('o') -Force
    Write-Json $statePath $state
    $previousRoot=$env:SOURCE_INPUT_ROOT; $env:SOURCE_INPUT_ROOT=$TargetRoot
    try {
        $args=@('compose','-f',(Join-Path $TargetRoot 'compose.yaml'),'-f',(Join-Path $TargetRoot 'compose.sources-local.yaml'),'-p',$ProjectName)
        foreach ($profile in $script:Profiles) {$args+=@('--profile',$profile)}
        $main=Run-Docker ($args+@('config','--format','json')) | ConvertFrom-Json
        $lab=Run-Docker @('compose','-f',(Join-Path $TargetRoot 'compose.testlab.yaml'),'config','--format','json') | ConvertFrom-Json
    } finally { $env:SOURCE_INPUT_ROOT=$previousRoot }
    $main.name=$ProjectName; $main.networks.default.name=$ProjectName+'_default'
    foreach ($property in $main.volumes.PSObject.Properties) { $property.Value.name=$ProjectName+'_'+$property.Name }
    $portMap=@{frontend=$FrontendPort;backend=$BackendPort;postgres=$DatabasePort;prometheus=$PrometheusPort;alertmanager=$AlertmanagerPort;mailpit=$MailpitPort}
    foreach ($property in $main.services.PSObject.Properties) {
        $service=$property.Value; $key='sentinelops/'+$property.Name
        $image=$manifest.service_images.PSObject.Properties[$key]
        if ($image) {$service.image=$image.Value} else {$service.image=$manifest.backend_image}
        $service.PSObject.Properties.Remove('build')
        if ($portMap.ContainsKey($property.Name)) { foreach ($port in $service.ports) {$port.published=[string]$portMap[$property.Name]} }
    }
    $scheduler=$main.services.backend.environment.PSObject.Properties['LLM_PROBE_SCHEDULER_ENABLED']
    $state.probe_scheduler=($null -eq $scheduler -or $scheduler.Value -ne 'false') -and -not $ValidationOnly
    $main.services.backend.environment | Add-Member NoteProperty LLM_PROBE_SCHEDULER_ENABLED 'false' -Force
    $lab.name=$state.lab_project; $lab.networks.platform.name=$ProjectName+'_default'
    foreach ($property in $lab.services.PSObject.Properties) {
        $property.Value.image=$manifest.service_images.PSObject.Properties['sentinelops-testlab/'+$property.Name].Value
        $property.Value.PSObject.Properties.Remove('build')
    }
    $lab.services.business.ports[0].published=[string]$BusinessPort
    $lab.services.ssh.ports[0].published=[string]$SshPort
    Write-Json (Join-Path $transfer 'main.compose.json') $main
    Write-Json (Join-Path $transfer 'testlab.compose.json') $lab
    Write-Json $statePath $state
}
$state=Read-Json $statePath
$transfer=Join-Path $TargetRoot '.transfer'; $data=Join-Path $transfer 'data'
$mainFile=Join-Path $transfer 'main.compose.json'; $labFile=Join-Path $transfer 'testlab.compose.json'
try {
    Write-Host '[2/7] Loading pinned images.'
    $tar=Join-Path $transfer 'images.tar'
    [SentinelTransferCrypto]::Gzip((Join-Path $PSScriptRoot 'images.tar.gz'),$tar,$true)
    [void](Run-Docker @('image','load','--input',$tar)); Remove-Item -LiteralPath $tar
    foreach ($image in $manifest.images) { if ((Run-Docker @('image','inspect',$image.tag,'--format','{{.Id}}')).Trim() -ne $image.id) {throw 'Imported image identity mismatch'} }
    foreach ($pair in @(@('sentinelops/backend','sentinelops-backend:local'),@('sentinelops/frontend','sentinelops-frontend:local'),@('sentinelops/docker-state','sentinelops-docker-state:local'),@('sentinelops-testlab/business','sentinelops-testlab-business:v1'),@('sentinelops-testlab/ssh','sentinelops-execution-ssh:local'))) {
        [void](Run-Docker @('image','tag',$manifest.service_images.PSObject.Properties[$pair[0]].Value,$pair[1]))
    }
    [void](Run-Docker @('image','tag',$manifest.fault_image,'sentinelops-testlab-business:v1-bug'))
    Write-Host '[3/7] Restoring Linux volumes with ownership and permissions.'
    $hashes=Read-Json (Join-Path $data 'volume-fingerprints.json')
    foreach ($suffix in @($manifest.volumes)+@('postgres_data')) {
        $volume=$ProjectName+'_'+$suffix
        $existing=@((Run-Docker @('volume','ls','--format','{{.Name}}')) -split "`n")
        if ($volume -in $existing) {
            $info=@(Decode-Json (Run-Docker @('volume','inspect',$volume)))[0]
            if ($info.Labels.'sentinelops.transfer.bundle' -ne $manifest.bundle_id) {throw 'Refusing to overwrite an unrelated volume'}
        } else { [void](Run-Docker @('volume','create','--label',('com.docker.compose.project='+$ProjectName),'--label',('com.docker.compose.volume='+$suffix),'--label',('sentinelops.transfer.bundle='+$manifest.bundle_id),$volume)) }
        if ($suffix -ne 'postgres_data' -and $suffix -notin $state.imported_volumes) {
            [void](Run-Docker @('run','--rm','--user','0:0','--entrypoint','/bin/tar','--mount',('type=volume,source='+$volume+',target=/volume'),'--mount',('type=bind,source='+$data+'\volumes,target=/backup,readonly'),$manifest.backend_image,'-xpf',('/backup/'+$suffix+'.tar'),'-C','/volume','--same-owner'))
            $hash=(Run-Docker @('run','--rm','--user','0:0','--entrypoint','/bin/bash','--mount',('type=volume,source='+$volume+',target=/volume,readonly'),$manifest.backend_image,'-euc','set -o pipefail; cd /volume; tar --sort=name -cf - . | sha256sum')).Split(' ')[0]
            if ($hash -ne $hashes.PSObject.Properties[$suffix].Value) {throw ('Volume content/permission mismatch: '+$suffix)}
            $state.imported_volumes=@($state.imported_volumes)+@($suffix); Write-Json $statePath $state
        }
    }
    Write-Host '[4/7] Restoring PostgreSQL and verifying every table before starting workers.'
    [void](Compose $mainFile $ProjectName @('up','-d','--no-build','--pull','never','--wait','postgres'))
    $pg=(Compose $mainFile $ProjectName @('ps','-q','postgres')).Trim()
    if (-not $state.database_restored) {
        [void](Run-Docker @('cp',(Join-Path $data 'postgres.dump'),($pg+':/tmp/sentinel-transfer.dump')))
        [void](Run-Docker @('exec',$pg,'pg_restore','--exit-on-error','--clean','--if-exists','--no-owner','-U',$state.database_user,'-d',$state.database,'/tmp/sentinel-transfer.dump'))
        [void](Run-Docker @('exec',$pg,'rm','/tmp/sentinel-transfer.dump'))
        $before=@(Read-Json (Join-Path $data 'database-fingerprint.json'))
        $after=@(Database-Fingerprint $pg $state.database_user $state.database)
        if (Compare-Object $before $after) {throw 'Database content mismatch; workers have not been started'}
        $state.database_restored=$true; Write-Json $statePath $state
    }
    [void](Compose $mainFile $ProjectName @('up','-d','--no-build','--pull','never','--wait','backend'))
    [void](Compose $labFile $state.lab_project @('up','-d','--no-build','--pull','never','--wait','business'))
    $business=(Compose $labFile $state.lab_project @('ps','-q','business')).Trim()
    $businessId=(Run-Docker @('inspect',$business,'--format','{{.Id}}')).Trim()
    Write-Utf8 (Join-Path $TargetRoot '.local/testlab/ssh/allowed_ids') ($businessId+"`n")
    [void](Compose $labFile $state.lab_project @('up','-d','--no-build','--pull','never','--wait','ssh','traffic'))
    $backend=(Compose $mainFile $ProjectName @('ps','-q','backend')).Trim()
    [void](Run-Docker @('exec',$backend,'mkdir','-p','/tmp/sentinel-transfer'))
    foreach ($file in @('metadata.json','openapi.json')) { [void](Run-Docker @('cp',(Join-Path $data $file),($backend+':/tmp/sentinel-transfer/'+$file))) }
    [void](Run-Docker @('cp',(Join-Path $TargetRoot '.local/admin-credentials.txt'),($backend+':/tmp/sentinel-transfer/admin-credentials.txt')))
    [void](Run-Docker @('cp',(Join-Path $PSScriptRoot 'rebind.py'),($backend+':/tmp/sentinel-transfer/rebind.py')))
    $runtime=@()
    foreach ($pair in @(@($state.main_services | ForEach-Object { @{project=$ProjectName;service=$_;file=$mainFile;origin='sentinelops'} })+@($manifest.lab_services | ForEach-Object { @{project=$state.lab_project;service=$_;file=$labFile;origin='sentinelops-testlab'} }))) {
        $id=(Compose $pair.file $pair.project @('ps','-q',$pair.service)).Trim()
        if ($id) { $info=@(Decode-Json (Run-Docker @('inspect',$id)))[0]; $runtime+=@{origin=$pair.origin;service=$pair.service;id=$info.Id;name=$info.Name.TrimStart('/');started_at=$info.State.StartedAt;project=$pair.project} }
    }
    Write-Json (Join-Path $data 'new-runtime.json') @{containers=$runtime;computer=$env:COMPUTERNAME;project=$ProjectName;validation=[bool]$state.validation}
    [void](Run-Docker @('cp',(Join-Path $data 'new-runtime.json'),($backend+':/tmp/sentinel-transfer/new-runtime.json')))
    Write-Host '[5/7] Creating a new computer asset and pausing imported historical assets.'
    [void](Run-Docker @('exec',$backend,'/app/.venv/bin/python','/tmp/sentinel-transfer/rebind.py','prepare'))
    [void](Run-Docker @('cp',($backend+':/tmp/sentinel-transfer/rebind-result.json'),(Join-Path $data 'rebind-result.json')))
    $core=@($state.main_services | Where-Object {$_ -notin @('backend','postgres')})
    [void](Compose $mainFile $ProjectName (@('up','-d','--no-build','--pull','never','--wait')+$core))
    # Enable the normal probe scheduler before freezing the new Docker IDs.
    # Start.ps1 will then reuse this backend instead of recreating a bound target.
    $main=Read-Json $mainFile
    $main.services.backend.environment.LLM_PROBE_SCHEDULER_ENABLED=([string][bool]$state.probe_scheduler).ToLower()
    Write-Json $mainFile $main
    [void](Compose $mainFile $ProjectName @('up','-d','--no-build','--pull','never','--no-deps','--wait','backend'))
    $backend=(Compose $mainFile $ProjectName @('ps','-q','backend')).Trim()
    [void](Run-Docker @('exec',$backend,'mkdir','-p','/tmp/sentinel-transfer'))
    foreach ($file in @('metadata.json','openapi.json','rebind-result.json')) { [void](Run-Docker @('cp',(Join-Path $data $file),($backend+':/tmp/sentinel-transfer/'+$file))) }
    [void](Run-Docker @('cp',(Join-Path $TargetRoot '.local/admin-credentials.txt'),($backend+':/tmp/sentinel-transfer/admin-credentials.txt')))
    [void](Run-Docker @('cp',(Join-Path $PSScriptRoot 'rebind.py'),($backend+':/tmp/sentinel-transfer/rebind.py')))
    $runtime=@()
    foreach ($pair in @(@($state.main_services | ForEach-Object { @{project=$ProjectName;service=$_;file=$mainFile;origin='sentinelops'} })+@($manifest.lab_services | ForEach-Object { @{project=$state.lab_project;service=$_;file=$labFile;origin='sentinelops-testlab'} }))) {
        $id=(Compose $pair.file $pair.project @('ps','-q',$pair.service)).Trim()
        $info=@(Decode-Json (Run-Docker @('inspect',$id)))[0]
        $runtime+=@{origin=$pair.origin;service=$pair.service;id=$info.Id;name=$info.Name.TrimStart('/');started_at=$info.State.StartedAt;project=$pair.project}
    }
    Write-Json (Join-Path $data 'new-runtime.json') @{containers=$runtime;computer=$env:COMPUTERNAME;project=$ProjectName;validation=[bool]$state.validation}
    [void](Run-Docker @('cp',(Join-Path $data 'new-runtime.json'),($backend+':/tmp/sentinel-transfer/new-runtime.json')))
    Write-Host '[6/7] Rebinding services, predictions, rules and current deployments.'
    [void](Run-Docker @('exec',$backend,'/app/.venv/bin/python','/tmp/sentinel-transfer/rebind.py','finish'))
    [void](Run-Docker @('cp',($backend+':/tmp/sentinel-transfer/rebind-result.json'),(Join-Path $data 'rebind-result.json')))
    $resources=(Read-Json (Join-Path $data 'rebind-result.json')).resources
    Write-Json (Join-Path $TargetRoot '.local/testlab/resources.json') $resources
    $state | Add-Member NoteProperty rebound $true -Force; Write-Json $statePath $state
    & (Join-Path $PSScriptRoot 'Start.ps1') -TargetRoot $TargetRoot
    & (Join-Path $PSScriptRoot 'Verify.ps1') -TargetRoot $TargetRoot
    $recordBase = 'http://127.0.0.1:'+$state.backend_port
    $recordToken = Login $recordBase $TargetRoot
    try {
        $sourceDuration = if ($manifest.PSObject.Properties['backup_duration_seconds']) {$manifest.backup_duration_seconds} else {$null}
        $backupRecord = Register-TransferBackup $recordBase $recordToken $manifest $sourceDuration
        $started = if ($state.PSObject.Properties['restore_started_at']) {[DateTime]::Parse($state.restore_started_at).ToUniversalTime()} else {$restoreStarted}
        $restoreBody = @{kind='restore';backup_id=$backupRecord.id;idempotency_key=[Guid]::NewGuid().ToString();artifacts=$backupRecord.artifacts;completed_at=[DateTime]::UtcNow.ToString('o');duration_seconds=([DateTime]::UtcNow-$started).TotalSeconds;verification='verified';evidence=('Independent restore and Verify.ps1 passed; project '+$ProjectName+'. Database and volume fingerprints checked before workers started.')}
        $restoreRecord = Api $recordBase '/api/v1/system/backup-records' $recordToken 'POST' $restoreBody
        Write-Json (Join-Path $TargetRoot '.transfer/restore-registration.json') $restoreRecord
    } catch {Write-Warning 'Restore verified; platform registration failed. Record the measured duration and artifact hashes manually.'}
    $state.ready=$true; Write-Json $statePath $state
    Write-Host '[7/7] Restore and verification passed.'
    Write-Host ('Platform: http://127.0.0.1:'+$state.frontend_port)
    Write-Host ('Test business: http://127.0.0.1:'+$state.business_port)
} catch {
    Write-Host ('Restore incomplete. Data retained at '+$TargetRoot+'. Fix the reported problem and rerun with -Resume.')
    throw
}
