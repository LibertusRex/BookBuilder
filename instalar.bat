@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
echo ============================================================
echo  BookBuilder - instalacao (precisa de internet so desta vez)
echo ============================================================
where uv >nul 2>nul
if errorlevel 1 (
  echo Instalando o gerenciador "uv" (Astral)...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
  set "PATH=%USERPROFILE%\.local\bin;%PATH%"
)
echo.
echo Instalando o Python 3.12 e as bibliotecas (versoes fixas do uv.lock)...
uv sync --frozen
if errorlevel 1 goto erro
echo.
echo Baixando Tesseract OCR, modelos de idioma e o modelo de layout...
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\instalar_componentes.ps1"
if errorlevel 1 goto erro
echo.
echo Pronto! Use "iniciar.bat" para abrir o BookBuilder.
pause
exit /b 0
:erro
echo.
echo A instalacao falhou. Veja as mensagens acima.
pause
exit /b 1
