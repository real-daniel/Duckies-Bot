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
        --icon assets\duckies_companion.ico `
        --add-data "assets\duckies_companion.png;assets" `
        --paths src `
        companion_main.py
}
finally {
    Pop-Location
}

Write-Host "Built dist\DuckiesCompanion.exe"
