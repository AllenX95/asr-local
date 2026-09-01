$ErrorActionPreference = 'Stop'
$desktopDir = (Resolve-Path (Join-Path $PSScriptRoot '..\..\apps\desktop-electron')).Path
$releaseDir = Join-Path $desktopDir 'release-electron'
$stagingToken = '{0}-{1}' -f $PID, [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
$stagingDir = Join-Path $desktopDir "release-electron-staging-$stagingToken"
$targetAppDir = Join-Path $releaseDir 'win-unpacked'
$stagedAppDir = Join-Path $stagingDir 'win-unpacked'
$backupAppDir = Join-Path $releaseDir 'win-unpacked-backup'

function Assert-DesktopChildPath([string]$Path) {
    $resolved = [System.IO.Path]::GetFullPath($Path)
    $root = [System.IO.Path]::GetFullPath($desktopDir).TrimEnd([System.IO.Path]::DirectorySeparatorChar)
    if (-not $resolved.StartsWith($root + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to modify path outside Electron desktop directory: $resolved"
    }
    return $resolved
}

function Remove-SafeDirectory([string]$Path) {
    $resolved = Assert-DesktopChildPath $Path
    if (Test-Path -LiteralPath $resolved) {
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}

function Move-SafeDirectoryAtomic([string]$Source, [string]$Destination) {
    $resolvedSource = Assert-DesktopChildPath $Source
    $resolvedDestination = Assert-DesktopChildPath $Destination
    if (-not (Test-Path -LiteralPath $resolvedSource -PathType Container)) {
        throw "Source directory is missing: $resolvedSource"
    }
    if (Test-Path -LiteralPath $resolvedDestination) {
        throw "Atomic move destination already exists: $resolvedDestination"
    }
    for ($attempt = 1; $attempt -le 5; $attempt++) {
        try {
            [System.IO.Directory]::Move($resolvedSource, $resolvedDestination)
            return
        } catch {
            if ($attempt -eq 5) { throw }
            Start-Sleep -Milliseconds (250 * $attempt)
        }
    }
}

function Assert-PackagedRuntime([string]$AppDir) {
    $appExe = Join-Path $AppDir 'ASR Local.exe'
    $runtimePython = Join-Path $AppDir 'resources\runtime-root\runtime\python\python.exe'
    if (-not (Test-Path -LiteralPath $appExe)) {
        throw "Packaged executable is missing: $appExe"
    }
    if (-not (Test-Path -LiteralPath $runtimePython)) {
        throw "Packaged Python runtime is missing: $runtimePython"
    }

    $validation = @'
from pathlib import Path
import imageio_ffmpeg
import pyannote.audio
import qwen_asr
from app.audio import resolve_ffmpeg_executable

ffmpeg = Path(resolve_ffmpeg_executable())
assert ffmpeg.is_file(), f"packaged ffmpeg is missing: {ffmpeg}"
print(f"packaged-runtime-ok ffmpeg={ffmpeg}")
'@
    & $runtimePython -X utf8 -c $validation
    if ($LASTEXITCODE -ne 0) {
        throw "Packaged runtime validation failed with exit code $LASTEXITCODE"
    }
}

function Assert-TargetAppStopped {
    $targetExe = [System.IO.Path]::GetFullPath((Join-Path $targetAppDir 'ASR Local.exe'))
    $running = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.ExecutablePath -and $_.ExecutablePath.Equals($targetExe, [System.StringComparison]::OrdinalIgnoreCase) }
    if ($running) {
        $pids = ($running.ProcessId | Sort-Object) -join ', '
        throw "ASR Local is still running from the release directory (PID: $pids). Close it before activating the staged package. The existing release was not modified."
    }
}

Push-Location $desktopDir
try {
    npm run runtime:build
    if ($LASTEXITCODE -ne 0) { throw "Runtime build failed with exit code $LASTEXITCODE" }

    npm run electron:build
    if ($LASTEXITCODE -ne 0) { throw "Electron build failed with exit code $LASTEXITCODE" }

    $electronDist = Join-Path $desktopDir 'node_modules\electron\dist'
    if (-not (Test-Path -LiteralPath (Join-Path $electronDist 'electron.exe'))) {
        throw "Electron distribution is missing: $electronDist. Run npm install with a reachable Electron mirror once."
    }

    Remove-SafeDirectory $stagingDir
    & '.\node_modules\.bin\electron-builder.cmd' --win dir "--config.electronDist=$electronDist" "--config.directories.output=$stagingDir"
    if ($LASTEXITCODE -ne 0) { throw "Electron packaging failed with exit code $LASTEXITCODE" }
    Assert-PackagedRuntime $stagedAppDir
    Assert-TargetAppStopped

    New-Item -ItemType Directory -Force -Path $releaseDir | Out-Null
    if ((Test-Path -LiteralPath $backupAppDir) -and -not (Test-Path -LiteralPath $targetAppDir)) {
        Move-SafeDirectoryAtomic $backupAppDir $targetAppDir
    } else {
        Remove-SafeDirectory $backupAppDir
    }
    $hadPreviousPackage = Test-Path -LiteralPath $targetAppDir
    if ($hadPreviousPackage) {
        Move-SafeDirectoryAtomic $targetAppDir $backupAppDir
    }
    try {
        Move-SafeDirectoryAtomic $stagedAppDir $targetAppDir
    } catch {
        if ($hadPreviousPackage -and -not (Test-Path -LiteralPath $targetAppDir) -and (Test-Path -LiteralPath $backupAppDir)) {
            Move-SafeDirectoryAtomic $backupAppDir $targetAppDir
        }
        throw
    }

    Remove-SafeDirectory $backupAppDir
    foreach ($metadataName in @('builder-debug.yml', 'builder-effective-config.yaml')) {
        $metadataPath = Join-Path $stagingDir $metadataName
        if (Test-Path -LiteralPath $metadataPath) {
            Copy-Item -LiteralPath $metadataPath -Destination (Join-Path $releaseDir $metadataName) -Force
        }
    }
    Remove-SafeDirectory $stagingDir
    Write-Host "Packaged ASR Local activated safely: $targetAppDir"
} catch {
    throw
} finally {
    Pop-Location
}
