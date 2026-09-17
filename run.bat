@echo off
chcp 65001 >nul
cd /d "%~dp0"
python huijin_monitor.py >> run.log 2>&1
