$ErrorActionPreference = 'Stop'
$script:Profiles = @('monitoring','ai','execution','reports','sources','local-test')
if (-not ('SentinelTransferCrypto' -as [type])) { Add-Type -Path (Join-Path $PSScriptRoot 'DataCrypto.cs') }

function Write-Utf8([string]$Path, [string]$Text) {
    [IO.File]::WriteAllText($Path, $Text, (New-Object Text.UTF8Encoding($false)))
}
function Write-Json([string]$Path, $Value) { Write-Utf8 $Path (ConvertTo-Json -InputObject $Value -Depth 80) }
function Read-Json([string]$Path) { return (Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json) }
function Decode-Json([string]$Text) { return ($Text | ConvertFrom-Json) }
function Read-Environment([string]$Path) {
    $values = @{}
    foreach ($line in [IO.File]::ReadAllLines($Path)) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
            $values[$Matches[1]] = $Matches[2].Trim().Trim('"').Trim("'")
        }
    }
    return $values
}
function Set-EnvironmentFile([string]$Path, [hashtable]$Values) {
    $lines = @([IO.File]::ReadAllLines($Path) | Where-Object { ($_ -split '=',2)[0] -notin $Values.Keys })
    $lines += @($Values.Keys | Sort-Object | ForEach-Object { $_ + '=' + $Values[$_] })
    Write-Utf8 $Path (($lines -join "`n") + "`n")
}
function Run-Docker([string[]]$Arguments) {
    $errorFile = [IO.Path]::GetTempFileName()
    $savedPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $output = & docker @Arguments 2> $errorFile
    $exitCode = $LASTEXITCODE
    $ErrorActionPreference = $savedPreference
    if ($exitCode -ne 0) { throw "Docker operation failed ($exitCode). Details: $errorFile" }
    Remove-Item -LiteralPath $errorFile -Force
    return ($output -join "`n")
}
function Run-Tar([string[]]$Arguments) {
    & tar.exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw 'Archive operation failed' }
}
function Docker-ImageArchive([string[]]$Arguments, [string]$TemporaryDirectory) {
    if (-not $TemporaryDirectory) { return (Run-Docker $Arguments) }
    $temporary=[IO.Path]::GetFullPath($TemporaryDirectory)
    if ($temporary -notmatch '^[A-Za-z]:\\[A-Za-z0-9_.\\-]+$') { throw 'Docker temporary path must be an absolute ASCII path without spaces' }
    [void](New-Item -ItemType Directory -Path $temporary -Force)
    $linux='/run/desktop/mnt/host/'+$temporary.Substring(0,1).ToLower()+$temporary.Substring(2).Replace('\','/')
    $daemon=(& wsl.exe -d docker-desktop -u root -- pidof dockerd).Trim()
    if ($LASTEXITCODE -ne 0 -or $daemon -notmatch '^\d+$') {throw 'Cannot locate Docker Desktop WSL daemon'}
    & wsl.exe -d docker-desktop -u root -- nsenter -t $daemon -m -- mountpoint -q /var/lib/docker/tmp
    if ($LASTEXITCODE -eq 0) {throw 'Docker temporary directory already has a custom mount; refusing to replace it'}
    & wsl.exe -d docker-desktop -u root -- nsenter -t $daemon -m -- mount --bind $linux /var/lib/docker/tmp
    if ($LASTEXITCODE -ne 0) {throw 'Cannot redirect Docker temporary storage'}
    try { return (Run-Docker $Arguments) }
    finally {
        & wsl.exe -d docker-desktop -u root -- nsenter -t $daemon -m -- umount /var/lib/docker/tmp
        if ($LASTEXITCODE -ne 0) {Write-Warning 'Temporary mount cleanup failed; restarting Docker Desktop clears this temporary mount'}
    }
}
function Compose([string]$File, [string]$Project, [string[]]$Arguments) {
    $command = @('compose','-f',$File,'-p',$Project)
    foreach ($profile in $script:Profiles) { $command += @('--profile',$profile) }
    return (Run-Docker ($command + $Arguments))
}
function Wait-Until([string]$Description, [scriptblock]$Check, [int]$Seconds=180) {
    $deadline = [DateTime]::UtcNow.AddSeconds($Seconds)
    do {
        try { if (& $Check) { return } } catch { }
        Start-Sleep -Seconds 3
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Timed out: $Description"
}
function Read-TransferPassword([string]$PasswordFile) {
    if ($PasswordFile) { return ([IO.File]::ReadAllText((Resolve-Path -LiteralPath $PasswordFile).Path)).Trim() }
    $secure = Read-Host 'Transfer password' -AsSecureString
    $address = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($address) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($address) }
}
function Assert-Docker([string]$Architecture='x86_64') {
    $info = Run-Docker @('info','--format','{{.OSType}}|{{.Architecture}}')
    if ($info -ne "linux|$Architecture") { throw "Requires Linux Docker containers with architecture $Architecture; found $info" }
}
function Sql([string]$Container, [string]$User, [string]$Database, [string]$Query) {
    return (Run-Docker @('exec',$Container,'psql','-X','-v','ON_ERROR_STOP=1','-U',$User,'-d',$Database,'-At','-c',$Query))
}
function Database-Fingerprint([string]$Container, [string]$User, [string]$Database) {
    $tables = (Sql $Container $User $Database "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename") -split "`n"
    $queries = foreach ($table in $tables) {
        $table = $table.Trim()
        if ($table -notmatch '^[a-z_]+$') { throw 'Unexpected table name' }
        "SELECT '$table',count(*)::text,md5(COALESCE(string_agg(md5(t::text),'' ORDER BY t::text),'')) FROM $table t"
    }
    return @((Sql $Container $User $Database ($queries -join ' UNION ALL ')) -split "`n" | ForEach-Object { $_.Trim() } | Sort-Object)
}
function Assert-Under([string]$Path, [string]$Parent) {
    $resolved = [IO.Path]::GetFullPath($Path)
    $root = [IO.Path]::GetFullPath($Parent).TrimEnd('\') + '\'
    if (-not $resolved.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) { throw 'Path escapes intended transfer directory' }
}
function Check-Bundle([string]$Directory) {
    $manifest = Read-Json (Join-Path $Directory 'manifest.json')
    foreach ($file in $manifest.files) {
        $path = Join-Path $Directory $file.path
        Assert-Under $path $Directory
        if (-not (Test-Path -LiteralPath $path) -or (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $file.sha256) {
            throw "Transfer file missing or modified: $($file.path)"
        }
    }
    return $manifest
}
function Api([string]$Base, [string]$Path, [string]$Token, [string]$Method='GET', $Body=$null) {
    $headers = @{}
    if ($Token) { $headers.Authorization = 'Bearer ' + $Token }
    $args = @{ Uri=$Base+$Path; Method=$Method; Headers=$headers; UseBasicParsing=$true; TimeoutSec=45 }
    if ($null -ne $Body) { $args.Body=[Text.Encoding]::UTF8.GetBytes((ConvertTo-Json $Body -Depth 30)); $args.ContentType='application/json' }
    return (Invoke-RestMethod @args)
}
function Login([string]$Base, [string]$Root) {
    $credentials = [IO.File]::ReadAllText((Join-Path $Root '.local/admin-credentials.txt'))
    $values = @{}
    foreach ($match in [regex]::Matches($credentials, '(?m)(username|password)\s*[:=]\s*(.+)')) { $values[$match.Groups[1].Value]=$match.Groups[2].Value.Trim() }
    return (Api $Base '/api/v1/auth/login' '' 'POST' $values).access_token
}

function Active-JobsQuery {
    $jobs = @{
        skill_executions="'queued','prechecking','executing','verifying'"
        postmortem_deliveries="'queued','sending'"
        analysis_runs="'queued','running','cancelling'"
        agent_analyses="'queued','running'"
        postmortem_reports="'queued','running'"
        source_snapshots="'queued','running'"
        prediction_results="'queued','running'"
        evaluation_runs="'queued','running'"
        notifications="'queued','sending'"
        retention_plans="'queued','running'"
        knowledge_documents="'queued','running'"
    }
    $parts = foreach ($table in $jobs.Keys) {
        $column = if ($table -eq 'notifications') {'mail_status'} elseif ($table -eq 'knowledge_documents') {'index_status'} else {'status'}
        "(SELECT count(*) FROM $table WHERE $column IN ($($jobs[$table])) AND deleted_at IS NULL)"
    }
    return 'SELECT ' + ($parts -join '+')
}

function Expected-MainServices([string]$Root) {
    $arguments = @('compose','--project-directory',$Root,'--env-file',(Join-Path $Root '.env'),'-f',(Join-Path $Root 'compose.yaml'))
    foreach ($profile in $script:Profiles) {$arguments += @('--profile',$profile)}
    $configuration = Decode-Json (Run-Docker ($arguments+@('config','--format','json')))
    return @($configuration.services.PSObject.Properties | Where-Object {$_.Value.restart -ne 'no'} | ForEach-Object {$_.Name})
}

function Register-TransferBackup([string]$Base, [string]$Token, $Manifest, [Nullable[double]]$Duration) {
    $artifacts = @($Manifest.files | Where-Object {$_.path -in @('payload.enc','images.tar.gz')} | ForEach-Object {@{name=$_.path;sha256=$_.sha256;bytes=$_.bytes}})
    $completed = if ($Manifest.completed_at) {$Manifest.completed_at} else {$Manifest.exported_at}
    $body = @{kind='backup';idempotency_key=[Guid]::NewGuid().ToString();artifacts=$artifacts;completed_at=$completed;duration_seconds=$Duration;verification='verified';evidence='Consistent encrypted export; database and volume fingerprints and artifact SHA256 recorded.'}
    return (Api $Base '/api/v1/system/backup-records' $Token 'POST' $body)
}
