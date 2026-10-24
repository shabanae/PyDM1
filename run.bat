@echo off
cd /d "%~dp0"
where pythonw >nul 2>&1 && (start "" pythonw pydm.py) || (start "" py -3 pydm.py)
