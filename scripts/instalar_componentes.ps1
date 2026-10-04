<#
  Baixa os componentes de OCR usados pelo BookBuilder (uma única vez, com internet):

    * Tesseract OCR 5.4.0 (build UB Mannheim, licença Apache-2.0)
    * modelos de idioma "tessdata_best": português e inglês
    * modelo de análise de layout PP-DocLayout V3 em ONNX (licença Apache-2.0)
    * llama.cpp b11381 com Vulkan (MIT), usado pelo Surya na placa de vídeo
    * dicionário VERO pt-BR do LibreOffice (LGPLv3/MPL), usado na fusão Tesseract × Surya
    * opcional (-ComSurya): modelo Surya OCR 2 (1,4 GB, licença OpenRAIL-M modificada)

  Tudo vai para a pasta vendor\ do BookBuilder. Os downloads são conferidos
  por SHA-256. Depois disso, o aplicativo funciona sem internet.
#>
param([switch]$ComSurya)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$vendor = Join-Path $root 'vendor'
$dl = Join-Path $root 'build\downloads'
New-Item -ItemType Directory -Force $vendor, $dl | Out-Null

function Get-Verified($url, $dest, $sha) {
    if (-not (Test-Path $dest) -or ((Get-FileHash $dest -Algorithm SHA256).Hash -ne $sha)) {
        Write-Host "Baixando $url"
        Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing
    }
    $h = (Get-FileHash $dest -Algorithm SHA256).Hash
    if ($sha -and $h -ne $sha) { throw "Arquivo corrompido ou alterado: $dest (SHA-256 $h)" }
}

# 1) Tesseract
$tessExe = Join-Path $vendor 'tesseract\tesseract.exe'
if (-not (Test-Path $tessExe)) {
    $setup = Join-Path $dl 'tesseract-setup.exe'
    Get-Verified 'https://github.com/UB-Mannheim/tesseract/releases/download/v5.4.0.20240606/tesseract-ocr-w64-setup-5.4.0.20240606.exe' `
        $setup 'C885FFF6998E0608BA4BB8AB51436E1C6775C2BAFC2559A19B423E18678B60C9'
    $sevenZip = @("$env:ProgramFiles\7-Zip\7z.exe", "${env:ProgramFiles(x86)}\7-Zip\7z.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
    $target = Join-Path $vendor 'tesseract'
    if ($sevenZip) {
        & $sevenZip x $setup "-o$target" -y | Out-Null
        Remove-Item -Recurse -Force (Join-Path $target '$PLUGINSDIR') -ErrorAction SilentlyContinue
    } else {
        Write-Host 'Instalando o Tesseract na pasta vendor (o Windows pode pedir permissão)...'
        Start-Process -FilePath $setup -ArgumentList '/S', "/D=$target" -Wait
    }
    if (-not (Test-Path $tessExe)) { throw 'Não foi possível instalar o Tesseract.' }
}

# 2) Modelos de idioma (melhor qualidade)
$tessdata = Join-Path $vendor 'tesseract\tessdata'
$langs = @{
    'por' = '711DE9DBB8052067';  # prefixo do SHA-256 conferido
    'eng' = '8280AED0782FE272'
}
foreach ($l in $langs.Keys) {
    $f = Join-Path $tessdata "$l.traineddata"
    $ok = (Test-Path $f) -and ((Get-FileHash $f).Hash.StartsWith($langs[$l]))
    if (-not $ok) {
        Write-Host "Baixando modelo de idioma: $l"
        Invoke-WebRequest "https://github.com/tesseract-ocr/tessdata_best/raw/main/$l.traineddata" -OutFile $f -UseBasicParsing
        if (-not (Get-FileHash $f).Hash.StartsWith($langs[$l])) { throw "Modelo $l com conteúdo inesperado." }
    }
}

# 3) Modelo de layout
$models = Join-Path $vendor 'models'
New-Item -ItemType Directory -Force $models | Out-Null
Get-Verified 'https://www.modelscope.cn/models/RapidAI/RapidLayout/resolve/v1.2.0/onnx/pp_doc_layout/pp_doc_layoutv3.onnx' `
    (Join-Path $models 'pp_doc_layoutv3.onnx') '250DBAD1DFB9E4983FAB75E1BF5085CD56EC3F41D5C7D0F8623EC74856E7AA67'

# 4) llama.cpp (Vulkan) para o Surya
$llamaExe = Join-Path $vendor 'llama\llama-server.exe'
if (-not (Test-Path $llamaExe)) {
    $zip = Join-Path $dl 'llama-vulkan.zip'
    Get-Verified 'https://github.com/ggml-org/llama.cpp/releases/download/b11381/llama-b11381-bin-win-vulkan-x64.zip' `
        $zip '517032B9AB2E1C7991A1313FA8C39CBC1A9DA6EBF82944CF34CCF6ADF3D0A37A'
    Expand-Archive $zip -DestinationPath (Join-Path $vendor 'llama') -Force
}

# 5) Dicionário pt-BR (VERO, LibreOffice), versão fixada
$dic = Join-Path $vendor 'dicionario'
New-Item -ItemType Directory -Force $dic | Out-Null
$commit = '5cf23af85b47be6e398add826da1a02175be4238'
$files = @{
    'pt_BR.aff' = '21D8AD2A769A60E17E2B5EA4EF11D4D593A58B9E2A82D642EF82D6A4C5523865';
    'pt_BR.dic' = 'A38BFB26B68ECE2834E79FE83E48D5792652970ACE12DB89D1B9674BF9933183';
    'README_pt_BR.txt' = '9974CE691FDC1FE731717D7A2DC668244405FDC2BF9BF3367EB9B29E85177C88'
}
foreach ($f in $files.Keys) {
    Get-Verified "https://raw.githubusercontent.com/LibreOffice/dictionaries/$commit/pt_BR/$f" (Join-Path $dic $f) $files[$f]
}

# 6) Opcional: modelo do Surya (também pode ser baixado depois, pelo próprio programa)
if ($ComSurya) {
    $sm = Join-Path $vendor 'models\surya'
    New-Item -ItemType Directory -Force $sm | Out-Null
    $hf = 'https://huggingface.co/datalab-to/surya-ocr-2-gguf/resolve/main'
    foreach ($m in @(@('surya-2.gguf', '1F18ABE17B1ED8B4'), @('surya-2-mmproj.gguf', '98C0563673B1657F'))) {
        $dst = Join-Path $sm $m[0]
        if (-not (Test-Path $dst) -or -not (Get-FileHash $dst).Hash.StartsWith($m[1])) {
            Write-Host "Baixando $($m[0])..."
            Invoke-WebRequest "$hf/$($m[0])" -OutFile $dst -UseBasicParsing
            if (-not (Get-FileHash $dst).Hash.StartsWith($m[1])) { throw "Arquivo $($m[0]) com conteúdo inesperado." }
        }
    }
}

Write-Host ''
Write-Host 'Componentes instalados:'
& $tessExe --version 2>&1 | Select-Object -First 1
Get-ChildItem $tessdata -Filter *.traineddata | ForEach-Object { "  idioma: " + $_.BaseName }
"  layout: pp_doc_layoutv3.onnx"
"  llama.cpp: " + (Test-Path $llamaExe)
"  dicionário pt-BR: " + (Test-Path (Join-Path $dic 'pt_BR.dic'))
