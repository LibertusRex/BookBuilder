# BookBuilder

Transforma uma pasta de imagens de páginas (fotos, digitalizações ou capturas de tela de um livro ou documento), ou um PDF de páginas digitalizadas, em dois arquivos editáveis: **DOCX** (Word) e **EPUB 3**.

Tudo acontece **no seu computador**. As imagens e o texto nunca são enviados para a internet, e não há serviço pago. A internet só é usada uma vez, na instalação, para baixar as bibliotecas e os modelos de OCR.

---

## Instalação

### Opção 1 — instalador pronto (recomendada)

Se você recebeu `BookBuilder-Setup-0.1.0.exe`, dê dois cliques e siga as telas. Se recebeu a pasta `BookBuilder` (com `BookBuilder.exe` dentro), copie-a para onde quiser e abra `BookBuilder.exe`. Nos dois casos não é preciso instalar mais nada.

### Opção 2 — a partir do código-fonte

1. Dê dois cliques em **`instalar.bat`**. Ele:
   - instala o gerenciador `uv`, se ainda não estiver no computador;
   - instala o Python 3.12 e as bibliotecas, nas versões fixadas em `uv.lock`;
   - baixa o Tesseract OCR 5.4, os modelos de português e inglês, o modelo de análise de layout, o llama.cpp (para o Surya) e o dicionário pt-BR, conferindo o SHA-256 de cada arquivo. O modelo do Surya (1,4 GB) é baixado pelo próprio programa no primeiro uso, ou com `scripts\instalar_componentes.ps1 -ComSurya`.
2. Depois disso, use **`iniciar.bat`** para abrir o programa.

### Gerar o executável e o instalador

Depois do passo 1 da Opção 2, rode **`gerar_executavel.bat`**:

