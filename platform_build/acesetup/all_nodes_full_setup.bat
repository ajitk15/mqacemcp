@echo off
REM ===========================================================================
REM CONSOLIDATED ACE SETUP - both integration nodes on ONE host
REM
REM Creates NODE1 and NODE2 with IDENTICAL configuration by reusing setup.bat,
REM associating each with the queue manager created by the MQ setup
REM (NODE1 -> MQNODE1, NODE2 -> MQNODE2). Run mqsetup first so those QMs exist.
REM
REM Run from the IBM ACE command console (mqsi* commands on PATH):
REM   cd /d C:\Workspace\accready\mqacemcp\platform_build\acesetup
REM   all_nodes_full_setup.bat
REM ===========================================================================

setlocal
cd /d "%~dp0"

echo === NODE1 (QM MQNODE1) ===
call setup.bat NODE1 MQNODE1

echo.
echo === NODE2 (QM MQNODE2) ===
call setup.bat NODE2 MQNODE2

echo.
echo Done.
endlocal
