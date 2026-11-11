@echo off
chcp 65001 >nul
setlocal
set "SELF=%~f0"
net session >nul 2>&1
if errorlevel 1 (
    powershell -NoProfile -Command "Start-Process -FilePath $env:SELF -Verb RunAs"
    exit /b
)
set "ERR=0"
if exist "%~dp0softcam.dll" (
    regsvr32 /u /s "%~dp0softcam.dll"
    if errorlevel 1 set "ERR=1"
) else (
    echo softcam.dll が見つかりません。
    set "ERR=1"
)
if exist "%~dp0softcam32.dll" (
    "%SystemRoot%\SysWOW64\regsvr32.exe" /u /s "%~dp0softcam32.dll"
)
echo.
if "%ERR%"=="0" (
    echo 登録を解除しました。「PhoneCam」カメラは一覧から消えます。
) else (
    echo 登録解除に失敗しました。管理者権限で実行しているか確認してください。
)
echo.
pause
