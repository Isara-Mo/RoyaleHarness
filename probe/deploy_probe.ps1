param([switch]$Restore, [switch]$Check)
$ErrorActionPreference = 'Stop'
if ($Restore -and $Check) { throw 'Choose -Restore or -Check.' }
$releaseRoot = Split-Path $PSScriptRoot -Parent
$python = if ($env:CR_AGENT_PYTHON) { $env:CR_AGENT_PYTHON } else { Join-Path $releaseRoot '.venv/Scripts/python.exe' }
if (!(Test-Path -LiteralPath $python)) { throw 'Run setup.ps1 first, or set CR_AGENT_PYTHON.' }
$installerArgs = @((Join-Path $releaseRoot 'tools/install_probe.py'))
if ($Restore) { $installerArgs += '--restore' }
if ($Check) { $installerArgs += '--check' }
& $python @installerArgs
if ($LASTEXITCODE -ne 0) { throw 'Probe operation failed; see error above.' }
