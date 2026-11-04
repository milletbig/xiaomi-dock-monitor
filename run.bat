@echo off
setlocal
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"
python -m dockmon %*
endlocal
