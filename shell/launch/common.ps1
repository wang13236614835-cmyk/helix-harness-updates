# Helix Harness 共用函数：找 Python、检查服务（被 helix.ps1 / install.ps1 引用）
$ErrorActionPreference = "Stop"
$HelixPort = 18772
$HelixUrl = "http://127.0.0.1:$HelixPort/"

function Test-HelixPython([string]$exe) {
  if (-not $exe -or -not (Test-Path -LiteralPath $exe)) { return $false }
  if ($exe -like "*\WindowsApps\*") { return $false }   # 应用商店占位程序，不是真 Python
  try { $v = & $exe -c "import sys;print('%d.%d' % sys.version_info[:2])" 2>$null } catch { return $false }
  if (-not $v) { return $false }
  $p = "$v".Trim().Split("."); return ([int]$p[0] -eq 3 -and [int]$p[1] -ge 10)
}

function Find-HelixPython([string]$root) {
  $bundledPython = Join-Path (Split-Path -Parent $root) "runtime\python\python.exe"
  if (Test-HelixPython $bundledPython) { return $bundledPython }
  $saved = Join-Path $root "launch\python.txt"
  if (Test-Path -LiteralPath $saved) { $s = (Get-Content -LiteralPath $saved -Raw -Encoding UTF8).Trim(); if (Test-HelixPython $s) { return $s } }
  $c = New-Object System.Collections.Generic.List[string]
  try { $py = (& py -3 -c "import sys;print(sys.executable)" 2>$null); if ($py) { $c.Add("$py".Trim()) } } catch {}
  foreach ($n in @("python.exe", "python3.exe")) { Get-Command $n -All -ErrorAction SilentlyContinue | ForEach-Object { $c.Add($_.Source) } }
  $c.Add("D:\aidd destoop\.venv_build\Scripts\python.exe")
  $c.Add("D:\aidd destoop\.venv\Scripts\python.exe")
  Get-ChildItem -Path "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe", "$env:ProgramFiles\Python3*\python.exe", "C:\Python3*\python.exe" -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | ForEach-Object { $c.Add($_.FullName) }
  foreach ($e in $c) { if (Test-HelixPython $e) { return $e } }
  return $null
}

function Test-HelixUp {
  try { $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 -Uri ($HelixUrl + "api/health"); return ($r.StatusCode -eq 200) } catch { return $false }
}

function Stop-Helix {
  $expectedScript = [IO.Path]::GetFullPath((Join-Path (Split-Path -Parent $PSScriptRoot) 'server\serve.py'))
  $listeners = @(Get-NetTCPConnection -LocalPort $HelixPort -State Listen -ErrorAction SilentlyContinue)
  foreach ($listener in $listeners | Sort-Object OwningProcess -Unique) {
    $helixProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $($listener.OwningProcess)" -ErrorAction Stop
    $scriptArgument = '(?i)(?:^|[\s"])' + [regex]::Escape($expectedScript) + '(?=[\s"]|$)'
    if ($helixProcess.Name -notlike 'python*' -or $helixProcess.CommandLine -notmatch $scriptArgument) {
      throw "Port $HelixPort belongs to another application or installation; it was not stopped."
    }
    Stop-Process -Id $helixProcess.ProcessId -ErrorAction Stop
  }
}

function Open-HelixWindow {
  $browsers = @("${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe", "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe", "$env:ProgramFiles\Google\Chrome\Application\chrome.exe", "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe")
  $b = $browsers | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
  if ($b) { Start-Process -FilePath $b -ArgumentList "--app=$HelixUrl", "--window-size=1600,1000" } else { Start-Process $HelixUrl }
}
