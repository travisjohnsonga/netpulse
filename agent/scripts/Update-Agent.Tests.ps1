# Pester tests for Update-Agent.ps1 version parsing. The script is dot-sourced
# (its dot-source guard defines the functions but skips the installer body).
#   Invoke-Pester agent/scripts/Update-Agent.Tests.ps1
BeforeAll {
    . "$PSScriptRoot/Update-Agent.ps1"
}

Describe 'Get-VersionFromText' {
    It 'extracts the version from the REAL timestamped stderr line' {
        # This is the exact shape the Go agent prints (the bug repro).
        Get-VersionFromText '2026/06/26 20:02:37 netpulse-agent v1.3.0' |
            Should -Be 'v1.3.0'
    }

    It 'extracts a dev/describe version with a commit suffix' {
        Get-VersionFromText '2026/06/26 20:02:37 netpulse-agent v1.3.0-4-g525bb42' |
            Should -Be 'v1.3.0-4-g525bb42'
    }

    It 'extracts a bare (no timestamp) version line' {
        Get-VersionFromText 'netpulse-agent v2.0.1' | Should -Be 'v2.0.1'
    }

    It 'returns (unreadable) when there is no version token' {
        Get-VersionFromText 'some unrelated output' | Should -Be '(unreadable)'
    }
}

Describe 'Get-AgentVersion (capture from a binary that writes version to STDERR)' {
    It 'reads the version from an executable that prints the timestamped line to STDERR' {
        # Simulate the agent: a tiny script that writes the real line to STDERR,
        # exercising the 2>&1 merge + ToString() coercion path that was broken.
        if ($IsWindows) {
            $fake = Join-Path $TestDrive 'fake-agent.cmd'
            Set-Content -Path $fake -Value '@echo 2026/06/26 20:02:37 netpulse-agent v1.3.0 1>&2'
        } else {
            $fake = Join-Path $TestDrive 'fake-agent.sh'
            Set-Content -Path $fake -Value "#!/bin/sh`necho '2026/06/26 20:02:37 netpulse-agent v1.3.0' >&2"
            chmod +x $fake
        }
        Get-AgentVersion $fake | Should -Be 'v1.3.0'
    }

    It 'returns (none) for a path that does not exist' {
        Get-AgentVersion (Join-Path $TestDrive 'nope.exe') | Should -Be '(none)'
    }
}
