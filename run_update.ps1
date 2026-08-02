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

& $pythonLauncher -3.10 (Join-Path $projectRoot "scripts\collect_macro_indicators.py") --project-root $projectRoot
if ($LASTEXITCODE -ne 0) {
    throw "Macro indicator update failed, exit code: $LASTEXITCODE"
}

& $pythonLauncher -3.10 (Join-Path $projectRoot "存储日报\scripts\run_storage_intel.py") --hours 30 --target-news 12 --max-news 15
if ($LASTEXITCODE -ne 0) {
    throw "存储产业情报更新失败，退出码：$LASTEXITCODE"
}

& $pythonLauncher -3.10 (Join-Path $projectRoot "scripts\collect_storage_prices.py") --project-root $projectRoot
if ($LASTEXITCODE -ne 0) {
    throw "Storage price update failed, exit code: $LASTEXITCODE"
}

& $pythonLauncher -3.10 (Join-Path $projectRoot "scripts\build_unified_data.py") --project-root $projectRoot
if ($LASTEXITCODE -ne 0) {
    throw "双维数据模型构建失败，退出码：$LASTEXITCODE"
}

& $pythonLauncher -3.10 (Join-Path $projectRoot "scripts\build_github_pages.py") --project-root $projectRoot
if ($LASTEXITCODE -ne 0) {
    throw "GitHub Pages 构建失败，退出码：$LASTEXITCODE"
}

& $pythonLauncher -3.10 (Join-Path $projectRoot "scripts\build_online_worker.py") --project-root $projectRoot
if ($LASTEXITCODE -ne 0) {
    throw "在线 Worker 打包失败，退出码：$LASTEXITCODE"
}
