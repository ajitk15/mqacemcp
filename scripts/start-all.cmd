@echo off
REM Double-clickable launcher for the MCP chatbot stack.
REM Forwards any args to the PowerShell script (e.g. -SkipMcp, -CheckOnly).
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-all.ps1" %*
