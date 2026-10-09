$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    throw 'Run scripts/setup.ps1 first.'
}
if (-not (Test-Path -LiteralPath 'dist\index.html')) {
    throw 'Run npm run build first.'
}
& '.\.venv\Scripts\python.exe' -m uvicorn server.main:app --host 127.0.0.1 --port 8090
