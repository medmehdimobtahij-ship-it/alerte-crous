@echo off
chcp 65001 >nul
title Connexion CROUS
cd /d "%~dp0"
python connexion.py
pause
