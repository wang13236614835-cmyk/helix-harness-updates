# Helix Harness 卸载：停服务、删快捷方式、删安装目录；成员名册先备份到「文档」。
param([string]$Dest = "", [switch]$Relaunched)
$ErrorActionPreference = "Continue"
if (-not $Dest) {
  $cands = @((Split-Path -Parent $PSScriptRoot), "D:\zcode-workspace\helix-harness\shell", (Join-Path $env:LOCALAPPDATA "HelixHarness\shell"))
  $Dest = $cands | Where-Object { Test-Path (Join-Path $_ "launch\install.json") } | Select-Object -First 1
  if (-not $Dest) { Write-Host "  没找到已安装的 Helix Harness。" ; Start-Sleep 3; exit 0 }
}
# 先把自己拷到临时目录再执行，避免删除正在运行的脚本
if (-not $Relaunched) {
  $tmp = Join-Path $env:TEMP "helix_uninstall"; New-Item -ItemType Directory -Force -Path $tmp | Out-Null
  Copy-Item (Join-Path $PSScriptRoot "uninstall.ps1"), (Join-Path $PSScriptRoot "common.ps1") $tmp -Force
  Start-Process powershell -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$tmp\uninstall.ps1`" -Dest `"$Dest`" -Relaunched"; exit 0
}
. (Join-Path $PSScriptRoot "common.ps1")
Write-Host ""; Write-Host "  卸载 Helix Harness：$Dest" -ForegroundColor Cyan
if ((Read-Host "  确认卸载？(y/N)") -notmatch "^[yY]") { exit 0 }
Stop-Helix; Start-Sleep -Milliseconds 500
$data = Join-Path $Dest "data"
if (Test-Path $data) { $bk = Join-Path ([Environment]::GetFolderPath("MyDocuments")) ("Helix_data_backup_" + (Get-Date -Format "yyyyMMdd_HHmmss")); Copy-Item $data $bk -Recurse -Force; Write-Host "  名册、本机项目、账本和数据文件已备份到 $bk" }
Remove-Item (Join-Path ([Environment]::GetFolderPath("Desktop")) "Helix Harness.lnk") -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path ([Environment]::GetFolderPath("Programs")) "Helix Harness") -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path ([Environment]::GetFolderPath("Startup")) "Helix Harness 服务.lnk") -Force -ErrorAction SilentlyContinue
if ((Test-Path (Join-Path $Dest "server\serve.py")) -and (Test-Path (Join-Path $Dest "app\index.html"))) { Remove-Item $Dest -Recurse -Force }
Write-Host "  已卸载。（不会动 research_v4 和 Workbench；Helix 自己的数据已备份到「文档」）" -ForegroundColor Green; Start-Sleep 4
