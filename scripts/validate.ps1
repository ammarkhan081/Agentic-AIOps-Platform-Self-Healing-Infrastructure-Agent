$ErrorActionPreference = 'Stop'

function Invoke-Step {
  param (
    [string]$Label,
    [scriptblock]$Command
  )

  Write-Host "==> $Label"
  & $Command
  if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
  }
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$windowsPython = Join-Path $repoRoot '.venv\Scripts\python.exe'
$unixPython = Join-Path $repoRoot '.venv/bin/python'
$python = if (Test-Path $windowsPython) { $windowsPython } else { $unixPython }
$npm = if ($env:OS -eq 'Windows_NT') { 'npm.cmd' } else { 'npm' }

Push-Location (Join-Path $repoRoot 'aiops')
Invoke-Step 'Backend lint' { & $python -m ruff check src tests }
Invoke-Step 'Backend format check' { & $python -m ruff format --check src tests }
Invoke-Step 'Backend static checks' { & $python -m compileall src tests }
Pop-Location

Push-Location (Join-Path $repoRoot 'frontend')
Invoke-Step 'Frontend lint' { & $npm run lint }
Invoke-Step 'Frontend format check' { & $npm run format:check }
Invoke-Step 'Frontend typecheck' { & $npm run typecheck }
Pop-Location

Write-Host 'Validation complete.'
