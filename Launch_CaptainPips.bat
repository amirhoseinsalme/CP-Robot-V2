@echo off
title CaptainPips Launcher

echo Starting CaptainPips Bot...
start "CaptainPips Bot" cmd /k "cd /d "D:\CP Robot V2" && python -m captainpips.main"

timeout /t 3 /nobreak >nul

echo Opening Panel...
start http://localhost:8000

exit
