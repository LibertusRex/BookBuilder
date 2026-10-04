<#
  Gera o executável do BookBuilder (dist\BookBuilder\BookBuilder.exe) e, se o
  Inno Setup 6 estiver instalado, também o instalador
  (dist\instalador\BookBuilder-Setup-<versão>.exe).

  Pré-requisito: ter rodado instalar.bat (bibliotecas e componentes de OCR).
#>
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path 'vendor\tesseract\tesseract.exe') -or -not (Test-Path 'vendor\models\pp_doc_layoutv3.onnx')) {
    throw 'Componentes de OCR ausentes. Execute instalar.bat primeiro.'
}

Write-Host 'Gerando o executável com o PyInstaller...'
uv run --frozen pyinstaller bookbuilder.spec --noconfirm --clean
if ($LASTEXITCODE -ne 0) { throw 'O PyInstaller falhou.' }

$exe = Join-Path $root 'dist\BookBuilder\BookBuilder.exe'
if (-not (Test-Path $exe)) { throw 'O executável não foi gerado.' }
Write-Host "Executável: $exe"

$iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe", "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") |
    Where-Object { Test-Path $_ } | Select-Object -First 1
if ($iscc) {
    Write-Host 'Gerando o instalador com o Inno Setup...'
    & $iscc 'scripts\instalador.iss'
    if ($LASTEXITCODE -ne 0) { throw 'O Inno Setup falhou.' }
    Get-ChildItem 'dist\instalador' | ForEach-Object { "Instalador: " + $_.FullName }
} else {
    Write-Host ''
    Write-Host 'Inno Setup 6 não encontrado: o instalador não foi gerado.'
    Write-Host 'Para gerá-lo, instale o Inno Setup (winget install JRSoftware.InnoSetup) e rode este script de novo.'
    Write-Host 'A pasta dist\BookBuilder já funciona sozinha: basta copiá-la e abrir BookBuilder.exe.'
}
