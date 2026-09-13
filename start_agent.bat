@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not defined CR_AGENT_PYTHON set "CR_AGENT_PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%CR_AGENT_PYTHON%" (
    echo Python environment missing. Run setup.ps1 first, or set CR_AGENT_PYTHON.
    pause
    exit /b 1
)
"%CR_AGENT_PYTHON%" -X utf8 -u tools\launch_menu.py %*
set "agentExitCode=%errorlevel%"
if not "%agentExitCode%"=="0" pause
exit /b %agentExitCode%
