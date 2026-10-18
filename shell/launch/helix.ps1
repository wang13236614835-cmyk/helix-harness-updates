# Helix Harness 启动器：确保本机服务在跑（隐藏窗口），再用 Edge/Chrome 应用窗口打开。
#   -ServerOnly  只启动服务（开机自启用）    -Stop  停止服务
param([switch]$ServerOnly, [switch]$Stop)
. (Join-Path $PSScriptRoot "common.ps1")
$root = Split-Path -Parent $PSScriptRoot
if ((Get-Content -LiteralPath (Join-Path $root 'helix.config.json') -Raw -Encoding UTF8 | ConvertFrom-Json).member_mode) { $env:HELIX_RESEARCH_ONLY_BUNDLED = '1' }
if ($Stop) { Stop-Helix; Write-Host "Helix 服务已停止"; exit 0 }
if (-not (Test-HelixUp)) {
  $py = Find-HelixPython $root
  if (-not $py) {
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show("没有找到 Python 3.10+。`n请安装 Python（python.org），或确认 D:\aidd destoop\.venv_build 存在，然后重新运行「安装Helix.bat」。", "Helix Harness") | Out-Null
    exit 1
  }
  New-Item -ItemType Directory -Force -Path (Join-Path $root "data") | Out-Null
  $env:PYTHONIOENCODING = "utf-8"; $env:PYTHONUTF8 = "1"
  $pyw = Join-Path (Split-Path -Parent $py) "pythonw.exe"; if (Test-Path -LiteralPath $pyw) { $py = $pyw }   # 无控制台窗口
  Start-Process -FilePath $py -ArgumentList "`"$root\server\serve.py`"" -WorkingDirectory $root -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $root "data\serve.log") -RedirectStandardError (Join-Path $root "data\serve.err.log")
  for ($i = 0; $i -lt 80 -and -not (Test-HelixUp); $i++) { Start-Sleep -Milliseconds 250 }
  if (-not (Test-HelixUp)) {
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show("Helix 服务没有启动成功，请查看：`n$root\data\serve.err.log", "Helix Harness") | Out-Null
    exit 1
  }
}
if (-not $ServerOnly) { Open-HelixWindow }
