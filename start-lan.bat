@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Polka ryadom - zapusk dlya lokalnoy seti

echo ============================================================
echo   «Полка рядом» — запуск для показа на двух ноутбуках
echo ============================================================
echo.

rem --- первый запуск: ставим зависимости ---
if not exist "backend\.venv\Scripts\python.exe" (
  echo Первый запуск: создаю окружение Python и ставлю зависимости...
  python -m venv backend\.venv || (echo Не найден Python 3.12 — установите его с python.org & pause & exit /b 1)
  backend\.venv\Scripts\python -m pip install -q -r backend\requirements.txt
)
if not exist "frontend\node_modules" (
  echo Первый запуск: ставлю зависимости интерфейса...
  pushd frontend
  call npm install --no-audit --no-fund
  popd
)

rem --- сервер и интерфейс в отдельных окнах ---
start "Polka API (не закрывать)" cmd /k "cd /d %~dp0backend && .venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000"
start "Polka WEB (не закрывать)" cmd /k "cd /d %~dp0frontend && npm run dev:lan"

echo.
echo Готово. Через 5-10 секунд откройте:
echo.
echo   На ЭТОМ ноутбуке:     http://localhost:5173
echo.
echo   На ВТОРОМ ноутбуке (подключён к той же Wi-Fi сети) — один из адресов:
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do (
  for /f "tokens=*" %%b in ("%%a") do echo     http://%%b:5173
)
echo.
echo Если Windows спросит про доступ Node.js к сети — нажмите «Разрешить».
echo Чтобы остановить систему, закройте окна «Polka API» и «Polka WEB».
echo.
pause
