# Test, build a credential-free member package, then restart the owner installation.
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")
$root = Split-Path -Parent $PSScriptRoot
$py = Find-HelixPython $root
if (-not $py) { throw "Python 3.10+ was not found. Install Python and try again." }
Push-Location $root
try {
  $env:PYTHONIOENCODING = "utf-8"; $env:PYTHONUTF8 = "1"
  & $py (Join-Path $root "server\test_helix05.py")
  if ($LASTEXITCODE -ne 0) { throw "Regression tests failed. Nothing was restarted." }
  & $py (Join-Path $root "server\test_registration.py")
  if ($LASTEXITCODE -ne 0) { throw "Registration tests failed. Nothing was restarted." }
  & $py (Join-Path $root "server\package_members.py")
  if ($LASTEXITCODE -ne 0) { throw "Packaging failed. Nothing was restarted." }
  Stop-Helix
  Start-Sleep -Milliseconds 700
  & (Join-Path $PSScriptRoot "helix.ps1")
  if (-not (Test-HelixUp)) { throw "Helix did not start. Check data\serve.err.log." }
  Write-Host "Member ZIP is in the releases folder beside shell. Local Helix is running." -ForegroundColor Green
} finally { Pop-Location }
