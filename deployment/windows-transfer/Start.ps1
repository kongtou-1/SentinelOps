param([Parameter(Mandatory=$true)][string]$TargetRoot)
. (Join-Path $PSScriptRoot 'Common.ps1')
$TargetRoot=(Resolve-Path -LiteralPath $TargetRoot).Path
$state=Read-Json (Join-Path $TargetRoot '.transfer/restore-state.json')
if (-not $state.rebound) {throw 'Restore has not completed resource binding'}
Assert-Docker
$mainFile=Join-Path $TargetRoot '.transfer/main.compose.json'
$labFile=Join-Path $TargetRoot '.transfer/testlab.compose.json'
[void](Compose $mainFile $state.project @('up','-d','--no-build','--pull','never','--wait','postgres'))
[void](Compose $labFile $state.lab_project @('up','-d','--no-build','--pull','never','--wait','business'))
$business=(Compose $labFile $state.lab_project @('ps','-q','business')).Trim()
Write-Utf8 (Join-Path $TargetRoot '.local/testlab/ssh/allowed_ids') ((Run-Docker @('inspect',$business,'--format','{{.Id}}')).Trim()+"`n")
[void](Compose $labFile $state.lab_project @('up','-d','--no-build','--pull','never','--wait','ssh','traffic'))
$main=Read-Json $mainFile
$main.services.backend.environment.LLM_PROBE_SCHEDULER_ENABLED=([string][bool]$state.probe_scheduler).ToLower()
Write-Json $mainFile $main
[void](Compose $mainFile $state.project (@('up','-d','--no-build','--pull','never','--wait')+@($state.main_services)))
Write-Host ('Ready: http://127.0.0.1:'+$state.frontend_port)
