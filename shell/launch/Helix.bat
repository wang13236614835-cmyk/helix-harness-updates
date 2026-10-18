@echo off
rem Start Helix Harness (same as the desktop shortcut)
powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0helix.ps1" %*
