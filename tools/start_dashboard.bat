@echo off
chcp 936 >nul
setlocal EnableExtensions EnableDelayedExpansion

rem ============================================================
rem  启动 Django 看板（本仓库根目录），然后打开 /dashboard/
rem
rem  判据：
rem    200 / 302 -> 本项目（302 = 未登录，跳登录页，正常）
rem    404       -> 8000 被别的 Django 占着
rem    000/其它  -> 没起
rem
rem  NGDCS_NO_OPEN=1 时只检测/启动，不打开浏览器
rem ============================================================

set "PROJ=%~dp0.."
set "PY=%PROJ%\.venv\Scripts\python.exe"
set "URL=http://127.0.0.1:8000/dashboard/"

if not exist "%PROJ%\manage.py" (
    echo [错误] 找不到 %PROJ%\manage.py
    goto :fail
)

if not exist "%PROJ%\dashboard\urls.py" (
    echo [错误] 当前目录没有 dashboard 应用。
    echo        请确认这是 NGDCS-portfolio 仓库的根目录。
    goto :fail
)

if not exist "%PY%" (
    echo [错误] 找不到虚拟环境解释器：
    echo        %PY%
    echo        先在本仓库根目录创建 .venv 并安装依赖。
    goto :fail
)

rem MySQL 只提示、不代劳启动
sc.exe query MySQL80 | findstr /i "RUNNING" >nul
if errorlevel 1 (
    echo [提示] MySQL80 服务没在运行，面板上会没有数据。
    echo.
)

call :probe
if "!HTTP!"=="200" goto :open
if "!HTTP!"=="302" goto :open
if "!HTTP!"=="404" goto :wrong

rem 端口有人监听，但不是我们的 Django
netstat -ano | findstr ":8000 " | findstr "LISTENING" >nul
if not errorlevel 1 goto :occupied

cd /d "%PROJ%"
echo 启动 Django（8000）...
start "NGDCS Django" cmd /k "%PY% manage.py runserver 127.0.0.1:8000"

echo 等 Django 起来（最多 15 秒）...
for /l %%i in (1,1,15) do (
    call :probe
    if "!HTTP!"=="200" goto :open
    if "!HTTP!"=="302" goto :open
    ping -n 2 127.0.0.1 >nul
)

echo [错误] 15 秒内 /dashboard/ 没返回 200/302，请看新窗口里的报错。
goto :fail

:open
echo.
echo 看板已就绪：%URL%
if not defined NGDCS_NO_OPEN (
    start "" "%URL%"
    echo 已尝试在浏览器中打开。
)
exit /b 0

:wrong
echo.
echo [错误] /dashboard/ 返回 404，8000 不是本项目。
echo        先停掉占用 8000 的进程，再重跑本脚本。
echo        排查：netstat -ano ^| findstr ":8000 "
goto :fail

:occupied
echo.
echo [错误] 8000 已被占用，但 /dashboard/ 既不返回 200/302，也不返回 404。
echo        排查：netstat -ano ^| findstr ":8000 "
goto :fail

:fail
if not defined NGDCS_NO_OPEN pause
exit /b 1

:probe
set "HTTP=000"
for /f %%C in ('curl.exe -s -o NUL -w "%%{http_code}" --max-time 2 "%URL%"') do set "HTTP=%%C"
exit /b 0
