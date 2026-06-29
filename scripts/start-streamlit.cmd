@echo off
REM Double-clickable launcher for the Streamlit UI stack.
REM Forwards any args to the PowerShell script (e.g. -Port 8503, -SkipMcp).
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-streamlit.ps1" %*
