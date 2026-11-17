@echo off
rem Thin wrapper: the real launcher lives next to the .venv and build scripts.
call "%~dp0apps\windows\rc003\start-dev.cmd" %*
