@echo off
REM ===========================================================================
REM ACE integration node setup - creates ONE node and deploys the demo apps.
REM
REM Windows equivalent of `setup`. Parameterised so the SAME configuration is
REM applied to every node (NODE1, NODE2, ...). Run from the IBM ACE command
REM console (mqsi* commands on PATH).
REM
REM Usage:
REM   setup.bat <NODE_NAME> [QMGR_NAME]
REM     NODE_NAME  integration node to create/configure (default: ACEDEMO)
REM     QMGR_NAME  optional queue manager to associate with the node (-q)
REM
REM Examples:
REM   setup.bat NODE1 MQNODE1
REM   setup.bat NODE2 MQNODE2
REM ===========================================================================

setlocal
REM Run from this script's folder so the .bar files resolve.
cd /d "%~dp0"

set "NODE=%~1"
if "%NODE%"=="" set "NODE=ACEDEMO"
set "QMGR=%~2"

REM Create + start the integration node (associate the QM when given).
if "%QMGR%"=="" (
  mqsicreatebroker %NODE%
) else (
  mqsicreatebroker %NODE% -q %QMGR%
)
mqsistart %NODE%

REM Create integration servers (execution groups)
mqsicreateexecutiongroup %NODE% -e ACE_DEMO_TRANSFORM
mqsicreateexecutiongroup %NODE% -e ACE_DEMO_MESSAGING
mqsicreateexecutiongroup %NODE% -e ACE_DEMO_CONNECTORS
mqsicreateexecutiongroup %NODE% -e ACE_DEMO_CACHE

REM Deploy the BAR files
mqsideploy %NODE% -e ACE_DEMO_TRANSFORM  -a ACE_DEMO_TRANSFORM.bar
mqsideploy %NODE% -e ACE_DEMO_MESSAGING  -a ACE_DEMO_MESSAGING.bar
mqsideploy %NODE% -e ACE_DEMO_CONNECTORS -a ACE_DEMO_CONNECTORS.bar
mqsideploy %NODE% -e ACE_DEMO_CACHE      -a ACE_DEMO_CACHE.bar

endlocal
