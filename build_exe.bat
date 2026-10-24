@echo off
rem ============================================================
rem  Build PyDM.exe  (Windows 7 / 8 / 8.1 / 10 / 11)
rem  Windows 8.1: use Python 3.8 (best) up to 3.12. NOT 3.13+.
rem  The browser extensions are embedded INSIDE the exe.
rem ============================================================
cd /d "%~dp0"
set PYEXE=
py -3.8 -c "import sys" >nul 2>&1 && set PYEXE=py -3.8
if "%PYEXE%"=="" py -3 -c "import sys" >nul 2>&1 && set PYEXE=py -3
if "%PYEXE%"=="" set PYEXE=python
echo Using: %PYEXE%
%PYEXE% -m pip install --upgrade pyinstaller certifi
if errorlevel 1 goto fail
%PYEXE% -m PyInstaller --noconsole --onefile --clean --name PyDM --icon pydm.ico ^
  --add-data "extension_chrome;extension_chrome" ^
  --add-data "extension_firefox;extension_firefox" pydm.py
if errorlevel 1 goto fail
if not exist dist\PyDM.exe goto fail
echo.
echo DONE:  %~dp0dist\PyDM.exe
echo (Run it, then Settings: enable "Run at Windows startup")
pause
exit /b 0
:fail
echo BUILD FAILED - see messages above
pause
exit /b 1
