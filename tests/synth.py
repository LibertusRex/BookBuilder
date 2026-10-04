"""Gerador de páginas sintéticas para os testes.

Cria um pequeno "livro" de 7 páginas em PNG (300 DPI, A5) com: acentos;
títulos e corpo em tamanhos diferentes; cabeçalhos repetidos e alternados
(par/ímpar); rodapé com número de página; nota de rodapé com chamada
sobrescrita; texto em duas colunas; página inclinada; nomes de arquivo fora
de ordem; um trecho parcialmente ilegível.

Também devolve o "gabarito" (textos esperados) usado nas verificações.
"""

from __future__ import annotations

import os
import random
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

DPI = 300
W, H = 1748, 2480  # A5 a 300 DPI
MARGIN_X = 190
TOP = 300
FONTS = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"

BOOK = "O LIVRO DOS TESTES"
CHAPTER = "Capítulo 1 — Introdução"

PARAS = [
    "A administração pública deve observar os princípios da legalidade, impessoalidade, moralidade, "
    "publicidade e eficiência. Essa orientação não é apenas uma recomendação: trata-se de exigência "
    "constitucional que alcança todos os órgãos e entidades.",
    "Na prática, a aplicação desses princípios exige atenção às circunstâncias concretas de cada caso, "
    "à motivação dos atos e à proporcionalidade das decisões tomadas pelo gestor público.",
    "Também se deve considerar a participação da sociedade, que acompanha a execução das políticas "
    "públicas e pode provocar os órgãos de controle quando identificar irregularidades.",
    "As três dimensões do controle — interno, externo e social — complementam-se e permitem corrigir "
    "falhas antes que elas causem prejuízos irreparáveis ao patrimônio comum.",
    "Por fim, convém lembrar que a interpretação das normas deve buscar a solução mais coerente com a "
    "finalidade da lei, evitando conclusões que levem a resultados injustos ou contraditórios.",
]
NOTE_TEXT = "Sobre o tema, ver a doutrina citada na introdução desta obra."
COL_LEFT = ("Na primeira coluna, o texto apresenta a origem histórica do instituto e explica como ele foi "
            "incorporado ao ordenamento jurídico brasileiro ao longo do século passado.")
COL_RIGHT = ("Na segunda coluna, o texto descreve a situação atual, com as mudanças trazidas pela legislação "
             "recente e as principais controvérsias ainda em debate nos tribunais.")


def font(name: str, size_pt: float) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / name), int(round(size_pt * DPI / 72)))


def wrap(draw: ImageDraw.ImageDraw, text: str, f, width: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=f) <= width:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def draw_paragraph(d: ImageDraw.ImageDraw, text: str, f, x: int, y: int, width: int, indent: int = 70,
                   leading: float = 1.45, justify: bool = True) -> int:
    lines = wrap(d, text, f, width - indent)
    size = f.size
    for i, ln in enumerate(lines):
        lx = x + (indent if i == 0 else 0)
        avail = width - (indent if i == 0 else 0)
        words = ln.split()
        if justify and i < len(lines) - 1 and len(words) > 1:
            total = sum(d.textlength(w, font=f) for w in words)
            gap = (avail - total) / (len(words) - 1)
            cx = lx
            for w in words:
                d.text((cx, y), w, font=f, fill=0)
                cx += d.textlength(w, font=f) + gap
        else:
            d.text((lx, y), ln, font=f, fill=0)
        y += int(size * leading)
    return y


@dataclass
class Synth:
    folder: Path
    files: list[str] = field(default_factory=list)  # na ordem correta de leitura
    running: list[str] = field(default_factory=list)
    body_snippets: list[str] = field(default_factory=list)
    note_text: str = NOTE_TEXT


