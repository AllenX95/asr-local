$ErrorActionPreference = 'Stop'
$desktopDir = (Resolve-Path (Join-Path $PSScriptRoot '..\..\apps\desktop-electron')).Path
Push-Location $desktopDir
try {
    npm run electron:build
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    $electronDist = Join-Path $desktopDir 'node_modules\electron\dist'
    if (-not (Test-Path -LiteralPath (Join-Path $electronDist 'electron.exe'))) {
        throw "Electron distribution is missing: $electronDist. Run npm install with a reachable Electron mirror once."
    }
    & '.\node_modules\.bin\electron-builder.cmd' --win dir "--config.electronDist=$electronDist"
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
