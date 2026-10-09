# BookBuilder

Turns a folder of page images (photos, scans or screenshots of a book or document), or a PDF of scanned pages, into two editable files: **DOCX** (Word) and **EPUB 3**.

Everything happens **on your computer**. Images and text are never sent to the internet, and there is no paid service. The internet is used only once, during installation, to download libraries and OCR models.

---

## Installation

### Option 1 — ready-made installer (recommended)

If you received `BookBuilder-Setup-0.1.0.exe`, double-click it and follow the screens. If you received the `BookBuilder` folder (with `BookBuilder.exe` inside), copy it wherever you like and open `BookBuilder.exe`. In both cases nothing else needs to be installed.

### Option 2 — from source

1. Double-click **`instalar.bat`**. It:
   - installs the `uv` package manager, if it is not on the computer yet;
   - installs Python 3.12 and the libraries, at the versions pinned in `uv.lock`;
   - downloads Tesseract OCR 5.4, the Portuguese and English language models, the layout analysis model, llama.cpp (for Surya) and the pt-BR dictionary, checking the SHA-256 of each file. The Surya model (1.4 GB) is downloaded by the program itself on first use, or with `scripts\instalar_componentes.ps1 -ComSurya`.
2. After that, use **`iniciar.bat`** to open the program.

### Building the executable and the installer

After step 1 of Option 2, run **`gerar_executavel.bat`**:

