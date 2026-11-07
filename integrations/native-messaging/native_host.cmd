@echo off
setlocal
set "PYTHONPATH=%~dp0..\..\src"
python "%~dp0native_host.py"
