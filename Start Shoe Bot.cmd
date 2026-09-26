@echo off
title Shoe Bot
cd /d "%~dp0"
echo Starting Shoe Bot. Keep this window open while the bot is running.
echo To stop the bot, press Ctrl+C or close this window.
echo Only open one copy of this launcher.
echo.
"%~dp0.venv\Scripts\python.exe" local_runner.py
echo.
echo Shoe Bot has stopped. Any error is shown above.
pause
