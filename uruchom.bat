@echo off
setlocal
cd /d "%~dp0"

echo [LaserBurn Studio] Uruchamianie srodowiska wirtualnego...

if not exist ".venv\Scripts\python.exe" (
    echo [BLAD] Nie znaleziono srodowiska wirtualnego w katalogu .venv!
    echo [BLAD] Utworz je poleceniem: python -m venv .venv
    pause
    exit /b 1
)

echo [LaserBurn Studio] Start aplikacji (tryb debugowania konsoli)...
echo ========================================================

:: Uruchomienie interpretera z wymuszeniem buforowania wyjscia na biezaco
.venv\Scripts\python.exe -u laserburn_studio.py

set EXIT_CODE=%ERRORLEVEL%

echo ========================================================
if %EXIT_CODE% NEQ 0 (
    echo [BLAD] Aplikacja zakonczyla dzialanie z kodem bledu: %EXIT_CODE%
    echo Sprawdz powyzszy traceback w konsoli.
    pause
) else (
    echo [LaserBurn Studio] Zakonczono pomyslnie.
)

endlocal