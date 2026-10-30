@echo off
chcp 65001 >nul 2>&1
title MTCaptcha Solver API Server [Port 3050]
color 0A

echo ===========================================================
echo     MTCaptcha Automated Solver API (@Real_zezomod)
echo ===========================================================
echo.
echo Starting MTCaptcha Solver Server on port 3050...
python server.py --port 3050

pause