- the complete program goes to `dist\BookBuilder\BookBuilder.exe` (it works without internet);
- if [Inno Setup 6](https://jrsoftware.org/isinfo.php) is installed (`winget install JRSoftware.InnoSetup`), the same script also creates `dist\instalador\BookBuilder-Setup-0.1.0.exe`.

---

## How to use it (four steps)

**1. Choose images**
- Click *Open image folder…* or *Open PDF…*, or drag the folder or the PDF into the window.
- Accepted formats: PNG, JPG, JPEG, TIFF (each frame of a multipage TIFF becomes a page), BMP, WebP and PDF (each PDF page becomes a page; made for PDFs from scanning apps, such as the iPhone scanner and Scanner Pro).
- The order is natural: "page 2" comes before "page 10". Drag the thumbnails to reorder them.
- Rotate pages or exclude them from the conversion. **The original files are never changed.**
- Choose the language: Portuguese, English or mixed.

**2. Process**
- With a dedicated graphics card (4 GB or more), **Surya** re-reads the text together with Tesseract. It is on by default and can be turned off at this step. The first time, the program offers to download the model (1.4 GB, once). Without a dedicated card, Surya stays off and everything runs on Tesseract alone.
- Click *Start processing*. Progress is shown per page, with an estimated time remaining.
- The program asks for a folder to save the project in. Each finished page is saved, so you can **cancel** and later **resume** without redoing what is done, even if you close the program in the middle.

**3. Review**
- On the left is the original image, with the regions coloured by their role; on the right, the result with the estimated fonts.
- Click a region to:
  - change its **role** (body, title, header, footer, page number, footnote…) for that block only, for a page, for a range of pages, or for the whole document;
  - **edit the text**, with bold, italic, underline, superscript and subscript.
- **Yellow background** = low-confidence OCR text. **"[…]"** = text covered in the original image (for example, by a phone notification), which cannot be recovered.
- Use the *Show* filter to see only the pages with warnings, the low-confidence ones, repeated captures, and so on.
- In the *Divergences* tab: passages where Tesseract and Surya read different things and no rule decided. It shows the image crop and both readings; choose with **T** (Tesseract) or **S** (Surya), tick "apply to all identical cases in the book" if you want, and press **N** for the next one. The *Tesseract × Surya divergences* filter lists only those pages.
- In the *Styles and fonts* tab:
  - choose another font or size for a whole style at once;
  - adjust the **scale** (DPI, page size or body text size).

**4. Export**
- Choose the folder, the name and the formats, and fill in title, author, language and an optional cover.
- For DOCX, choose between *continuous text* and *keep the original page breaks*.
- At the end, a **report** is shown and saved next to the files (`<name>-relatorio.html`).

**DOCX → EPUB (for example, after reviewing the book in Word)**
- On the first screen, click *Convert DOCX to EPUB…* or drag the `.docx` file into the window.
- Choose where to save the EPUB and, if you want, title, author and cover (without a title, the document properties or the file name is used).
- The *Heading 1–6* styles become the table of contents and the chapters; bordered paragraphs become boxes; lists (including Word's automatic numbering), quotes, captions, footnotes, images, tables, bold/italic and links are kept. Word headers, footers and page numbers are left out (in EPUB, pagination is the reader's).
- From the command line: `python -m bookbuilder.cli livro.docx --epub livro.epub`.

---

## What the program does underneath

| Stage | How |
|---|---|
| OCR | Tesseract 5.4 with the best-quality models (`tessdata_best`) for Portuguese and English. Titles in thin or narrow fonts get a second, enlarged OCR pass. |
| Re-reading with Surya (optional, graphics card) | Surya OCR 2, run locally through llama.cpp (Vulkan), re-reads each text block; the layout remains BookBuilder's. The two readings are merged: agreement → accepted (no yellow); a one-letter difference → Tesseract; words joined in Tesseract but separate in Surya → Surya; §, dashes, roman numerals, numbering, italic/bold → Surya; Surya's accents only when the word exists in the book or in the pt-BR dictionary (VERO/LibreOffice); curly quotes → Tesseract; other differences → Surya with yellow highlight. Re-readings unrelated to the block (hallucinations) are discarded. List markers ("a)") come from Tesseract; well-formed URLs and valid CNJ case numbers and law numbers (check digit, N/AAAA format) win. What still disagrees goes to a **crop arbiter**: the region is enlarged 3× and re-read by Tesseract and Surya; the majority vote decides, and only ties stay yellow. |
| Pre-processing | Inverts dark-mode pages; corrects skew and orientation. Detects and removes screen overlays (notices, buttons, reader bars), marking the text they cover. Removes the dashed underlines of "popular highlights". |
| Photographed pages (scanned PDF, camera photos) | The PDF page is read at the resolution of the photo itself and in the orientation the PDF shows it; all pages are brought to the same width, so letters have the same size. Lighting is evened out channel by channel (hand or phone shadow, yellowish light): the paper becomes white and text in the shadow becomes legible again. Curved lines near the spine or the edge are straightened using the text lines themselves. Fingers and edges in the photo are removed as noise. Reader overlay detection is off for these (a shadow never erases text). Figures get the same light correction. The text layer that the scanning app writes into the PDF is ignored (OCR is redone). Body paragraphs all use the body style (same font and size), justified, with no side indent and the same first-line indent (the photo makes each paragraph's measurements slightly different); indented epigraphs and quotes are kept as they are. In DOCX, the table of contents and each chapter start on a new page. A word split at the end of a page whose remainder does not appear on the next photo ("felicida-" | "— primeiro…") joins the paragraphs and is highlighted in yellow, without completing the word. A page number stuck to the footer ("12 Tal Ben-Shahar") becomes an automatic number. A photographed cover (first page with a large figure or letters much bigger than the book's) stays whole as an image, in its original colours: alone on the first DOCX page and as the EPUB cover (unless another cover is chosen at export). |
| Layout | PP-DocLayout V3 model (ONNX, local) for titles, text, tables, figures, captions, headers, footers and notes. Reading order by recursive cuts (respects two columns). |
| Paragraphs | Lines are joined into paragraphs by indent and spacing. Hyphenation at the end of a line is undone carefully: "crimi-/nalidade" becomes "criminalidade", but "acreditava-/se" and "contra-/ataque" keep the hyphen. Paragraphs that continue on the next page are joined. |
| Headers and footers | Decided for the whole document: position, repetition across pages, text similar to masked numbers, odd/even alternation and increasing numbering. Position alone is never enough. |
| Footnotes | Smaller text at the foot of the page, with a call mark; linked to the superscript call in the text. A call the Tesseract reads as symbols (".?º", "!º") gets the number that Surya read, as superscript. |
| Fonts and sizes | Image crops are compared with the same words drawn in the fonts installed on Windows. Size is measured from letter height, converted to points using the chosen scale. |
| Graphic noise | E-book interface icons (headphones, document, zoom arrow, pin), podcast/video frames read as letters ("Lone —", "LER"), the reader interface and the "Go to page…" window of captures taken while browsing, and image crops that repeat a neighbouring text are removed. They are kept as "artefact" and can be restored in the review. |
| Table of contents and drop caps | The dotted leader between the title and the page number, which the OCR reads as small letters, is dropped and each entry becomes a paragraph, all in the same style ("Sumário"). In DOCX, the page number sits at the right margin, in a dot-leader tab. In EPUB, where the reader paginates by the chosen font size, the entry has no page number but a link to the chapter (when it is in the book). A wrongly read entry number ("l.", "4.º") follows the sequence of the neighbouring entries. A drop cap, larger than the text, returns to the start of its word ("M" + "as" → "Mas"). |
| Titles | Titles, subtitles and section headings are always bold, including inside boxes ("SÍNTESE", "PONTO RELEVANTE PARA DEBATE"). Besides the layout detector, these become titles: a numbered section on one line, a short line in a larger font than the body, an article alone on its line ("Art. 146") and the same short text that is a title elsewhere in the book. The items of a SÍNTESE box are not titles. |
| Text corrections | Dash between spaces → em dash "—"; roman numeral "I" read as "l", "1" or "|"; "(inciso), I)" → "(inciso I)"; "§ 1.%" → "§ 1.º"; comma or quotes with the wrong spacing; a missing accent on a word the book always writes with one ("vitima" → "vítima"); a word repeated by the re-reading. |
| DOCX | Semantic styles (Heading 1–6, Normal, Caption…). Headers and footers in Word's native areas, in very small type, with page number and total page fields. Native footnotes. Bordered boxes. Web addresses become links. No floating text boxes. |
| EPUB 3 | Reflowable text, semantic HTML, navigable table of contents, notes with back-links, web addresses as links. No printed headers, footers or page numbers. |

---

## Limitations

- **Surya:** needs a dedicated graphics card; makes processing slower (about 6–7 s per page against about 2 s with Tesseract alone). Its errors often look like correct words — that is why divergences that do not fit the rules stay yellow. The model has a modified OpenRAIL-M licence (free personal use; commercial use is restricted).
- **Fonts and sizes are estimates.** Commercial book fonts are rarely installed; the program uses the closest one, reports the substitution and shows the similarity. You can change the font of each style.
- **Screenshots have no physical size.** For them, the body text size (11 pt by default) sets the scale, and the proportions between titles and text are kept. For scans with a real DPI, that DPI is used.
- **Photos:** curvature correction is approximate (aimed at the OCR); very blurry pages, pages with flash glare or with fingers over the text cannot be recovered. Tips: diffuse light, no flash, phone parallel to the page, and the book open flat near the spine.
- **Covered or illegible text in the image is not invented.** It appears as "[…]" or is kept as an image marked "illegible" for you to check.
- **Diagrams and tables without visible lines** may be kept as an image; the text read from them becomes alternative text.
- **Bold, italic and underline** are detected approximately.
- **In EPUB**, the reading app may replace the chosen font and size, and pagination is its own.
- **In DOCX**, headers and footers are structurally separate from the body, but external programs may treat them differently.
- **Headers that change on every page** (such as the Kindle reading percentage) have no equivalent in Word and are omitted, with a warning in the report.

---

## For developers

- **Code:** `src\bookbuilder`, with separate modules:
  - `ocr\` — pre-processing and Tesseract;
  - `layout\` — regions, paragraphs, reading order, headers and footers;
  - `typography\` — styles and fonts;
  - `document.py` — whole-document analysis;
  - `review.py` — review operations;
  - `export\` — DOCX, EPUB and report;
  - `ui\` — Qt interface.
- **Intermediate representation:** `model.py` (project → pages → blocks → lines → words, with coordinates, styles, confidence and role).
- **Command line:** `uv run python -m bookbuilder.cli <folder> --projeto <output.bookbuilder> --docx x.docx --epub x.epub` (add `--sem-surya` to use Tesseract only).
- **Tesseract × Surya merge:** `ocr\surya.py` (graphics card, server and block re-reading) and `layout\fusion.py` (merge rules).
- **Tests:** `uv run pytest`. They include a synthetic book with accents, titles in several sizes, alternating headers, numbered footers, a footnote, two columns, a tilted page, out-of-order names and an illegible passage.
- **Example:** `uv run python scripts\gerar_exemplo_sintetico.py` generates that example book in `build\`.

---

## Interface guide (Portuguese labels, in English)

The program's interface is in Portuguese. The tables below translate every button, menu, tab, label and option, and say what each one does. Texts in quotes are exactly as they appear on screen.

### Menus and window

| On screen (Portuguese) | English | What it does |
|---|---|---|
| **Arquivo** | File | Project menu. |
| Nova conversão a partir de uma pasta… | New conversion from a folder… | Starts a new project from a folder of images or a PDF. |
| Abrir projeto… | Open project… | Opens a saved `.bookbuilder` project to continue it. |
| Salvar projeto | Save project | Saves the project now. |
| Salvar projeto como… | Save project as… | Saves the project under another name or folder. |
| Projetos recentes | Recent projects | Projects opened recently. |
| Sair | Quit | Closes the program (asks before discarding unsaved work). |
| **Ajuda** | Help | Help menu. |
| Sobre e limitações | About and limitations | Shows the program's information and its limits. |
| "1. Escolher imagens", "2. Processar", "3. Revisar", "4. Exportar" | "1. Choose images", "2. Process", "3. Review", "4. Export" | The four steps shown at the top, in order. |
| "Projeto ainda não salvo" / "Projeto: …" | "Project not saved yet" / "Project: …" | Name of the current project, shown in the header. |
| Componentes ausentes | Missing components | Warning that an OCR component is not installed (see the installation section). |

### Dialog boxes

| On screen (Portuguese) | English | When it appears |
|---|---|---|
| Projeto não salvo / Descartar o projeto atual, que ainda não foi salvo? | Project not saved / Discard the current project, which has not been saved yet? | Before opening or creating another project. |
| Sair / O projeto não foi salvo. Salvar antes de sair? | Quit / The project has not been saved. Save before quitting? | When closing with unsaved work. |
| Processamento em andamento / O processamento será interrompido; as páginas concluídas ficam salvas. Sair? | Processing in progress / Processing will stop; finished pages are kept. Quit? | When closing during processing. |
| Abrir projeto / Esta pasta não contém um projeto do BookBuilder. | Open project / This folder does not contain a BookBuilder project. | When the chosen folder is not a project. |
| Abrir / Não foi possível ler "…" | Open / Could not read "…" | The folder could not be read. |
| Nenhuma imagem (PNG, JPG, TIFF, BMP, WebP) ou PDF encontrado. | No image (PNG, JPG, TIFF, BMP, WebP) or PDF found. | The folder has no supported files. |
| Projeto / "N imagens mudaram desde o processamento e serão processadas de novo." | Project / "N images changed since processing and will be processed again." | Images changed on disk since the last run. |
| Escolha a pasta com as imagens das páginas | Choose the folder with the page images | Folder picker title. |
| Escolha o PDF com as páginas digitalizadas | Choose the PDF with the scanned pages | PDF picker title. |
| Onde salvar o projeto (para poder continuar depois)? | Where to save the project (so you can continue later)? | Asks for the project folder before processing. |
| Falha no processamento | Processing failed | A page failed; the message gives the reason. |
| Substituir arquivos? | Replace files? | The export would overwrite existing files. |
| Pronto! Arquivos gerados: | Done! Files created: | Shown after the export. |
| Falha na exportação | Export failed | The export did not finish. |

### Step 1 — Choose images

| On screen (Portuguese) | English | What it does |
|---|---|---|
| Abrir pasta de imagens… | Open image folder… | Chooses a folder with page images. |
| Abrir PDF… | Open PDF… | Chooses a PDF of scanned pages. |
| Converter DOCX em EPUB… | Convert DOCX to EPUB… | Opens the DOCX → EPUB converter (see its section below). |
| Nenhuma pasta aberta. Você também pode arrastar uma pasta ou um PDF para esta janela. | No folder open. You can also drag a folder or a PDF into this window. | Empty-state message. |
| Pasta: / PDF: | Folder: / PDF: | Source currently open. |
| Idioma do texto: | Text language: | Language of the book. |
| Português / Inglês / Português e inglês (documento misto) | Portuguese / English / Portuguese and English (mixed document) | Language options. |
| Dica: arraste as miniaturas para mudar a ordem. As imagens originais nunca são alteradas. | Tip: drag the thumbnails to change the order. The original images are never changed. | Reminder. |
| Girar à esquerda / Girar à direita (↺ / ↻) | Rotate left / Rotate right | Turns the selected page. |
| Excluir / incluir na conversão | Exclude / include in the conversion | Leaves a page out of (or back into) the conversion. |
| Restaurar ordem natural | Restore natural order | Puts the pages back in the order of their names (page 2 before page 10). |
| EXCLUÍDA | EXCLUDED | Label on pages left out. |
| N páginas — N serão convertidas | N pages — N will be converted | Count of pages in the conversion. |
| Próximo: processar → | Next: process → | Goes to step 2. |

### Step 2 — Process

| On screen (Portuguese) | English | What it does |
|---|---|---|
| Iniciar processamento | Start processing | Runs the OCR and the analysis on all pages. |
| Retomar processamento | Resume processing | Continues a stopped run, without redoing finished pages. |
| Cancelar | Cancel | Stops after the pages in progress finish; finished pages are saved. |
| Cancelando… as páginas em andamento terminam e ficam salvas. | Cancelling… pages in progress finish and are saved. | Message while cancelling. |
| Refazer só a análise do documento | Redo only the document analysis | Repeats the layout analysis without repeating the OCR. |
| Processar de novo também as páginas já concluídas | Process again the pages already finished too | Redoes every page, not only the pending ones. |
| Processos em paralelo: | Parallel processes: | How many pages are processed at the same time. |
| Releitura com o Surya (placa de vídeo) | Re-reading with Surya (graphics card) | Group for the optional second OCR. |
| Reler o texto com o Surya, combinando com o Tesseract (recomendado) | Re-read the text with Surya, combining with Tesseract (recommended) | Turns Surya on. |
| Baixar o modelo do Surya (1,4 GB)… | Download the Surya model (1.4 GB)… | Downloads the Surya model. |
| Download / Baixando: N de M MB | Download / Downloading: N of M MB | Download progress. |
| Modelo do Surya instalado. | Surya model installed. | Confirmation after the download. |
| Surya desligado / Surya não usado | Surya off / Surya not used | Explains why Surya did not run (see the reason shown). |
| Tesseract N/M · Surya N/M | Tesseract N/M · Surya N/M | Pages finished by each OCR. |
| Páginas processadas: | Pages processed: | List of pages and their status. |
| ✔ / ✖ | ✔ / ✖ | Page finished / page failed. |
| decorrido / restante ≈ | elapsed / remaining ≈ | Time already spent and estimated time left. |
| Projeto salvo em: | Project saved in: | Where the project is stored. |
| Próximo: revisar → | Next: review → | Goes to step 3. |

### Step 3 — Review

| On screen (Portuguese) | English | What it does |
|---|---|---|
| Mostrar: | Show: | Filter for the page list. |
| Todas as páginas | All pages | No filter. |
| Com algum aviso | With any warning | Pages with at least one warning. |
| Baixa confiança / ilegível | Low confidence / illegible | Pages with low-confidence or unreadable text. |
| Texto encoberto | Covered text | Pages where text is hidden in the image (shown as "[…]"). |
| Cabeçalho/rodapé a conferir | Header/footer to check | Pages where a repeated header or footer needs checking. |
| Com notas de rodapé | With footnotes | Pages that have footnotes. |
| Divergências Tesseract × Surya | Tesseract × Surya divergences | Pages where the two OCRs disagree. |
| Capturas repetidas | Repeated captures | Screenshots that repeat the previous page. |
| Com falha | Failed | Pages that could not be processed. |
| Excluídas | Excluded | Pages left out of the conversion. |
| Mostrar regiões | Show regions | Colours the layout regions over the image. |
| Imagem original (clique numa região): | Original image (click a region): | The page image, where each region can be clicked. |
| Resultado (prévia com as fontes estimadas): | Result (preview with the estimated fonts): | Preview of the text as it will be exported. |
| Bloco / Estilo / Estilos e fontes | Block / Style / Styles and fonts | The three tabs of the review. |
| Função: / Função do bloco | Role: / Block role | What the block is (see the list of roles below). |
| Aplicar função | Apply role | Applies the chosen role. |
| Aplicar a: | Apply to: | Scope of the change (see the list of scopes below). |
| Intervalo de páginas: / de / até | Page range: / from / to | Pages used when the scope is a range. |
| Nível do título: | Heading level: | Heading level (1 to 6) of a title block. |
| Texto do bloco | Block text | Text box to edit the block's text. |
| Salvar texto | Save text | Saves the edited text. |
| Negrito / Itálico | Bold / Italic | Format of the selected text. |
| Divergências | Divergences | Tab with the passages where Tesseract and Surya disagree. |
| T: … \| S: … | T: … \| S: … | Tesseract reading and Surya reading of a divergence. |
| Usar Tesseract (T) / Usar Surya (S) | Use Tesseract (T) / Use Surya (S) | Chooses one of the two readings. |
| Aplicar a todas as divergências iguais no livro | Apply to all identical divergences in the book | Repeats the choice for every identical passage. |
| Próxima divergência no livro ▶ (N) | Next divergence in the book ▶ (N) | Goes to the next open divergence. |
| Divergências desta página: | Divergences on this page: | Divergences of the current page. |
| Recorte da imagem original: | Crop of the original image: | Image crop around the divergence. |
| Avisos da página: / Avisos do bloco: | Page warnings: / Block warnings: | Warnings about the page or the block. |
| Confiança média do OCR: N% / confiança N% | Average OCR confidence: N% / confidence N% | OCR confidence. |
| Reanalisar documento (mantém suas correções) | Re-analyse document (keeps your corrections) | Repeats the analysis but keeps the edits made by hand. |
| Excluir esta página da conversão | Exclude this page from the conversion | Leaves the page out. |
| Processar esta página de novo | Process this page again | Redoes the OCR of the page. |
| Incluir mesmo sendo captura repetida | Include even though it is a repeated capture | Keeps a repeated screenshot in the export. |
| Ajustar o estilo selecionado (vale para todos os blocos dele) | Adjust the selected style (applies to all its blocks) | Changes the font or size of a whole style. |
| Estilo: / Fonte: / Tamanho: | Style: / Font: / Size: | Style, font and size of the selected style. |
| Fonte estimada | Estimated font | Font the program estimated from the image. |
| Escolher… / Ajustar / Voltar à estimativa | Choose… / Adjust / Return to the estimate | Changes the font, applies the size, or undoes the change. |
| Escala tipográfica (pixels → pontos) | Typographic scale (pixels → points) | How image pixels become points. |
| Modo: | Mode: | How the scale is computed (see the list below). |
| Automático / Pelo DPI / Pelo tamanho da página / Pelo tamanho do corpo do texto | Automatic / By DPI / By page size / By body text size | Scale modes. |
| DPI das imagens: | Image DPI: | Resolution of the images (the "do arquivo" option uses the file's own value). |
| Tamanho da página: / Largura da página: | Page size: / Page width: | Physical size of the page. |
| A4 / A5 / Carta / Ofício / 14 × 21 cm / 16 × 23 cm / 17 × 24 cm | A4 / A5 / Letter / Legal / 14 × 21 cm / 16 × 23 cm / 17 × 24 cm | Standard page sizes. |
| Corpo do texto: | Body text: | Size of the body text (used to set the scale). |
| Recalcular tamanhos | Recalculate sizes | Recomputes the estimated sizes. |
| Página em modo escuro (invertida para o OCR) | Dark-mode page (inverted for the OCR) | The page was light text on a dark background and was inverted. |
| Página com N colunas | Page with N columns | Layout note. |
| Número impresso: N | Printed number: N | The page number printed in the book. |
| Status: | Status: | Processing state of the page. |
| Situação / Tamanho / Semelhança | Status / Size / Similarity | Columns of the font table. |
| fonte original não identificada com certeza | original font not identified with certainty | The estimated font is only the closest installed one. |
| NÃO INSTALADA | NOT INSTALLED | The chosen font is not installed on this computer. |
| escolhida por você | chosen by you | The font was picked by hand. |
| (sem posição na imagem) | (no position in the image) | Divergence without a location on the image. |
| Sobreposição | Overlay | Screen overlay (notice, button) removed from the page. |
| Erro: | Error: | Reason a page failed. |
| “[…]” marca texto encoberto na imagem original. | "[…]" marks text covered in the original image. | Explanation shown at the bottom. |
| Trechos com fundo amarelo têm baixa confiança no OCR. | Passages with yellow background have low OCR confidence. | Explanation shown at the bottom. |
| Próximo: exportar → | Next: export → | Goes to step 4. |

**Roles of a block (Função):**

| Portuguese | English | Meaning |
|---|---|---|
| Corpo do texto | Body text | Normal paragraph text. |
| Título | Title | Heading (with level 1 to 6). |
| Cabeçalho | Header | Text repeated at the top of the pages. |
| Rodapé | Footer | Text repeated at the bottom of the pages. |
| Número de página | Page number | Printed page number. |
| Nota de rodapé | Footnote | Note at the foot of the page. |
| Legenda | Caption | Caption of an image or table. |
| Item de lista | List item | Item of a list. |
| Citação | Quote | Quoted text. |
| Tabela | Table | Table. |
| Imagem | Image | Figure kept as an image. |
| Ignorar (artefato) | Ignore (artefact) | Removed from the export; can be restored. |

**Scopes of a change (Aplicar a):**

| Portuguese | English |
|---|---|
| Só este bloco | Only this block |
| Blocos equivalentes nesta página | Equivalent blocks on this page |
| Blocos equivalentes no intervalo de páginas | Equivalent blocks in the page range |
| Blocos equivalentes no documento inteiro | Equivalent blocks in the whole document |

**Warnings on a page (flags):**

| Portuguese | English |
|---|---|
| trechos de baixa confiança no OCR | low-confidence passages in the OCR |
| texto encoberto por sobreposição (marcado “[…]”) | text covered by an overlay (marked "[…]") |
| sobreposição da tela (aviso, botões) removida | screen overlay (notice, buttons) removed |
| trecho ilegível | illegible passage |
| tabela não reconhecida com segurança (inserida como imagem) | table not recognised with confidence (inserted as an image) |
| nota de rodapé sem chamada localizada | footnote without a call mark found |
| texto de margem não repetido: conferir se é cabeçalho/rodapé | margin text not repeated: check whether it is a header/footer |
| bloco de margem devolvido ao conteúdo | margin block returned to the content |
| inclinação corrigida | skew corrected |
| orientação corrigida | orientation corrected |
| página sem texto reconhecido | page with no text recognised |
| falha: … | failed: … |

### Step 4 — Export

| On screen (Portuguese) | English | What it does |
|---|---|---|
| Pasta de destino: | Destination folder: | Where the files are saved. |
| Abrir pasta de destino | Open destination folder | Opens that folder. |
| Nome dos arquivos: | File names: | Base name for the files. |
| Gerar: | Generate: | Which formats to create. |
| DOCX (Word) / EPUB 3 | DOCX (Word) / EPUB 3 | The two formats. |
| Dados do livro (usados no EPUB e nas propriedades do DOCX) | Book details (used in the EPUB and in the DOCX properties) | Group with the metadata. |
| Título: / Autor: / Editora: / Idioma: | Title: / Author: / Publisher: / Language: | Metadata fields. |
| Português (Brasil) / Português (Portugal) / Inglês / Espanhol | Portuguese (Brazil) / Portuguese (Portugal) / English / Spanish | Language options. |
| Capa: / Escolher imagem… / Imagem da capa | Cover: / Choose image… / Cover image | Optional cover image (JPG or PNG). |
| Opções do DOCX | DOCX options | Group of Word options. |
| Texto contínuo (recomendado para edição) | Continuous text (recommended for editing) | Ignores the original page breaks. |
| Preservar as quebras das páginas originais | Keep the original page breaks | Starts each page where it was in the book. |
| Cabeçalhos e rodapés nas áreas próprias do Word (fora do corpo do texto) | Headers and footers in Word's own areas (outside the body text) | Keeps headers and footers separate. |
| Destacar em amarelo os trechos de baixa confiança | Highlight low-confidence passages in yellow | Marks uncertain text in yellow. |
| Opções do EPUB | EPUB options | Group of EPUB options. |
| Marcar trechos de baixa confiança | Mark low-confidence passages | Marks uncertain text in the EPUB. |
| Relatório: | Report: | Box on the right where the final report is shown (it is also saved as an HTML file next to the exported files). |
| opcional | optional | Field can be left empty. |
| Exportando… | Exporting… | Export in progress. |
| Exportar | Export | Starts the export. |
| Marque DOCX, EPUB ou ambos. | Select DOCX, EPUB or both. | Nothing was selected. |
| Escolha uma pasta de destino existente. | Choose an existing destination folder. | The folder does not exist. |
| O nome não pode conter os caracteres … | The name cannot contain the characters … | Invalid characters in the file name. |
| Processe as páginas (etapa 2) antes de exportar. | Process the pages (step 2) before exporting. | Export needs processed pages. |

### DOCX → EPUB converter

| On screen (Portuguese) | English | What it does |
|---|---|---|
| Converter DOCX em EPUB | Convert DOCX to EPUB | Dialog title. |
| Documento DOCX: | DOCX document: | Word file to convert. |
| Escolher… | Choose… | Opens the file picker. |
| Salvar EPUB em: | Save EPUB in: | Where to save the EPUB. |
| Título: / Autor: / Capa (imagem): | Title: / Author: / Cover (image): | Optional metadata; without a title, the document's properties or the file name is used. |
| Converter | Convert | Starts the conversion. |
| Convertendo… (um livro grande pode levar um ou dois minutos) | Converting… (a large book can take a minute or two) | Conversion in progress. |
| EPUB gravado em: | EPUB saved in: | Confirmation with the path. |
| Verificação do EPUB: sem problemas. | EPUB check: no problems. | Result of the EPUB validation. |
| Parágrafos / títulos / notas de rodapé / imagens / tabelas | Paragraphs / headings / footnotes / images / tables | Counts shown after the conversion. |
| Não foi possível converter | Could not convert | The conversion failed; the reason follows. |
| Abrir pasta / Fechar | Open folder / Close | Buttons after the conversion. |

### Languages and file types

| On screen (Portuguese) | English |
|---|---|
| Português / Inglês | Portuguese / English |
| Documentos do Word (*.docx) / EPUB (*.epub) / PDF (*.pdf) | Word documents (*.docx) / EPUB (*.epub) / PDF (*.pdf) |
| Imagens (*.jpg *.jpeg *.png) | Images (*.jpg *.jpeg *.png) |
| Projeto BookBuilder (*.bookbuilder) | BookBuilder project (*.bookbuilder) |
| PDF de páginas digitalizadas (por exemplo, do scanner do iPhone ou do Scanner Pro) | Scanned-pages PDF (for example, from the iPhone scanner or Scanner Pro) |

*Texts that appear inside the generated documents (the report, the book content, and the messages in the Word and EPUB files) are not translated: the book keeps its original language.*
