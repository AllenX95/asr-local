@echo off
setlocal

chcp 65001 >nul
title Tingji Assistant

set "PROJECT_ROOT=%~dp0"
set "DESKTOP_DIR=%PROJECT_ROOT%apps\desktop-electron"
set "APP_EXE=%DESKTOP_DIR%\release-electron\win-unpacked\ASR Local.exe"

if exist "%APP_EXE%" (
    echo Starting Tingji Assistant Electron release...
    echo %APP_EXE%
    echo.
    set "ASR_LOCAL_LEGACY_CONFIG_DIR=%PROJECT_ROOT%config"
    set "ASR_LOCAL_LEGACY_OUTPUTS_DIR=%PROJECT_ROOT%outputs"
    start "" "%APP_EXE%"
    exit /b 0
)

if not exist "%DESKTOP_DIR%\package.json" (
    echo Cannot find Electron desktop project:
    echo %DESKTOP_DIR%
    echo.
    pause
    exit /b 1
)

pushd "%DESKTOP_DIR%" >nul
if not exist "node_modules" (
    echo Installing frontend dependencies...
    call npm install
    if errorlevel 1 (
        echo.
        echo npm install failed.
        pause
        exit /b 1
    )
)

echo Release executable was not found. Starting development mode instead...
echo Project: %PROJECT_ROOT%
echo.
call npm run electron:dev
set "EXIT_CODE=%ERRORLEVEL%"
popd >nul

if not "%EXIT_CODE%"=="0" (
    echo.
    echo Startup failed. Exit code: %EXIT_CODE%
    echo Check outputs\logs for application logs, or keep this window open for build errors.
    echo.
    pause
    exit /b %EXIT_CODE%
)

endlocal
