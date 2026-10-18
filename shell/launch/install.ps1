# Helix Harness 3.1.1 安装（-Quiet：按上次的目录直接更新，不提问）：复制文件 → 找 Python → 建快捷方式 → 启动。重装/升级会保留整个 data\ 目录（成员名册、本机项目、账本、数据文件）。
param([string]$Dest = "", [switch]$Quiet)
. (Join-Path $PSScriptRoot "common.ps1")
$ErrorActionPreference = "Stop"
$src = Split-Path -Parent $PSScriptRoot
Write-Host ""; Write-Host "  Helix Harness 3.1.1 人机双螺旋 安装" -ForegroundColor Cyan; Write-Host ""
if (-not $Dest) { $Dest = if (Test-Path "D:\") { "D:\zcode-workspace\helix-harness\shell" } else { Join-Path $env:LOCALAPPDATA "HelixHarness\shell" } }
if (-not $Quiet) {
  $ans = Read-Host "  安装到 [$Dest]（直接回车确认，或输入其他目录）"
  if ($ans.Trim()) { $Dest = $ans.Trim().Trim('"') }
}

Write-Host "  [1/5] 停止旧版本服务（如果在运行）"
Stop-Helix; Start-Sleep -Milliseconds 500

Write-Host "  [2/5] 复制文件到 $Dest"
New-Item -ItemType Directory -Force -Path $Dest | Out-Null
$same = (Resolve-Path -LiteralPath $src).Path.TrimEnd("\") -ieq (Resolve-Path -LiteralPath $Dest).Path.TrimEnd("\")
if (-not $same) {
  & robocopy "$src" "$Dest" /E /NFL /NDL /NJH /NJS /NP /XD "$src\data" "__pycache__" "_devstate" | Out-Null
  if ($LASTEXITCODE -ge 8) { throw "复制失败（robocopy $LASTEXITCODE）" }
}
New-Item -ItemType Directory -Force -Path (Join-Path $Dest "data") | Out-Null
if (Test-Path (Join-Path $Dest "data\people.json")) { Write-Host "        已保留 data\（成员名册、本机项目、账本、数据文件）" -ForegroundColor DarkGray }

Write-Host "  [3/5] 查找 Python 3.1.10+"
$py = Find-HelixPython $Dest
if (-not $py) {
  Write-Host "  [X] 没有找到 Python 3.1.10+。请从 https://www.python.org 安装（勾选 Add to PATH），再重新运行本安装。" -ForegroundColor Red
  Read-Host "  按回车退出"; exit 1
}
Set-Content -LiteralPath (Join-Path $Dest "launch\python.txt") -Value $py -Encoding UTF8
Write-Host "        使用 $py" -ForegroundColor DarkGray

Write-Host "  [4/5] 创建快捷方式（桌面 + 开始菜单）"
$ws = New-Object -ComObject WScript.Shell
$ps = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$icon = Join-Path $Dest "app\icon.ico"
function New-HelixLnk([string]$path, [string]$arguments, [string]$desc) {
  $l = $ws.CreateShortcut($path); $l.TargetPath = $ps; $l.Arguments = $arguments; $l.WorkingDirectory = $Dest
  $l.IconLocation = "$icon,0"; $l.Description = $desc; $l.WindowStyle = 7; $l.Save()
}
$run = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Dest\launch\helix.ps1`""
$desk = [Environment]::GetFolderPath("Desktop")
$menu = Join-Path ([Environment]::GetFolderPath("Programs")) "Helix Harness"
New-Item -ItemType Directory -Force -Path $menu | Out-Null
New-HelixLnk (Join-Path $desk "Helix Harness.lnk") $run "Helix Harness · MASH-AIDD 人机双螺旋"
New-HelixLnk (Join-Path $menu "Helix Harness.lnk") $run "Helix Harness · MASH-AIDD 人机双螺旋"
New-HelixLnk (Join-Path $menu "停止 Helix 服务.lnk") "$run -Stop" "停止本机 Helix 服务"
New-HelixLnk (Join-Path $menu "卸载 Helix Harness.lnk") "-NoProfile -ExecutionPolicy Bypass -File `"$Dest\launch\uninstall.ps1`"" "卸载 Helix Harness"

$startup = Join-Path ([Environment]::GetFolderPath("Startup")) "Helix Harness 服务.lnk"
$auto = if ($Quiet) { if (Test-Path $startup) { "y" } else { "n" } } else { Read-Host "  开机时在后台启动 Helix 服务？(y/N)" }
if ($auto -match "^[yY]") { New-HelixLnk $startup "$run -ServerOnly" "开机启动 Helix 服务（不弹窗）"; Write-Host "        已设置开机自启" -ForegroundColor DarkGray }
elseif (Test-Path $startup) { Remove-Item $startup -Force }

@{ version = "3.1.1"; dest = $Dest; python = $py; installed_at = (Get-Date).ToString("s") } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Dest "launch\install.json") -Encoding UTF8

Write-Host "  [5/5] 启动"
& (Join-Path $Dest "launch\helix.ps1")
Write-Host ""; Write-Host "  完成。以后双击桌面「Helix Harness」即可打开；地址 $HelixUrl" -ForegroundColor Green
Write-Host "  成员名册保存在 $Dest\data\people.json（只存显示名与指纹，不存原名）"; Write-Host ""
if (-not $Quiet) { Start-Sleep 4 } else { Start-Sleep 2 }
