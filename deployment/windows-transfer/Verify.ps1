param([Parameter(Mandatory=$true)][string]$TargetRoot)
. (Join-Path $PSScriptRoot 'Common.ps1')
$TargetRoot=(Resolve-Path -LiteralPath $TargetRoot).Path
$state=Read-Json (Join-Path $TargetRoot '.transfer/restore-state.json')
$base='http://127.0.0.1:'+$state.frontend_port
$token=Login $base $TargetRoot
if ((Api $base '/health/ready' '').status -ne 'ok') {throw 'Platform is not ready'}
if ((Api $base '/api/v1/me' $token).role -ne 'admin') {throw 'Login failed'}
$result=Read-Json (Join-Path $TargetRoot '.transfer/data/rebind-result.json')
$resources=$result.resources
$snapshot=Api $base ('/api/v1/source-snapshots/'+$resources.snapshot_id) $token
if ($snapshot.status -ne 'ready') {throw 'Historical source snapshot missing'}
$overview=Api $base ('/api/v1/servers/'+$resources.server_id+'/container-overview') $token
$business=Api $base ('/api/v1/containers/'+$resources.container_id) $token
$application=Api $base ('/api/v1/applications/'+$resources.application_id) $token
$deployments=@(Api $base ('/api/v1/applications/'+$resources.application_id+'/deployments') $token)
if (-not @($deployments | Where-Object {$_.container_id -eq $resources.container_id -and -not $_.ended_at}).Count) {throw 'New business deployment missing'}
$group=@($overview.containers | Where-Object {$_.application_id -eq $resources.application_id})
Wait-Until 'three testlab members running and healthy' {
    $script:latest=Api $base ('/api/v1/servers/'+$resources.server_id+'/container-overview') $token
    $ids=@($script:latest.containers | Where-Object {$_.application_id -eq $resources.application_id} | ForEach-Object {$_.id})
    $observations=@($script:latest.observations | Where-Object {$_.container_id -in $ids})
    $ids.Count -eq 3 -and $observations.Count -eq 3 -and -not @($observations | Where-Object {$_.state -ne 'running' -or $_.health -ne 'healthy'}).Count
}
if (-not (Api $base ('/api/v1/servers/'+$resources.server_id+'/test-ssh') $token 'POST').success) {throw 'SSH verification failed'}
Wait-Until 'fresh CPU and memory samples for the new computer' {
    $summaries=@(Api $base '/api/v1/monitoring/servers-summary' $token)
    $sample=$summaries | Where-Object {$_.server_id -eq $resources.server_id}
    $sample -and $sample.metrics.host_cpu_percent.availability -eq 'available' -and $sample.metrics.host_memory_percent.availability -eq 'available'
}
$collectors=Invoke-WebRequest -UseBasicParsing -Uri ($base+'/api/v1/servers/'+$resources.server_id+'/collectors-compose') -Headers @{Authorization='Bearer '+$token}
if ($collectors.StatusCode -ne 200 -or $collectors.RawContentLength -lt 1000) {throw 'Collector configuration download failed'}
$containerIds=@((Compose (Join-Path $TargetRoot '.transfer/main.compose.json') $state.project @('ps','-q')) -split "`n" | Where-Object {$_})
$statuses=@(Decode-Json (Run-Docker (@('inspect')+$containerIds)))
if (@($statuses | Where-Object {-not $_.State.Running -or ($_.State.PSObject.Properties['Health'] -and $_.State.Health.Status -ne 'healthy')}).Count) {throw 'A restored service is not healthy'}
foreach ($service in $state.main_services) {
    if (-not @($statuses | Where-Object {$_.Config.Labels.'com.docker.compose.service' -eq $service}).Count) {throw "Restored service missing: $service"}
}
Wait-Until 'worker heartbeats' {
    $components = Api $base '/api/v1/system/components' $token
    -not @($components.workers | Where-Object {$_.expected -and $_.status -ne 'healthy'}).Count
}
$historyUrl='http://127.0.0.1:'+$state.prometheus_port+'/api/v1/query?query='+[Uri]::EscapeDataString('count_over_time(up[24h])')
$history=Invoke-RestMethod -UseBasicParsing -Uri $historyUrl
if ($history.status -ne 'success' -or -not @($history.data.result).Count) {throw 'Monitoring history missing'}
$pg=(Compose (Join-Path $TargetRoot '.transfer/main.compose.json') $state.project @('ps','-q','postgres')).Trim()
$reportId=(Sql $pg $state.database_user $state.database "SELECT id FROM postmortem_reports WHERE status IN ('draft','approved') AND deleted_at IS NULL ORDER BY created_at DESC LIMIT 1").Trim()
$exports=@()
if ($reportId) {
    foreach ($format in @('pdf','docx')) {
        $download=Invoke-WebRequest -UseBasicParsing -Uri ($base+'/api/v1/reports/'+$reportId+'/export?format='+$format) -Headers @{Authorization='Bearer '+$token}
        if ($download.StatusCode -ne 200 -or $download.RawContentLength -lt 1000) {throw 'Historical report export failed'}
        $exports+=@($format)
    }
}
$report=@{passed=$true;checked_at=[DateTime]::UtcNow.ToString('o');application_id=$resources.application_id;current_server_id=$resources.server_id;current_containers=@($result.containers.PSObject.Properties).Count;testlab_running_healthy=3;historical_snapshot=$snapshot.content_hash;database_equal_before_workers=$state.database_restored;historical_approvals_untouched=$result.historical_approvals_untouched;external_provider_tested=$false;platform_url=$base;business_url=('http://127.0.0.1:'+$state.business_port)}
Write-Json (Join-Path $TargetRoot '.transfer/verification.json') $report
Write-Host ('Verified: login, restored history, new monitoring, 3 healthy testlab containers, SSH, deployment and workers. '+$base)
