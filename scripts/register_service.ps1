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
$ServyExe = Get-Command servy-cli -ErrorAction SilentlyContinue
if (-not $ServyExe) {
    Write-Host "未找到 servy-cli，请先运行 .\scripts\install_servy.ps1" -ForegroundColor Red
    exit 1
}

Write-Host "注册服务: $ServiceName"
Write-Host "  Python: $PythonExe"
Write-Host "  Script: $DaemonScript"
Write-Host "  工作目录: $ProjectDir"

$StdoutLog = Join-Path $ProjectDir "daemon_stdout.log"

servy-cli install --name "$ServiceName" `
    --path "$PythonExe" `
    --params="-u `"$DaemonScript`"" `
    --startupDir "$ProjectDir" `
    --startupType Automatic `
    --description "AWSL WeChat Bot Daemon Controller" `
    --stdout "$StdoutLog"

if ($LASTEXITCODE -eq 0) {
    Write-Host "服务注册成功" -ForegroundColor Green
    Write-Host ""
    Write-Host "管理命令:"
    Write-Host "  servy-cli start --name $ServiceName"
    Write-Host "  servy-cli stop --name $ServiceName"
    Write-Host "  servy-cli status --name $ServiceName"
    Write-Host "  servy-cli uninstall --name $ServiceName"
    Write-Host ""
    Write-Host "健康检查请通过 Servy Manager GUI 配置:"
    Write-Host "  URL: http://localhost:8001/health"
} else {
    Write-Host "服务注册失败" -ForegroundColor Red
    exit 1
}
