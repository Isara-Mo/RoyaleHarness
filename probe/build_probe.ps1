param(
    [ValidateSet('Stable', 'Experimental')][string]$Profile = 'Stable',
    [string]$NdkRoot = $env:ANDROID_NDK_HOME
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'probe_artifacts.ps1')

if (!$NdkRoot) { throw 'Supply -NdkRoot or ANDROID_NDK_HOME (tested with NDK r27c).' }
$ndkClang = Join-Path $NdkRoot 'toolchains/llvm/prebuilt/windows-x86_64/bin/aarch64-linux-android24-clang++.cmd'
if (!(Test-Path -LiteralPath $ndkClang)) { throw "Compiler missing: $ndkClang" }
$origins = if ($Profile -eq 'Experimental') { 1 } else { 0 }
$artifactProfile = if ($origins) { 'experimental' } else { 'stable-candidate' }
$outputDir = Join-Path $PSScriptRoot "artifacts/candidates/$artifactProfile"
New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
$output = Join-Path $outputDir 'libscid_sdk.so'
$sourceHashes = @(Get-ProbeSourceHashes)
$compilerArgs = @(
    '-std=c++17', '-O2', '-fPIC', '-fvisibility=hidden', '-shared',
    '-Wl,-z,max-page-size=16384', "-DCR_EXPERIMENTAL_PRODUCER_ORIGINS=$origins",
    '-o', $output,
    (Join-Path $PSScriptRoot 'nulls_probe.cpp'),
    (Join-Path $PSScriptRoot 'card_selection_arm64.S'),
    (Join-Path $PSScriptRoot 'spawn_relations_arm64.S'),
    (Join-Path $PSScriptRoot 'attack_start_arm64.S'),
    (Join-Path $PSScriptRoot 'heal_events_arm64.S'), '-llog', '-ldl'
)
& $ndkClang @compilerArgs
if ($LASTEXITCODE -ne 0) { throw 'Probe compilation failed' }
if (($sourceHashes | ConvertTo-Json -Depth 4 -Compress) -ne
    (@(Get-ProbeSourceHashes) | ConvertTo-Json -Depth 4 -Compress)) {
    throw 'Probe sources changed during compilation; candidate is not deployable. Rebuild it.'
}
$manifest = [ordered]@{
    schema = 'nulls-probe-build.v1'
    profile = $artifactProfile
    validation = 'compiled_not_live_validated'
    producer_origins = [bool]$origins
    sha256 = (Get-FileHash -LiteralPath $output -Algorithm SHA256).Hash.ToLowerInvariant()
    built_utc = [DateTime]::UtcNow.ToString('o')
    compiler = $ndkClang
    compiler_args = $compilerArgs
    source_files = $sourceHashes
}
[IO.File]::WriteAllText((Join-Path $outputDir 'build.json'),
    ($manifest | ConvertTo-Json -Depth 8), [Text.UTF8Encoding]::new($false))
Write-Output "Built $artifactProfile candidate: $output"
Write-Output "SHA256 $($manifest.sha256)"
Write-Output 'Compiled only; not live validated. Default deployment still uses the pinned historical stable binary.'
