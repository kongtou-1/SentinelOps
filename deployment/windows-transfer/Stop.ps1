param([Parameter(Mandatory=$true)][string]$TargetRoot)
. (Join-Path $PSScriptRoot 'Common.ps1')
$TargetRoot=(Resolve-Path -LiteralPath $TargetRoot).Path
$state=Read-Json (Join-Path $TargetRoot '.transfer/restore-state.json')
[void](Compose (Join-Path $TargetRoot '.transfer/testlab.compose.json') $state.lab_project @('stop'))
[void](Compose (Join-Path $TargetRoot '.transfer/main.compose.json') $state.project @('stop'))
Write-Host 'Stopped. All restored data retained.'
