$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python environment creation failed.' }
}
& '.\.venv\Scripts\python.exe' -m pip --isolated install --index-url https://pypi.org/simple -r requirements-dev.lock
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
& '.\.venv\Scripts\python.exe' -m pip --isolated install --index-url https://pypi.org/simple --no-deps -e .
if ($LASTEXITCODE -ne 0) { throw 'Package installation failed.' }
npm ci --no-fund
if ($LASTEXITCODE -ne 0) { throw 'Frontend installation failed.' }
npm run build
if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
}
Write-Host 'Setup complete. Configure .env locally, then run scripts/start.ps1.'
