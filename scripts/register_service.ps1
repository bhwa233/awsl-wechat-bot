# 注册 AWSL WeChat Bot Daemon 为开机自启任务（任务计划程序）
# Windows 服务运行在 Session 0 无法访问桌面，因此使用任务计划程序在用户会话中运行
# 需要管理员权限运行

$ProjectDir = (Resolve-Path "$PSScriptRoot\..").Path
$PythonExe = Join-Path $ProjectDir "venv\Scripts\python.exe"
$DaemonScript = Join-Path $ProjectDir "daemon.py"
$TaskName = "awsl-wechat-bot"

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

# 删除已有的同名任务
schtasks /query /tn "$TaskName" >$null 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "删除已有任务: $TaskName"
    schtasks /delete /tn "$TaskName" /f
}

Write-Host "注册任务: $TaskName"
Write-Host "  Python: $PythonExe"
Write-Host "  Script: $DaemonScript"
Write-Host "  工作目录: $ProjectDir"

# 创建任务：用户登录时自动启动，最高权限，在用户桌面会话中运行
schtasks /create /tn "$TaskName" `
    /tr "`"$PythonExe`" -u `"$DaemonScript`"" `
    /sc onlogon `
    /rl highest `
    /f

if ($LASTEXITCODE -eq 0) {
    Write-Host "任务注册成功" -ForegroundColor Green
    Write-Host ""
    Write-Host "管理命令:"
    Write-Host "  schtasks /run /tn $TaskName        # 立即启动"
    Write-Host "  schtasks /end /tn $TaskName        # 停止"
    Write-Host "  schtasks /query /tn $TaskName      # 查看状态"
    Write-Host "  schtasks /delete /tn $TaskName /f  # 删除任务"
    Write-Host ""
    Write-Host "daemon API: http://localhost:8001"
    Write-Host "bot API:    http://localhost:8000"
} else {
    Write-Host "任务注册失败" -ForegroundColor Red
    exit 1
}
