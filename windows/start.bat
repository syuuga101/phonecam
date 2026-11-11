@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -3 phonecam_receiver.py
if errorlevel 1 pause
