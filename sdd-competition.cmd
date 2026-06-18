@echo off
if "%~2"=="" (
  echo Usage: sdd-competition.cmd PROJECT TASK_FILE [--rehearse]
  exit /b 2
)
if /I "%~3"=="--rehearse" (
  python "%~dp0scripts\sdd.py" --project "%~1" compete --task "%~2" --executor fixture
) else (
  python "%~dp0scripts\sdd.py" --project "%~1" compete --task "%~2"
)
