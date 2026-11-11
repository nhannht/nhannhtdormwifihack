@echo off
setlocal
set "DORMWIFI_ROOT=%~dp0.."
set "PYTHONPATH=%DORMWIFI_ROOT%;%PYTHONPATH%"
python -m dormwifi %*
