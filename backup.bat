@echo off
chcp 65001 >nul
title 备份 DeepSeek V4 Flash 对话助手
cd /d "%~dp0"
python backup.py
echo.
pause
