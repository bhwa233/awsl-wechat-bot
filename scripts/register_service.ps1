# 注册 AWSL WeChat Bot Daemon 为 Servy 服务
# 需要先安装 Servy: .\scripts\install_servy.ps1
# 需要管理员权限运行

$ProjectDir = (Resolve-Path "$PSScriptRoot\..").Path
$PythonExe = Join-Path $ProjectDir "venv\Scripts\python.exe"
$DaemonScript = Join-Path $ProjectDir "daemon.py"
$ServiceName = "awsl-wechat-bot"

# 检查 Python 虚拟环境
if (-not (Test-Path $PythonExe)) {
    Write-Host "未找到 venv Python: $PythonExe" -ForegroundColor Red
    Write-Host "请先创建虚拟环境: python -m venv venv"
    exit 1
}

# 检查 daemon.py
if (-not (Test-Path $DaemonScript)) {
    Write-Host "未找到 daemon.py: $DaemonScript" -ForegroundColor Red
    exit 1
}

# 检查 Servy
$ServyExe = Get-Command servy -ErrorAction SilentlyContinue
if (-not $ServyExe) {
    Write-Host "未找到 Servy，请先运行 .\scripts\install_servy.ps1" -ForegroundColor Red
    exit 1
}

Write-Host "注册服务: $ServiceName"
Write-Host "  Python: $PythonExe"
Write-Host "  Script: $DaemonScript"
Write-Host "  工作目录: $ProjectDir"

servy install --name "$ServiceName" `
    --command "$PythonExe" `
    --args "-u `"$DaemonScript`"" `
    --working-dir "$ProjectDir" `
    --start-type auto `
    --enableHealth `
    --heartbeatInterval 60 `
    --maxFailedChecks 3 `
    --recoveryAction RestartService `
    --healthCheckUrl "http://localhost:8001/health"

if ($LASTEXITCODE -eq 0) {
    Write-Host "服务注册成功" -ForegroundColor Green
    Write-Host ""
    Write-Host "管理命令:"
    Write-Host "  servy start $ServiceName    # 启动服务"
    Write-Host "  servy stop $ServiceName     # 停止服务"
    Write-Host "  servy status $ServiceName   # 查看状态"
    Write-Host "  servy uninstall $ServiceName # 卸载服务"
} else {
    Write-Host "服务注册失败" -ForegroundColor Red
    exit 1
}
