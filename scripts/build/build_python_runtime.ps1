param(
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$DesktopDir = Join-Path $ProjectRoot 'apps\desktop-electron'
$WorkerVenv = Join-Path $ProjectRoot 'apps\worker-python\.venv'
$RuntimeDir = Join-Path $DesktopDir 'runtime\python'
$RuntimePython = Join-Path $RuntimeDir 'python.exe'
$VersionFile = Join-Path $RuntimeDir 'ASR_LOCAL_RUNTIME_VERSION'
$ExpectedVersion = 'python-3.12.2+chunked-dual-asr-v2'

if ((Test-Path $RuntimePython) -and (Test-Path $VersionFile) -and ((Get-Content -Raw $VersionFile).Trim() -eq $ExpectedVersion) -and -not $Force) {
    Write-Host "Python runtime is already current: $RuntimeDir"
    exit 0
}

if (-not (Test-Path (Join-Path $WorkerVenv 'Lib\site-packages'))) {
    throw "Validated worker environment is missing: $WorkerVenv"
}

if (Test-Path $RuntimeDir) {
    $resolved = [System.IO.Path]::GetFullPath($RuntimeDir)
    $allowed = [System.IO.Path]::GetFullPath((Join-Path $DesktopDir 'runtime'))
    if (-not $resolved.StartsWith($allowed + [System.IO.Path]::DirectorySeparatorChar)) {
        throw "Refusing to replace runtime outside $allowed"
    }
    Remove-Item -LiteralPath $RuntimeDir -Recurse -Force
}

$TempDir = Join-Path $ProjectRoot 'tmp\python-runtime-build'
New-Item -ItemType Directory -Force -Path $TempDir | Out-Null
$Archive = Join-Path $TempDir 'python-3.12.2-embed-amd64.zip'
if (-not (Test-Path $Archive)) {
    Write-Host 'Downloading fixed Python 3.12.2 embeddable runtime...'
    Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.2/python-3.12.2-embed-amd64.zip' -OutFile $Archive
}

New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
Expand-Archive -LiteralPath $Archive -DestinationPath $RuntimeDir -Force
New-Item -ItemType Directory -Force -Path (Join-Path $RuntimeDir 'Lib\site-packages') | Out-Null
Copy-Item -Path (Join-Path $WorkerVenv 'Lib\site-packages\*') -Destination (Join-Path $RuntimeDir 'Lib\site-packages') -Recurse -Force
if (Test-Path (Join-Path $WorkerVenv 'Scripts')) {
    Copy-Item -Path (Join-Path $WorkerVenv 'Scripts') -Destination (Join-Path $RuntimeDir 'Scripts') -Recurse -Force
}

@'
python312.zip
.
Lib
Lib/site-packages
import site
'@ | Set-Content -LiteralPath (Join-Path $RuntimeDir 'python312._pth') -Encoding ascii
Set-Content -LiteralPath $VersionFile -Value $ExpectedVersion -Encoding ascii

Write-Host 'Validating portable runtime imports...'
& $RuntimePython -X utf8 -c "import sqlite3, torch, transformers, soundfile, qwen_asr, pyannote.audio; print('runtime-ok', torch.__version__)"
if ($LASTEXITCODE -ne 0) {
    throw "Portable runtime validation failed with exit code $LASTEXITCODE"
}
Write-Host "Portable Python runtime ready: $RuntimeDir"
