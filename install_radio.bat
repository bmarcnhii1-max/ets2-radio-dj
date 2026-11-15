@echo off
chcp 65001 >nul
title ETS2 Radio DJ — установка
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_radio.ps1"
