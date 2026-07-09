@echo off
setlocal

chcp 65001 >nul
title Tingji Assistant Tauri Dev Launcher

set "PROJECT_ROOT=%~dp0"
set "DESKTOP_DIR=%PROJECT_ROOT%apps\desktop-tauri"

if not exist "%DESKTOP_DIR%\package.json" (
    echo Cannot find Tauri desktop project:
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

echo Starting Tingji Assistant Tauri desktop...
echo Project: %PROJECT_ROOT%
echo.
call npm run tauri:dev
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
