@echo off
REM ===========================================================================
REM CONSOLIDATED MQ SETUP - all queue managers on ONE Windows host (ACECLUSTER)
REM
REM Windows-native equivalent of all_servers_full_setup.mqsc (which is a Linux
REM bash script and will NOT run under cmd / WSL against Windows MQ).
REM
REM Run from a normal Windows command prompt where the MQ commands work
REM (crtmqm / strmqm / runmqsc on PATH). All cluster CONNAMEs use localhost,
REM so every queue manager connects on this one box with no hosts-file changes.
REM
REM   cd /d C:\Workspace\accready\mqacemcp\platform_build\mqsetup
REM   all_servers_full_setup.bat
REM
REM Queue managers (all on this host, distinct ports):
REM   MQREPO1  1414  full repository
REM   MQQM1    1415  developer QM + MQ Console (SVRCONN channels)
REM   MQREPO2  1416  full repository
REM   MQNODE1  1420  ACE NODE1 QM (partial cluster member)
REM   MQNODE2  1421  ACE NODE2 QM (partial cluster member)
REM ===========================================================================

setlocal
REM Run from this script's own folder so the relative .mqsc paths resolve.
cd /d "%~dp0"

echo === SERVER1: MQREPO1 (full repository) ===
crtmqm MQREPO1
strmqm MQREPO1
runmqsc MQREPO1 < mqrepo1_defs.mqsc

echo.
echo === SERVER1: MQQM1 (developer QM + MQ Console) ===
crtmqm MQQM1
strmqm MQQM1
runmqsc MQQM1 < mqqm1_defs.mqsc

echo.
echo === SERVER2: MQREPO2 (full repository) ===
crtmqm MQREPO2
strmqm MQREPO2
runmqsc MQREPO2 < mqrepo2_defs.mqsc

echo.
echo === SERVER2: MQNODE1 (ACE NODE1 QM) ===
crtmqm MQNODE1
strmqm MQNODE1
runmqsc MQNODE1 < mqnode1_defs.mqsc

echo.
echo === SERVER3: MQNODE2 (ACE NODE2 QM) ===
crtmqm MQNODE2
strmqm MQNODE2
runmqsc MQNODE2 < mqnode2_defs.mqsc

echo.
echo Done.
endlocal