- o programa completo vai para `dist\BookBuilder\BookBuilder.exe` (funciona sem internet);
- se o [Inno Setup 6](https://jrsoftware.org/isinfo.php) estiver instalado (`winget install JRSoftware.InnoSetup`), o mesmo script também cria `dist\instalador\BookBuilder-Setup-0.1.0.exe`.

---

## Como usar (quatro etapas)

**1. Escolher imagens**
- Clique em *Abrir pasta de imagens…* ou *Abrir PDF…*, ou arraste a pasta ou o PDF para a janela.
- Formatos aceitos: PNG, JPG, JPEG, TIFF (cada quadro de um TIFF multipágina vira uma página), BMP, WebP e PDF (cada página do PDF vira uma página; feito para PDFs de aplicativos de digitalização, como o scanner do iPhone e o Scanner Pro).
- A ordem é natural: "página 2" vem antes de "página 10". Arraste as miniaturas para reordenar.
- Gire páginas ou exclua-as da conversão. **Os arquivos originais nunca são alterados.**
- Escolha o idioma: português, inglês ou documento misto.

**2. Processar**
- Com placa de vídeo dedicada (4 GB ou mais), o **Surya** relê o texto junto com o Tesseract — vem ligado por padrão e pode ser desligado nesta etapa. Na primeira vez, o programa oferece baixar o modelo (1,4 GB, uma única vez). Sem placa dedicada, o Surya fica desligado e tudo segue só com o Tesseract.
- Clique em *Iniciar processamento*. Há progresso por página e tempo restante estimado.
- O programa pede uma pasta para salvar o projeto. Cada página concluída fica gravada, então você pode **cancelar** e depois **retomar** sem refazer o que já foi feito, inclusive fechando o programa no meio.

**3. Revisar**
- À esquerda fica a imagem original com as regiões coloridas por função; à direita, o resultado com as fontes estimadas.
- Clique numa região para:
  - mudar a **função** (corpo, título, cabeçalho, rodapé, número de página, nota de rodapé…) só naquele bloco, na página, num intervalo de páginas ou no documento inteiro;
  - **editar o texto**, com negrito, itálico, sublinhado, sobrescrito e subscrito.
- **Fundo amarelo** = trecho de baixa confiança no OCR. **"[…]"** = texto encoberto na imagem original (por exemplo, por um aviso do celular), que não pode ser recuperado.
- Use o filtro *Mostrar* para ver só as páginas com avisos, as de baixa confiança, as capturas repetidas etc.
- Na aba *Divergências*: os trechos em que Tesseract e Surya leram coisas diferentes e nenhuma regra decidiu. Mostra o recorte da imagem e as duas leituras; escolha com **T** (Tesseract) ou **S** (Surya), marque “aplicar a todas iguais no livro” se quiser, e use **N** para ir à próxima. O filtro *Divergências Tesseract × Surya* lista só essas páginas.
- Na aba *Estilos e fontes*:
  - escolha outra fonte ou tamanho para um estilo inteiro de uma vez;
  - ajuste a **escala** (DPI, tamanho da página ou tamanho do corpo do texto).

**4. Exportar**
- Escolha a pasta, o nome e os formatos, e preencha título, autor, idioma e capa (opcional).
- No DOCX, escolha entre *texto contínuo* e *preservar as quebras das páginas originais*.
- Ao final, um **relatório** é mostrado e salvo ao lado dos arquivos (`<nome>-relatorio.html`).

**DOCX → EPUB (por exemplo, depois de revisar o livro no Word)**
- Na primeira tela, clique em *Converter DOCX em EPUB…* ou arraste o arquivo `.docx` para a janela.
- Escolha onde salvar o EPUB e, se quiser, título, autor e capa (sem título, vale o das propriedades do documento ou o nome do arquivo).
- Os estilos *Título 1–6* viram o sumário e os capítulos; parágrafos com borda viram quadros; listas (inclusive com numeração automática do Word), citações, legendas, notas de rodapé, imagens, tabelas, negrito/itálico e links são mantidos. Cabeçalhos, rodapés e números de página do Word ficam de fora (no EPUB a paginação é a do leitor).
- Pela linha de comando: `python -m bookbuilder.cli livro.docx --epub livro.epub`.

---

## O que o programa faz por baixo

| Etapa | Como |
|---|---|
| OCR | Tesseract 5.4 com os modelos de melhor qualidade (`tessdata_best`) de português e inglês. Títulos em fontes finas ou estreitas passam por um segundo OCR ampliado. |
| Releitura com o Surya (opcional, placa de vídeo) | Surya OCR 2 executado localmente pelo llama.cpp (Vulkan) relê cada bloco de texto; o layout continua sendo do BookBuilder. As duas leituras são combinadas: concordância → aceita (sem amarelo); uma letra de diferença → Tesseract; palavras juntas no Tesseract × separadas no Surya → Surya; §, travessões, romanos, numeração, itálico/negrito → Surya; acentos do Surya só se a palavra existir no livro ou no dicionário pt-BR (VERO/LibreOffice); aspas curvas → Tesseract; outras diferenças → Surya com destaque amarelo. Releituras sem relação com o bloco (alucinação) são descartadas. Marcadores de lista ("a)") vêm do Tesseract; URLs bem formadas e números de processo CNJ/leis válidos (dígito verificador, formato N/AAAA) vencem. O que ainda divergir passa por um **árbitro por recorte**: a região é ampliada 3× e relida pelo Tesseract e pelo Surya; a maioria de votos decide, e só os empates ficam em amarelo. |
| Pré-processamento | Inverte páginas em modo escuro; corrige inclinação e orientação. Detecta e apaga sobreposições da tela (avisos, botões, barras do leitor), marcando o texto encoberto. Remove os sublinhados tracejados de "destaques populares". |
| Páginas fotografadas (PDF de digitalização, fotos de câmera) | A página do PDF é lida na resolução da própria foto e no giro em que o PDF a mostra; todas as páginas são levadas à mesma largura, para as letras terem o mesmo tamanho. A iluminação é igualada canal por canal (sombra da mão ou do celular, luz amarelada): o papel fica branco e o texto na sombra volta a ser legível. As linhas curvadas perto da lombada ou da borda são endireitadas a partir das próprias linhas de texto. Dedos e bordas na foto são retirados como ruído. A detecção de sobreposições do leitor fica desligada (uma sombra nunca apaga texto). As figuras saem com a mesma correção de luz. A camada de texto que o aplicativo de digitalização grava no PDF é ignorada (o OCR é refeito). Os parágrafos do corpo do texto ficam todos no estilo do corpo (mesma fonte e tamanho), justificados, sem recuo lateral e com o mesmo recuo de primeira linha (a foto deixa as medidas de cada parágrafo um pouco diferentes); epígrafes e citações recuadas ficam como estão. O número da página grudado no rodapé ("12 Tal Ben-Shahar") vira número automático. A capa fotografada (primeira página com figura grande ou letras bem maiores que as do livro) fica inteira como imagem, nas cores originais: sozinha na primeira página do DOCX e como capa do EPUB (a menos que outra capa seja escolhida na exportação). |
| Layout | Modelo PP-DocLayout V3 (ONNX, local) para títulos, texto, tabelas, figuras, legendas, cabeçalhos, rodapés e notas. Ordem de leitura por cortes recursivos (respeita duas colunas). |
| Parágrafos | Linhas reunidas em parágrafos pelo recuo e pelo espaçamento. A hifenização no fim da linha é desfeita com cuidado: "crimi-/nalidade" vira "criminalidade", mas "acreditava-/se" e "contra-/ataque" mantêm o hífen. Parágrafos que continuam na página seguinte são unidos. |
| Cabeçalhos e rodapés | Decididos pelo documento inteiro: posição, repetição entre páginas, texto parecido com os números mascarados, alternância par/ímpar e numeração crescente. A posição sozinha nunca basta. |
| Notas de rodapé | Corpo menor no pé da página, com chamada; ligadas à chamada sobrescrita no texto. A chamada que o Tesseract lê como sinais (".?º", "!º") fica com o número que o Surya leu, sobrescrito. |
| Fontes e tamanhos | Trechos da imagem comparados com as mesmas palavras desenhadas nas fontes instaladas no Windows. Tamanho pela altura das letras, convertida em pontos pela escala escolhida. |
| Ruído gráfico | Ícones do livro digital (fone, documento, seta de ampliar, alfinete), molduras dos quadros de podcast/vídeo lidas como letras ("Lone —", "LER"), a interface do leitor e a janela "Go to page…" de capturas feitas durante a navegação, e recortes de imagem que repetem um texto vizinho são retirados. Ficam como "artefato" e podem ser devolvidos na revisão. |
| Sumário e capitulares | O pontilhado entre o título e o número da página, que o OCR lê como letras miúdas, é descartado e cada entrada do sumário vira um parágrafo, todas no mesmo estilo ("Sumário"). No DOCX o número da página fica na margem direita, numa tabulação com pontilhado. No EPUB, que o leitor pagina conforme a letra escolhida, a entrada não leva número de página e sim um link para o capítulo (quando ele está no livro). O número de ordem lido errado ("l.", "4.º") segue a sequência das entradas vizinhas. A letra capitular, maior que o texto, volta para o início da palavra ("M" + "as" → "Mas"). |
| Títulos | Títulos, subtítulos e seções sempre em negrito, também dentro de quadros ("SÍNTESE", "PONTO RELEVANTE PARA DEBATE"). Além do detector de layout, viram título: seção numerada de uma linha, linha curta em fonte maior que a do corpo, artigo sozinho na linha ("Art. 146") e o mesmo texto curto que é título no resto do livro. Os itens de uma SÍNTESE não são títulos. |
| Acertos de texto | Traço entre espaços → travessão "—"; inciso "I" lido como "l", "1" ou "|"; "(inciso), I)" → "(inciso I)"; "§ 1.%" → "§ 1.º"; vírgula ou aspas sem o espaço certo; acento que falta numa palavra que o livro sempre escreve acentuada ("vitima" → "vítima"); palavra repetida pela releitura. |
| DOCX | Estilos semânticos (Título 1–6, Normal, Legenda…). Cabeçalhos e rodapés nas áreas nativas do Word, em letra bem pequena, com campos de número de página e total de páginas. Notas de rodapé nativas. Quadros com borda. Endereços web viram links. Nenhuma caixa de texto flutuante. |
| EPUB 3 | Texto fluido, HTML semântico, sumário navegável, notas com links de ida e volta, endereços web como links. Sem cabeçalhos, rodapés nem números de página impressos. |

---

## Limitações

- **Surya:** precisa de placa de vídeo dedicada; deixa o processamento mais lento (cerca de 6–7 s por página contra ~2 s só com o Tesseract). Seus erros costumam parecer palavras certas — por isso as divergências que não se encaixam nas regras ficam em amarelo. O modelo tem licença AI Pubs OpenRAIL-M modificada (uso pessoal livre; há restrições comerciais).
- **Fontes e tamanhos são estimativas.** Fontes comerciais de livros raramente estão instaladas; o programa usa a mais parecida, informa a substituição e mostra a semelhança. Você pode trocar a fonte de cada estilo.
- **Capturas de tela não têm tamanho físico.** Nelas, o tamanho do corpo do texto (11 pt por padrão) define a escala, e as proporções entre títulos e texto são preservadas. Em digitalizações com DPI real, o DPI é usado.
- **Fotos:** a correção da curvatura é aproximada (pensada para o OCR); páginas muito tremidas, com reflexo de flash ou com dedos sobre o texto não têm como ser recuperadas. Dicas: luz difusa, sem flash, celular paralelo à página e a folha bem aberta perto da lombada.
- **Texto encoberto ou ilegível na imagem não é inventado.** Ele aparece como "[…]" ou é mantido como imagem marcada "ilegível" para você conferir.
- **Diagramas e tabelas sem linhas visíveis** podem ser mantidos como imagem; o texto lido neles vira descrição alternativa.
- **Negrito, itálico e sublinhado** são detectados de forma aproximada.
- **No EPUB**, o aplicativo de leitura pode trocar a fonte e o tamanho escolhidos, e a paginação é a dele.
- **No DOCX**, cabeçalhos e rodapés ficam estruturalmente separados do corpo, mas programas externos podem tratá-los de formas diferentes.
- **Cabeçalhos que variam a cada página** (como a porcentagem de leitura do Kindle) não têm equivalente no Word e são omitidos, com aviso no relatório.

---

## Para desenvolvedores

- **Código:** `src\bookbuilder`, com módulos separados:
  - `ocr\` — pré-processamento e Tesseract;
  - `layout\` — regiões, parágrafos, ordem de leitura, cabeçalhos e rodapés;
  - `typography\` — estilos e fontes;
  - `document.py` — análise do documento inteiro;
  - `review.py` — operações de revisão;
  - `export\` — DOCX, EPUB e relatório;
  - `ui\` — interface Qt.
- **Representação intermediária:** `model.py` (projeto → páginas → blocos → linhas → palavras, com coordenadas, estilos, confiança e função).
- **Linha de comando:** `uv run python -m bookbuilder.cli <pasta> --projeto <saida.bookbuilder> --docx x.docx --epub x.epub` (acrescente `--sem-surya` para usar só o Tesseract).
- **Fusão Tesseract × Surya:** `ocr\surya.py` (placa de vídeo, servidor e releitura dos blocos) e `layout\fusion.py` (regras de combinação).
- **Testes:** `uv run pytest`. Incluem um livro sintético com acentos, títulos em vários tamanhos, cabeçalhos alternados, rodapé numerado, nota de rodapé, duas colunas, página inclinada, nomes fora de ordem e trecho ilegível.
- **Exemplo:** `uv run python scripts\gerar_exemplo_sintetico.py` gera esse livro de exemplo em `build\`.
