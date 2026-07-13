param(
    [ValidateSet('real', 'isolated')]
    [string]$DataProfile = 'real'
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$desktopDir = Join-Path $projectRoot 'apps\desktop-electron'
$portablePython = Join-Path $desktopDir 'runtime\python\python.exe'
$venvPython = Join-Path $projectRoot 'apps\worker-python\.venv\Scripts\python.exe'
$python = if (Test-Path -LiteralPath $portablePython) { $portablePython } elseif (Test-Path -LiteralPath $venvPython) { $venvPython } else { $null }

if (-not $python) {
    throw "No usable Python runtime. Checked: $portablePython and $venvPython"
}
if ($DataProfile -eq 'real' -and (Get-Process -Name 'ASR Local' -ErrorAction SilentlyContinue)) {
    throw 'ASR Local is already running. Close the installed/unpacked application before real-data debugging.'
}

$env:ASR_LOCAL_PROJECT_ROOT = $projectRoot
$env:ASR_LOCAL_PYTHON = $python
$env:ASR_LOCAL_V2_PIPELINE_MODE = 'production'
$env:ASR_LOCAL_DEBUG_DATA_PROFILE = $DataProfile

if ($DataProfile -eq 'real') {
    $dataRoot = Join-Path $env:APPDATA 'ASR Local'
    $env:ASR_LOCAL_CONFIG_DIR = Join-Path $dataRoot 'config'
    $env:ASR_LOCAL_STATE_DIR = Join-Path $dataRoot 'workflow'
    $env:ASR_LOCAL_OUTPUTS_DIR = Join-Path $projectRoot 'outputs'
    $env:ASR_LOCAL_LEGACY_OUTPUTS_DIR = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'ASR Local\outputs'
    $env:ASR_LOCAL_LOG_DIR = Join-Path $dataRoot 'logs'
} else {
    $dataRoot = Join-Path $projectRoot 'tmp\electron-debug'
    $env:ASR_LOCAL_DEBUG_DATA_ROOT = $dataRoot
    $env:ASR_LOCAL_CONFIG_DIR = Join-Path $dataRoot 'config'
    $env:ASR_LOCAL_STATE_DIR = Join-Path $dataRoot 'workflow'
    $env:ASR_LOCAL_OUTPUTS_DIR = Join-Path $dataRoot 'outputs'
    $env:ASR_LOCAL_LOG_DIR = Join-Path $dataRoot 'logs'
}

New-Item -ItemType Directory -Force -Path $env:ASR_LOCAL_LOG_DIR | Out-Null
$env:ASR_LOCAL_WORKER_LOG = Join-Path $env:ASR_LOCAL_LOG_DIR 'python-worker.log'

Write-Host "ASR Local hot debug ($DataProfile)"
Write-Host "Python:  $python"
Write-Host "Data:    $dataRoot"
Write-Host "Logs:    $env:ASR_LOCAL_LOG_DIR"
Write-Host 'No Electron Builder or NSIS step will run.'

Push-Location $desktopDir
try {
    if (-not (Test-Path -LiteralPath 'node_modules')) { npm install; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } }
    npm run electron:dev
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
