# Offline regression checks. Never invokes ADB, deployment, or the game.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'probe_artifacts.ps1')
$realProbeRoot = $script:ProbeArtifactRoot
$artifactWorkspace = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'artifacts'))
$fixture = Join-Path $artifactWorkspace ('validation-' + [Guid]::NewGuid().ToString('N'))
$checks = 0
function Assert-Rejected([scriptblock]$Action, [string]$Expected) {
    $rejected = $false
    try { & $Action | Out-Null }
    catch {
        if ($_.Exception.Message -notlike "*$Expected*") { throw }
        $rejected = $true
    }
    if (!$rejected) { throw "Expected rejection: $Expected" }
    $script:checks++
}
function Write-FixtureManifest($Manifest) {
    [IO.File]::WriteAllText((Join-Path $fixture 'artifacts/candidates/experimental/build.json'),
        ($Manifest | ConvertTo-Json -Depth 8), [Text.UTF8Encoding]::new($false))
}
try {
    $stable = Get-ProbeArtifact
    if ($stable.sha256 -ne $script:StableProbeSha256 -or $stable.producer_origins) { throw 'Wrong default artifact' }
    $checks++
    New-Item -ItemType Directory -Path (Join-Path $fixture 'artifacts/stable'),
        (Join-Path $fixture 'artifacts/candidates/experimental') -Force | Out-Null
    $fixtureStable = Join-Path $fixture 'artifacts/stable/libscid_sdk.so'
    Copy-Item -LiteralPath $stable.path -Destination $fixtureStable
    [IO.File]::WriteAllText((Join-Path $fixture 'probe_features.h'), '#define TEST_FEATURE 1')
    [IO.File]::WriteAllText((Join-Path $fixture 'heal_events.inc'), '// fixture include')
    [IO.File]::WriteAllText((Join-Path $fixture 'heal_events_arm64.S'), '// fixture assembly')
    [IO.File]::WriteAllText((Join-Path $fixture 'artifacts/candidates/experimental/libscid_sdk.so'), 'fixture-candidate')
    # An arbitrary legacy-path artifact must never affect the default selection.
    [IO.File]::WriteAllText((Join-Path $fixture 'libscid_sdk.so'), 'wrong-legacy-artifact')
    $script:ProbeArtifactRoot = $fixture
    if ((Get-ProbeArtifact).sha256 -ne $stable.sha256) { throw 'Legacy file changed default selection' }
    $checks++
    $manifest = [ordered]@{
        schema = 'nulls-probe-build.v1'; profile = 'experimental'
        validation = 'compiled_not_live_validated'; producer_origins = $true
        sha256 = (Get-FileHash -LiteralPath (Join-Path $fixture 'artifacts/candidates/experimental/libscid_sdk.so') -Algorithm SHA256).Hash.ToLowerInvariant()
        source_files = @(Get-ProbeSourceHashes)
    }
    Write-FixtureManifest $manifest
    if (!(Get-ProbeArtifact -Profile Experimental).producer_origins) { throw 'Experimental fixture not selected' }
    $checks++
    [array]::Reverse($manifest.source_files)
    Write-FixtureManifest $manifest
    if (!(Get-ProbeArtifact -Profile Experimental).producer_origins) { throw 'Source ordering changed artifact validation' }
    $checks++

    $manifest.producer_origins = $false
    Write-FixtureManifest $manifest
    Assert-Rejected { Get-ProbeArtifact -Profile Experimental } 'profile/feature manifest mismatch'
    $manifest.producer_origins = $true
    $manifest.profile = 'stable-candidate'
    Write-FixtureManifest $manifest
    Assert-Rejected { Get-ProbeArtifact -Profile Experimental } 'profile/feature manifest mismatch'
    $manifest.profile = 'experimental'
    $goodHash = $manifest.sha256
    $manifest.sha256 = '0' * 64
    Write-FixtureManifest $manifest
    Assert-Rejected { Get-ProbeArtifact -Profile Experimental } 'binary checksum mismatch'
    $manifest.sha256 = $goodHash
    Write-FixtureManifest $manifest
    [IO.File]::WriteAllText((Join-Path $fixture 'probe_features.h'), '#define TEST_FEATURE 0')
    Assert-Rejected { Get-ProbeArtifact -Profile Experimental } 'Candidate source changed'
    [IO.File]::WriteAllText((Join-Path $fixture 'extra.inc'), '// new source')
    Assert-Rejected { Get-ProbeArtifact -Profile Experimental } 'Candidate source list changed'
    [IO.File]::WriteAllText($fixtureStable, 'wrong-stable-artifact')
    Assert-Rejected { Get-ProbeArtifact } 'stable probe checksum mismatch'
    Write-Output "Passed $checks offline artifact checks; no ADB commands executed."
}
finally {
    $script:ProbeArtifactRoot = $realProbeRoot
    if (Test-Path -LiteralPath $fixture) {
        $resolvedFixture = (Resolve-Path -LiteralPath $fixture).Path
        if (![IO.Path]::GetFullPath($resolvedFixture).StartsWith($artifactWorkspace + [IO.Path]::DirectorySeparatorChar,
                [StringComparison]::OrdinalIgnoreCase)) { throw 'Fixture cleanup path escaped artifact workspace' }
        Remove-Item -LiteralPath $resolvedFixture -Recurse -Force
    }
}
