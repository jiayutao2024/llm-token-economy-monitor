[CmdletBinding(SupportsShouldProcess)]
param()

foreach ($taskName in 'StorageIntel-Daily','StorageIntel-AM','StorageIntel-PM') {
    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    }
}
