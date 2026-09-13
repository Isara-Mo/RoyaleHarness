param(
    [switch]$DryRun,
    [switch]$Once,
    [switch]$HeroMusketeer,
    [switch]$BaseOnly,
    [string]$Checkpoint = 'hog26',
    [ValidateSet('reference', 'extended')]
    [string]$ObservationProfile = 'reference',
    [switch]$ExperimentalOrigins
)
$ErrorActionPreference = 'Stop'
if ($HeroMusketeer -and $BaseOnly) { throw 'Choose either HeroMusketeer or BaseOnly, not both.' }
if ($ExperimentalOrigins -and $ObservationProfile -ne 'extended') {
    throw 'ExperimentalOrigins requires ObservationProfile extended.'
}
$python = if ($env:CR_AGENT_PYTHON) { $env:CR_AGENT_PYTHON } else { Join-Path $PSScriptRoot '.venv/Scripts/python.exe' }
if (!(Test-Path -LiteralPath $python)) { throw "Python environment missing: $python" }
Push-Location $PSScriptRoot
try {
    $agentArgs = @('-u', 'main.py', '--checkpoint', $Checkpoint, '--observation-profile', $ObservationProfile)
    if ($DryRun) { $agentArgs += '--dry-run' }
    if ($Once) { $agentArgs += '--once' }
    if ($HeroMusketeer) { $agentArgs += '--hero-musketeer' }
    if ($BaseOnly) { $agentArgs += '--base-only' }
    if ($ExperimentalOrigins) { $agentArgs += '--experimental-origins' }
    & $python @agentArgs
} finally {
    Pop-Location
}
