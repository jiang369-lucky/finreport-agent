$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw '请先运行 setup.ps1 安装锁定的依赖。'
}
if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot '.env'))) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot '.env.example') -Destination (Join-Path $PSScriptRoot '.env')
}
& $pythonPath -X utf8 -m scripts.init_database
if ($LASTEXITCODE -ne 0) { throw '请先运行 start-db.ps1，确认 PostgreSQL 已就绪。' }
Write-Host '知报：http://127.0.0.1:8765；Ctrl+C 停止。'
& $pythonPath -X utf8 -m finagent.web
