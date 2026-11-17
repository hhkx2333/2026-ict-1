# Push only the reviewed C branch. Git Credential Manager handles sign-in locally.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    $cBranch = (git branch --show-current).Trim()
    if ($cBranch -ne 'codex/c-teacher-workbench') {
        throw 'Please switch to codex/c-teacher-workbench first. This script never pushes main.'
    }
    $pendingChanges = git status --porcelain
    if ($pendingChanges) {
        throw 'There are uncommitted changes. Review and commit them before pushing.'
    }
    git push --set-upstream origin codex/c-teacher-workbench
    if ($LASTEXITCODE -ne 0) {
        throw 'Push did not complete. Finish GitHub sign-in on this computer, then run this script again.'
    }
    Write-Host 'C branch pushed. Review / open pull request:'
    Write-Host 'https://github.com/hhkx2333/2026-ict-1/compare/main...codex/c-teacher-workbench'
} finally {
    Pop-Location
}
