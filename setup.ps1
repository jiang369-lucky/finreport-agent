$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw '创建 Python 3.11 虚拟环境失败。' }
}
& '.venv\Scripts\python.exe' -m ensurepip --upgrade
if ($LASTEXITCODE -ne 0) { throw '初始化 pip 失败。' }
# All three build tools were released before the historical cutoff.
& '.venv\Scripts\python.exe' -m pip install --disable-pip-version-check 'pip==25.2' 'setuptools==80.9.0' 'wheel==0.45.1'
if ($LASTEXITCODE -ne 0) { throw '历史构建工具安装失败。' }
& '.venv\Scripts\python.exe' -m pip install --disable-pip-version-check --require-hashes --no-build-isolation -r requirements.lock
if ($LASTEXITCODE -ne 0) { throw '依赖安装失败。' }
if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
}
& '.venv\Scripts\python.exe' -X utf8 -m scripts.configure_database
if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL 配置失败。' }
Write-Host '安装完成。运行 start-db.ps1 初始化数据库，再运行 start.ps1。'
