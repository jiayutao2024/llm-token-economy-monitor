[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$runner = Join-Path $projectRoot 'scripts\run_storage_intel.py'
$pythonLauncher = (Get-Command py.exe -ErrorAction Stop).Source
$logDir = Join-Path $projectRoot 'logs'
$taskLog = Join-Path $logDir 'scheduled-task.log'

if (-not (Test-Path -LiteralPath $logDir)) {
    New-Item -ItemType Directory -Path $logDir | Out-Null
}

& $pythonLauncher -3 $runner 2>&1 | Out-File -LiteralPath $taskLog -Encoding utf8 -Append
$exitCode = $LASTEXITCODE
if ($exitCode -ne 0) {
    throw "Storage intelligence runner failed with exit code $exitCode"
}
