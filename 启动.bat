@echo off
chcp 936 >nul
title Xilian Keyboard Console
cd /d "%~dp0"

set "PY="
if exist "%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\pythonw.exe" set "PY=%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\pythonw.exe"
if defined PY goto havepy
if exist "D:/WorkBuddyData/.workbuddy/binaries/python/envs/default/Scripts/pythonw.exe" set "PY=D:/WorkBuddyData/.workbuddy/binaries/python/envs/default/Scripts/pythonw.exe"
if defined PY goto havepy
where pythonw >nul 2>nul
if not errorlevel 1 set "PY=pythonw"
if defined PY goto havepy
where python >nul 2>nul
if not errorlevel 1 set "PY=python"
if defined PY goto havepy
goto nopython

:havepy
"%PY%" -c "import hid,PySide6" >nul 2>nul
if errorlevel 1 goto deps
start "" "%PY%" "%~dp0main.py"
exit /b 0

:deps
echo.
echo 缺少依赖，正在安装 hidapi 和 PySide6，第一次运行需要几分钟 ...
echo.
"%PY%" -m pip install hidapi PySide6 --disable-pip-version-check
start "" "%PY%" "%~dp0main.py"
exit /b 0

:nopython
echo.
echo [错误] 没找到 Python。
echo 请先安装 Python 3.10 或更高版本，安装时勾选 "Add Python to PATH"
echo 下载地址：https://www.python.org/downloads/
echo.
pause
exit /b 1
