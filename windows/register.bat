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
    regsvr32 /s "%~dp0softcam.dll"
    if errorlevel 1 set "ERR=1"
) else (
    echo softcam.dll が見つかりません。
    set "ERR=1"
)
if exist "%~dp0softcam32.dll" (
    "%SystemRoot%\SysWOW64\regsvr32.exe" /s "%~dp0softcam32.dll"
)
echo.
if "%ERR%"=="0" (
    echo 登録が完了しました。OBS や Discord のカメラ一覧に「PhoneCam」が表示されます。
    echo このフォルダを移動した場合は、もう一度 register.bat を実行してください。
) else (
    echo 登録に失敗しました。管理者権限で実行しているか確認してください。
)
echo.
pause
