@echo off
chcp 65001 >nul
title Alerte CROUS
cd /d "%~dp0"
python alerte_crous.py
pause
