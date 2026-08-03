@echo off
rem ASCII only. Do not put Korean text in this file.
rem
rem Commits every local change in this repo and pushes it to GitHub.
rem Use this after editing the app or adding/removing data files.
rem (The daily parquet upload only touches data/, so app code changes
rem  need this one.)
rem
rem Order matters: commit FIRST, then rebase onto origin, then push.
rem "git pull --rebase" refuses to run while there are unstaged changes.
setlocal
pushd "%~dp0" || (
    echo [ERROR] cannot enter folder: %~dp0
    pause
    exit /b 1
)

where git >nul 2>nul || (
    echo [ERROR] git not found. Install Git for Windows first.
    popd
    pause
    exit /b 1
)

set "BR="
for /f "delims=" %%b in ('git rev-parse --abbrev-ref HEAD 2^>nul') do set "BR=%%b"
if not defined BR (
    echo [ERROR] cannot detect current branch.
    popd
    pause
    exit /b 1
)

echo ============================================
echo  repo   : %~dp0
echo  branch : %BR%
echo ============================================
echo.
echo  --- changes ---
git status --short
echo.

set "DIRTY="
for /f "delims=" %%l in ('git status --porcelain') do set "DIRTY=1"
if not defined DIRTY (
    echo  nothing to commit. checking for unpushed commits...
    goto :SYNC
)

set "MSG="
set /p MSG="  commit message (blank = auto): "
if not defined MSG set "MSG=update"

echo.
echo  [1/3] commit
git add -A
git commit -m "%MSG%"
if errorlevel 1 (
    echo.
    echo  [ERROR] commit failed.
    popd
    pause
    exit /b 1
)

:SYNC
echo.
echo  [2/3] rebase onto origin/%BR%
git pull --rebase origin %BR%
if errorlevel 1 (
    echo.
    echo  [ERROR] rebase failed - there is a conflict with GitHub.
    echo          To undo and get back to where you were:
    echo              git rebase --abort
    popd
    pause
    exit /b 1
)

echo.
echo  [3/3] push
git push origin %BR%
if errorlevel 1 (
    echo.
    echo  [ERROR] push failed. Check your GitHub login.
) else (
    echo.
    echo  [OK] pushed to origin/%BR%.
    git log --oneline -3
)

echo.
popd
pause
