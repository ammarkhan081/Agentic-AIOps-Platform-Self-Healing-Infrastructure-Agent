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
Invoke-Step 'Backend import sorting' { & $python -m ruff check --fix --select I src tests }
Invoke-Step 'Backend format' { & $python -m ruff format src tests }
Pop-Location

Push-Location (Join-Path $repoRoot 'frontend')
Invoke-Step 'Frontend format' { & $npm run format }
Pop-Location

Write-Host 'Formatting complete.'
