$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonLauncher = "C:\Windows\py.exe"

if (-not (Test-Path -LiteralPath $pythonLauncher)) {
    throw "未找到 Python Launcher：$pythonLauncher"
}

& $pythonLauncher -3.10 (Join-Path $projectRoot "scripts\update_dashboard.py") --project-root $projectRoot
if ($LASTEXITCODE -ne 0) {
    throw "大模型商业化跟踪更新失败，退出码：$LASTEXITCODE"
}

& $pythonLauncher -3.10 (Join-Path $projectRoot "scripts\build_online_worker.py") --project-root $projectRoot
if ($LASTEXITCODE -ne 0) {
    throw "在线 Worker 打包失败，退出码：$LASTEXITCODE"
}
