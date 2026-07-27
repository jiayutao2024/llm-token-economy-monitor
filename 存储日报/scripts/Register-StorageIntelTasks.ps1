[CmdletBinding()]
param(
    [string]$DailyTime = '07:30'
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$runner = Join-Path $projectRoot 'scripts\run_storage_intel.py'
$pythonExe = (& py.exe -3 -c 'import sys; print(sys.executable)').Trim()

if (-not (Test-Path -LiteralPath $runner)) {
    throw "Runner not found: $runner"
}

$action = New-ScheduledTaskAction -Execute $pythonExe -Argument "`"$runner`"" -WorkingDirectory $projectRoot
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 20) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

foreach ($oldTask in 'StorageIntel-AM','StorageIntel-PM') {
    if (Get-ScheduledTask -TaskName $oldTask -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $oldTask -Confirm:$false
    }
}

$parsedTime = [DateTime]::ParseExact($DailyTime, 'HH:mm', [Globalization.CultureInfo]::InvariantCulture)
$triggerAt = [DateTime]::Today.AddHours($parsedTime.Hour).AddMinutes($parsedTime.Minute)
$trigger = New-ScheduledTaskTrigger -Daily -At $triggerAt
Register-ScheduledTask -TaskName 'StorageIntel-Daily' -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description 'Storage industry daily intelligence report' -Force | Out-Null
Get-ScheduledTask -TaskName 'StorageIntel-Daily' | Select-Object TaskName,State
