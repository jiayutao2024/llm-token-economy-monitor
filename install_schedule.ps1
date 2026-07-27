param(
    [string]$Time = "07:30"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$runner = Join-Path $projectRoot "run_update.ps1"
$taskName = "AI-Compute-Storage-Monitor-Daily"
$oldTaskName = "LLM-Market-Monitor-Mon-Fri"

if (-not (Test-Path -LiteralPath $runner)) {
    throw "Runner script was not found: $runner"
}

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$runner`"" `
    -WorkingDirectory $projectRoot

$daily = New-ScheduledTaskTrigger -Daily -At $Time
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 20)

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $daily `
    -Settings $settings `
    -Description "Daily refresh for AI compute, token, GPU rental and storage dashboard" `
    -Force | Out-Null

if (Get-ScheduledTask -TaskName $oldTaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $oldTaskName -Confirm:$false
}

Get-ScheduledTask -TaskName $taskName | Select-Object TaskName, State