def make_book(folder: str | os.PathLike) -> Synth:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    body_f = font("georgia.ttf", 11)
    title_f = font("georgiab.ttf", 22)
    sub_f = font("arialbd.ttf", 14)
    head_f = font("arial.ttf", 8)
    note_f = font("georgia.ttf", 8.5)
    sup_f = font("georgia.ttf", 7)
    syn = Synth(folder, running=[BOOK, CHAPTER])
    pages: list[Image.Image] = []
    width = W - 2 * MARGIN_X

    def new_page(num: int, first: bool = False) -> tuple[Image.Image, ImageDraw.ImageDraw]:
        im = Image.new("L", (W, H), 255)
        d = ImageDraw.Draw(im)
        if not first:
            head = BOOK if num % 2 == 0 else CHAPTER  # alternado: par = livro, ímpar = capítulo
            tw = d.textlength(head, font=head_f)
            hx = MARGIN_X if num % 2 == 0 else W - MARGIN_X - tw
            d.text((hx, 150), head, font=head_f, fill=0)
        num_s = str(num)
        d.text(((W - d.textlength(num_s, font=head_f)) / 2, H - 170), num_s, font=head_f, fill=0)
        return im, d

    # Página 11: abertura de capítulo (sem cabeçalho), título grande, subtítulo, nota de rodapé.
    im, d = new_page(11, first=True)
    y = 420
    d.text((MARGIN_X, y), "1. INTRODUÇÃO AO TEMA", font=title_f, fill=0)
    y += 190
    d.text((MARGIN_X, y), "1.1 Os princípios da administração", font=sub_f, fill=0)
    y += 120
    y = draw_paragraph(d, PARAS[0], body_f, MARGIN_X, y, width)
    # Parágrafo com chamada de nota sobrescrita no fim.
    last_y = draw_paragraph(d, PARAS[1], body_f, MARGIN_X, y + 20, width)
    lines = wrap(d, PARAS[1], body_f, width - 70)
    lx = MARGIN_X + (0 if len(lines) > 1 else 70) + d.textlength(lines[-1], font=body_f)
    d.text((lx + 4, last_y - int(body_f.size * 1.45) - 10), "1", font=sup_f, fill=0)
    # Filete e nota no pé da página.
    ny = H - 420
    d.line((MARGIN_X, ny, MARGIN_X + 400, ny), fill=0, width=3)
    d.text((MARGIN_X, ny + 30), "1 " + NOTE_TEXT, font=note_f, fill=0)
    pages.append(im)
    syn.body_snippets += ["administração pública deve observar os princípios", "proporcionalidade das decisões"]

    # Página 12: corpo comum, parágrafo que continua na próxima página.
    im, d = new_page(12)
    y = TOP
    y = draw_paragraph(d, PARAS[2], body_f, MARGIN_X, y, width) + 20
    y = draw_paragraph(d, PARAS[3], body_f, MARGIN_X, y, width) + 20
    d.text((MARGIN_X, y + 40), "1.2 O controle dos atos", font=sub_f, fill=0)
    y += 170
    draw_paragraph(d, PARAS[4], body_f, MARGIN_X, y, width)
    pages.append(im)
    syn.body_snippets += ["participação da sociedade", "interno, externo e social"]

    # Página 13: duas colunas.
    im, d = new_page(13)
    y = TOP
    d.text((MARGIN_X, y), "1.3 Duas perspectivas", font=sub_f, fill=0)
    y += 140
    colw = (width - 80) // 2
    draw_paragraph(d, COL_LEFT, body_f, MARGIN_X, y, colw, indent=0, justify=False)
    draw_paragraph(d, COL_RIGHT, body_f, MARGIN_X + colw + 80, y, colw, indent=0, justify=False)
    pages.append(im)
    syn.body_snippets += ["primeira coluna", "segunda coluna"]

    # Página 14: inclinada 2°.
    im, d = new_page(14)
    y = TOP
    y = draw_paragraph(d, PARAS[0], body_f, MARGIN_X, y, width) + 20
    draw_paragraph(d, PARAS[3], body_f, MARGIN_X, y, width)
    im = im.rotate(2.0, resample=Image.BICUBIC, expand=False, fillcolor=255)
    pages.append(im)

    # Página 15: trecho parcialmente ilegível (borrado e com ruído).
    im, d = new_page(15)
    y = TOP
    y = draw_paragraph(d, PARAS[1], body_f, MARGIN_X, y, width) + 20
    y2 = draw_paragraph(d, PARAS[4], body_f, MARGIN_X, y, width)
    box = (MARGIN_X, y, W - MARGIN_X, y2)
    region = im.crop(box).filter(ImageFilter.GaussianBlur(9))
    rnd = random.Random(7)
    px = region.load()
    for _ in range(region.width * region.height // 6):
        x, yy = rnd.randrange(region.width), rnd.randrange(region.height)
        px[x, yy] = rnd.choice((0, 255))
    im.paste(region, box[:2])
    pages.append(im)

    # Páginas 16 e 17: corpo simples (completam a repetição dos cabeçalhos).
    for num in (16, 17):
        im, d = new_page(num)
        draw_paragraph(d, PARAS[2 if num == 16 else 4], body_f, MARGIN_X, TOP, width)
        pages.append(im)

    # Nomes fora de ordem alfabética, em ordem natural correta.
    names = [f"pagina {i}.png" for i in (1, 2, 3, 9, 10, 11, 12)]
    for im, name in zip(pages, names):
        im.save(folder / name, dpi=(DPI, DPI))
    syn.files = names
    return syn


if __name__ == "__main__":
    import sys

    s = make_book(sys.argv[1] if len(sys.argv) > 1 else "build/sintetico")
    print("gerado em", s.folder, s.files)
