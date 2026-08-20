$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $ProjectRoot
try {
    python -m PyInstaller `
        --noconfirm `
        --clean `
        --onefile `
        --windowed `
        --name DuckiesCompanion `
        --paths src `
        companion_main.py
}
finally {
    Pop-Location
}

Write-Host "Built dist\DuckiesCompanion.exe"
