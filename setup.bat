@echo off
rem One-time setup: virtual env, packages, hand model, Gumball sprites. Safe to run again: skips what's done.
cd /d "%~dp0"

if not exist .venv\Scripts\python.exe (
    python -m venv .venv 2>nul || py -3 -m venv .venv || (echo Python 3.10+ not found. Install it from python.org with "Add to PATH" ticked, then run setup.bat again. & exit /b 1)
)
.venv\Scripts\python.exe -m pip install -q -r requirements.txt || exit /b 1

if not exist hand_landmarker.task (
    curl -L -o hand_landmarker.task https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task || exit /b 1
)

rem Windows' own tar (bsdtar) reads zips; Git's GNU tar would not, so call it by full path
if not exist gumball\sprites_v2\manifest.json (
    mkdir gumball 2>nul
    "%SystemRoot%\System32\tar.exe" -xf assets\gumball_sprites.zip -C gumball || exit /b 1
)

echo.
echo Done. Next: .\handmouse.bat --calibrate, then .\handmouse.bat --ctrl, then .\handmouse.bat
