# 安装 Servy (Windows 服务管理器)
# 需要管理员权限运行

Write-Host "正在安装 Servy..."
winget install --id aelassas.Servy --source winget --accept-package-agreements --accept-source-agreements

if ($LASTEXITCODE -eq 0) {
    Write-Host "Servy 安装成功"
} else {
    Write-Host "Servy 安装失败，请检查 winget 是否可用" -ForegroundColor Red
    exit 1
}
