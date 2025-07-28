@echo off
:: Lancer PowerShell en mode administrateur avec activation du venv

:: Définir le chemin du script PowerShell
set SCRIPT_PATH=%~dp0Lancer_Venv_Admin.ps1

:: Lancer PowerShell avec élévation (admin), sans fermer après
powershell -Command "Start-Process powershell -ArgumentList '-NoExit','-ExecutionPolicy Bypass','-File \"%SCRIPT_PATH%\"' -Verb RunAs"