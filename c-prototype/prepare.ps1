$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) {
        py -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Unable to create virtual environment.' }
    }
    & ./.venv/Scripts/python.exe -m pip install -r requirements.lock.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
    & ./.venv/Scripts/python.exe -m c_app.cli migrate
    if ($LASTEXITCODE -ne 0) { throw 'Database migration failed.' }
    Write-Host 'Ready. Follow README to create your administrator, import data, and start the app and worker.'
} finally {
    Pop-Location
}
