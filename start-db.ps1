$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw '请先运行 setup.ps1。' }
& $pythonPath -X utf8 -m scripts.configure_database
if ($LASTEXITCODE -ne 0) { throw '数据库配置失败。' }
& docker compose up -d db
if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL 启动失败，请确认 Docker Desktop 正在运行。' }
$ready = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    & docker compose exec -T db sh -c 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"' *> $null
    if ($LASTEXITCODE -eq 0) { $ready = $true; break }
    Start-Sleep -Seconds 2
}
if (-not $ready) { throw 'PostgreSQL 未在限定时间内就绪。' }
& $pythonPath -X utf8 -m scripts.init_database
if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL 初始化或旧数据迁移失败。' }
Write-Host 'PostgreSQL 已就绪，运行 start.ps1 启动工作台。'
