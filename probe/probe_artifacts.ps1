# Shared artifact resolution. No ADB operations or filesystem mutations here.
$script:ProbeArtifactRoot = $PSScriptRoot
$script:StableProbeSha256 = '9d1c8d79712c7116e27c324dd9bcbd85d6be07923e7c8b989c61bee57cd095b0'

function Get-ProbeSourceHashes {
    Get-ChildItem -LiteralPath $script:ProbeArtifactRoot -File |
        Where-Object { $_.Extension -in '.cpp', '.inc', '.h', '.S' -or $_.Name -in 'build_probe.ps1', 'probe_artifacts.ps1' } |
        Sort-Object Name | ForEach-Object {
            [ordered]@{
                path = $_.Name
                sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            }
        }
}

function Get-ProbeArtifact {
    param([ValidateSet('Stable', 'StableCandidate', 'Experimental', 'Restore')][string]$Profile = 'Stable')
    if ($Profile -eq 'Restore') {
        $path = Join-Path $script:ProbeArtifactRoot 'installed-probe-before.so'
        if (!(Test-Path -LiteralPath $path -PathType Leaf)) { throw 'Original pre-probe backup does not exist' }
        return [pscustomobject]@{
            profile = 'restore-original-backup'; path = $path
            sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
            validation = 'historical_original_backup'; producer_origins = $null
        }
    }
    if ($Profile -eq 'Stable') {
        $path = Join-Path $script:ProbeArtifactRoot 'artifacts/stable/libscid_sdk.so'
        $hash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($hash -ne $script:StableProbeSha256) { throw 'Pinned stable probe checksum mismatch; no device operation permitted' }
        return [pscustomobject]@{
            profile = 'stable'; path = $path; sha256 = $hash
            validation = 'previously_live_validated_binary'; producer_origins = $false
        }
    }
    $candidateProfile = if ($Profile -eq 'Experimental') { 'experimental' } else { 'stable-candidate' }
    $directory = Join-Path $script:ProbeArtifactRoot "artifacts/candidates/$candidateProfile"
    $path = Join-Path $directory 'libscid_sdk.so'
    $manifest = Get-Content -LiteralPath (Join-Path $directory 'build.json') -Raw | ConvertFrom-Json
    if ($manifest.schema -ne 'nulls-probe-build.v1' -or $manifest.profile -ne $candidateProfile -or
        $manifest.validation -ne 'compiled_not_live_validated' -or
        $manifest.producer_origins -isnot [bool] -or
        $manifest.producer_origins -ne ($Profile -eq 'Experimental')) {
        throw 'Candidate profile/feature manifest mismatch; rebuild the requested profile'
    }
    $hash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($hash -ne $manifest.sha256) { throw 'Candidate binary checksum mismatch; rebuild the requested profile' }
    $expectedSources = @(Get-ProbeSourceHashes)
    $recordedSources = @($manifest.source_files)
    if ($expectedSources.Count -ne $recordedSources.Count) { throw 'Candidate source list changed; rebuild the requested profile' }
    # Windows PowerShell and PowerShell 7 can order punctuation differently.
    # Validate the complete unique path/hash set, not host-dependent sort order.
    foreach ($expected in $expectedSources) {
        $records = @($recordedSources | Where-Object { $_.path -ceq $expected.path })
        if ($records.Count -ne 1 -or $expected.sha256 -ne $records[0].sha256) {
            throw "Candidate source changed: $($expected.path); rebuild the requested profile"
        }
    }
    return [pscustomobject]@{
        profile = $candidateProfile; path = $path; sha256 = $hash
        validation = $manifest.validation; producer_origins = $manifest.producer_origins
    }
}
