@echo off
rem ASCII only. Do not put Korean text in this file.
rem Korean messages are printed by 이미지_변환.py instead.
rem Run this after replacing the image xlsx, then run 저장소_반영.bat.
pushd "%~dp0" || (
    echo [ERROR] cannot enter folder: %~dp0
    pause
    exit /b 1
)

set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY (
    where py >nul 2>nul && set "PY=py -3"
)
if not defined PY (
    echo [ERROR] python not found.
    popd
    pause
    exit /b 1
)

%PY% "%~dp0이미지_변환.py"
popd
