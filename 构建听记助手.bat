@echo off
setlocal

chcp 65001 >nul
title Build Tingji Assistant Electron

set "PROJECT_ROOT=%~dp0"
set "DESKTOP_DIR=%PROJECT_ROOT%apps\desktop-electron"
set "APP_EXE=%DESKTOP_DIR%\release-electron\win-unpacked\ASR Local.exe"

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

echo Building Tingji Assistant Electron desktop...
echo Project: %PROJECT_ROOT%
echo.
call npm run electron:package
set "EXIT_CODE=%ERRORLEVEL%"
popd >nul

if not "%EXIT_CODE%"=="0" (
    echo.
    echo Build failed. Exit code: %EXIT_CODE%
    echo Keep this window open to inspect the build error.
    echo.
    pause
    exit /b %EXIT_CODE%
)

if not exist "%APP_EXE%" (
    echo.
    echo Build finished, but the expected executable was not found:
    echo %APP_EXE%
    echo.
    pause
    exit /b 1
)

echo.
echo Build complete:
echo %APP_EXE%
echo.
pause
endlocal
