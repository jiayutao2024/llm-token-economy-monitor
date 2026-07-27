param(
    [string]$Time = "08:30"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$runner = Join-Path $projectRoot "run_update.ps1"
$taskName = "LLM-Market-Monitor-Mon-Fri"

if (-not (Test-Path -LiteralPath $runner)) {
    throw "找不到运行脚本：$runner"
}

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$runner`"" `
    -WorkingDirectory $projectRoot

$monday = New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek Monday -At $Time
$friday = New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek Friday -At $Time
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 20)

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger @($monday, $friday) `
    -Settings $settings `
    -Description "每周一和周五更新全球大模型商业化与 Token 经济 HTML 看板" `
    -Force | Out-Null

Get-ScheduledTask -TaskName $taskName | Select-Object TaskName, State
