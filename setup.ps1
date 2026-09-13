param(
    [string]$Python,
    [ValidateSet('cu128', 'cpu')][string]$TorchBuild = 'cu128'
)
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    if (!(Test-Path -LiteralPath '.venv/Scripts/python.exe')) {
        if ($Python) { & $Python -m venv .venv } else { & py -3.12 -m venv .venv }
        if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 environment creation failed.' }
    }
    $runtime = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
    & $runtime -m pip install 'torch==2.11.0' --index-url "https://download.pytorch.org/whl/$TorchBuild"
    if ($LASTEXITCODE -ne 0) { throw 'Torch installation failed.' }
    & $runtime -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Runtime dependency installation failed.' }
    if (!(Test-Path -LiteralPath 'settings.local.json')) {
        Copy-Item -LiteralPath 'settings.example.json' -Destination 'settings.local.json'
    }
    Write-Output 'Edit settings.local.json, then run: .venv\Scripts\python.exe tools\preflight.py'
    Write-Output 'CPU installation: also set device to cpu in settings.local.json.'
} finally { Pop-Location }
