"""PDF de páginas digitalizadas (scanner do iPhone, Scanner Pro…): leitura
das páginas, iluminação igualada (sombra da mão), linhas endireitadas
(folha curvada), pontilhado de sumário e capitular."""

from __future__ import annotations

import dataclasses

import cv2
import numpy as np
import pytest
from PIL import Image, ImageDraw
from rapidfuzz import fuzz

from bookbuilder import imageio, paths
from bookbuilder import project as projmod
from bookbuilder.layout import cleanup, noise
from bookbuilder.model import Block, Line, Page, PageStatus, Role, Run, Settings, Word
from bookbuilder.ocr import preprocess
from bookbuilder.pipeline import PageJob, apply_light, process_page

from .synth import PARAS, draw_paragraph, font

W, H = 1500, 2400


def _page(paras=PARAS[:4]) -> np.ndarray:
    im = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(im)
    f = font("georgia.ttf", 9)
    y = 220
    for t in paras:
        y = draw_paragraph(d, t, f, 150, y, W - 300) + 25
    return np.asarray(im)


def _photo(gray: np.ndarray, curl: bool = True) -> np.ndarray:
    """Foto simulada: luz quente, sombra de borda nítida sobre metade da
    página e, opcionalmente, a folha curvada perto da borda direita."""
    g = gray.astype(np.float32)
    if curl:
        xs, ys = np.meshgrid(np.arange(W, dtype=np.float32), np.arange(H, dtype=np.float32))
        dy = 70 * np.clip((xs / W - 0.55) / 0.45, 0, 1) ** 2 * (ys / H)
        g = cv2.remap(g, xs, ys - dy, cv2.INTER_LINEAR, borderValue=255)
    light = np.ones((H, W), np.float32) * 0.82
    light[:, W // 2:] = 0.42  # sombra da mão
    light = cv2.GaussianBlur(light, (0, 0), 6)
    out = g[:, :, None] * light[:, :, None] * np.array([1.0, 0.93, 0.72], np.float32)
    return np.clip(out, 0, 255).astype(np.uint8)


@pytest.fixture(scope="module")
def pdf(tmp_path_factory):
    base = tmp_path_factory.mktemp("pdf")
    pages = [Image.fromarray(_photo(_page())), Image.fromarray(_photo(_page(PARAS[2:]), curl=False))]
    path = base / "livro.pdf"
    pages[0].save(path, "PDF", save_all=True, append_images=pages[1:], resolution=200, quality=92)
    return path


def test_paginas_do_pdf(pdf):
    refs = imageio.scan_folder(pdf)
    assert len(refs) == 2
    assert [r.display_name for r in refs] == ["livro.pdf [pág. 1]", "livro.pdf [pág. 2]"]
    assert refs[0].uid != refs[1].uid and refs[0].uid == imageio.scan_folder(pdf)[0].uid
    im = imageio.open_frame(refs[0])
    assert abs(im.size[0] - W) <= 2 and abs(im.size[1] - H) <= 2  # resolução da foto, sem reamostrar
    assert max(imageio.thumbnail(refs[0]).size) <= 200
    assert imageio.read_dpi(refs[0]) == 0.0  # tamanho de página do aplicativo não é o do livro
    assert imageio.scan_kind(refs[0]) == "pdf"


def test_projeto_a_partir_do_pdf(pdf):
    proj = projmod.new_project(str(pdf))
    assert len(proj.pages) == 2
    assert proj.export.base_name == "livro"
    assert proj.export.output_dir == str(pdf.parent)


def test_sombra_some_e_texto_fica():
    gray = _page()
    photo = _photo(gray, curl=False)
    flat = preprocess.flatten_illumination(photo, preprocess.text_height(cv2.cvtColor(photo, cv2.COLOR_RGB2GRAY)))
    g = cv2.cvtColor(flat, cv2.COLOR_RGB2GRAY)
    paper = gray > 250
    assert abs(float(np.median(g[:, :W // 2][paper[:, :W // 2]])) - float(np.median(g[:, W // 2:][paper[:, W // 2:]]))) < 8
    ink = gray < 60
    assert float(np.median(g[:, W // 2:][ink[:, W // 2:]])) < 110  # texto na sombra continua escuro
    paper_rgb = flat[paper].reshape(-1, 3).mean(0)
    assert paper_rgb.max() - paper_rgb.min() < 12  # papel branco, sem a cor da luz
    bg, ink_level = preprocess.estimate_levels(g)
    assert bg > ink_level  # não é tomada por "modo escuro"


def test_curvatura_endireitada():
    gray = _page()
    curled = cv2.cvtColor(_photo(gray), cv2.COLOR_RGB2GRAY)
    flat = preprocess.flatten_illumination(cv2.cvtColor(curled, cv2.COLOR_GRAY2RGB), 25)
    g = preprocess.normalize(cv2.cvtColor(flat, cv2.COLOR_RGB2GRAY), *preprocess.estimate_levels(cv2.cvtColor(flat, cv2.COLOR_RGB2GRAY)))
    warp = preprocess.fit_warp(g, 15)
    assert warp is not None
    fixed = preprocess.apply_warp(g, warp)

    def sharp(img):  # linhas retas: perfil horizontal de tinta mais contrastado na metade direita
        return float(np.var((img[:, W // 2:] < 128).sum(axis=1)))

    assert sharp(fixed) > sharp(g) * 1.2
    assert preprocess.fit_warp(gray, 15) is None  # página plana: nada a fazer


def test_sem_sobreposicao_em_digitalizacao():
    prep = preprocess.prepare(_photo(_page(), curl=False), overlays=False)
    assert prep.overlays == []


@pytest.mark.skipif(bool(paths.missing_components()), reason="componentes de OCR ausentes")
def test_ocr_da_pagina_fotografada(pdf, tmp_path):
    ref = imageio.scan_folder(pdf)[0]
    job = PageJob(Page(source=ref).to_dict(), dataclasses.asdict(Settings()), str(tmp_path))
    page = Page.from_dict(process_page(job))
    assert page.status == PageStatus.DONE, page.error
    assert page.light and page.light.get("warp") and not page.overlays and not page.inverted
    text = " ".join(" ".join(ln.text for ln in b.lines) for b in page.ordered_blocks() if b.role != Role.ARTIFACT)
    assert fuzz.token_set_ratio(text, " ".join(PARAS[:4])) >= 90
    # A mesma imagem de trabalho é refeita igual (Surya, árbitro, revisão).
    rgb = imageio.load_rgb(ref)
    assert np.array_equal(apply_light(rgb, page.light), apply_light(rgb, page.light))
    assert apply_light(rgb, page.light).shape[1] == 1800


def _w(t, x0, x1, h=30, conf=95.0, y=100):
    return Word(text=t, bbox=(x0, y - h, x1, y), conf=conf)


def test_pontilhado_de_sumario():
    ln = Line(bbox=(0, 60, 1400, 100), words=[
        _w("2.", 0, 30), _w("Bem-estar", 40, 200), _w("físico", 210, 300),
        _w("CESSA", 320, 500, h=12, conf=12), _w("E", 510, 530, h=7, conf=34), _w("CPI", 540, 700, h=7, conf=0),
        _w("PELO", 710, 800, h=44, conf=8), _w("SO", 810, 1000, h=6, conf=18), _w("65", 1350, 1390)])
    b = Block(id="b", role=Role.BODY, bbox=ln.bbox, lines=[ln])
    p = Page(source=imageio.SourceRef(path="x.pdf"), blocks=[b])
    assert cleanup.dot_leaders([p]) == 1
    assert ln.text == "2. Bem-estar físico ........ 65"
    # Título com os pontos grudados: a palavra fica.
    ln2 = Line(bbox=(0, 60, 1400, 100), words=[_w("CONCLUSÃO:", 0, 200), _w("FRENTE.....-0enssra", 210, 900, conf=0),
                                              _w("181", 1350, 1400)])
    b.lines = [ln2]
    cleanup.dot_leaders([p])
    assert ln2.text == "CONCLUSÃO: FRENTE ........ 181"


def test_texto_comum_terminado_em_numero_nao_vira_pontilhado():
    words = [_w("todos", 0, 100), _w("(art.", 110, 170), _w("117,", 200, 243, h=29, conf=8),
             _w("Ia", 270, 290, h=17, conf=0), _w("IV", 300, 330, h=23, conf=0)]
    ln = Line(bbox=(0, 60, 330, 100), words=list(words))
    p = Page(source=imageio.SourceRef(path="x.png"), blocks=[Block(id="b", role=Role.BODY, bbox=ln.bbox, lines=[ln])])
    assert cleanup.dot_leaders([p]) == 0
    assert ln.text == "todos (art. 117, Ia IV"


def test_capitular_volta_para_a_palavra():
    cap = Block(id="c", role=Role.BODY, bbox=(287, 491, 328, 540),
                lines=[Line(bbox=(287, 491, 328, 540), words=[Word("M", (287, 491, 328, 540), 90.0)])])
    first = Word("as", (330, 512, 370, 540), 95.0)
    body = Block(id="b", role=Role.BODY, bbox=(281, 510, 1754, 653),
                 lines=[Line(bbox=(330, 510, 1754, 545), words=[first, Word("o", (380, 518, 395, 540), 95.0)])],
                 surya_html="<p>as o que</p>")
    p = Page(source=imageio.SourceRef(path="x.pdf"), blocks=[cap, body])
    noise.attach_initials(p)
    assert first.text == "Mas" and body.surya_html == "<p>Mas o que</p>"
    assert cap.role == Role.ARTIFACT and not cap.lines


def _entry(bid, words, y):
    ln = Line(bbox=(0, y, 1400, y + 40), words=[_w(t, x0, x1, y=y + 40) for t, x0, x1 in words])
    return Block(id=bid, role=Role.BODY, bbox=ln.bbox, lines=[ln])


def test_sumario_uma_entrada_por_paragrafo_com_numero_a_direita():
    blocks = [_entry("a", [("l.", 0, 30), ("Bem-estar", 40, 200), ("espiritual", 210, 360), ("........", 370, 1300),
                           ("35", 1350, 1390)], 100),
              _entry("b", [("2.", 0, 30), ("Bem-estar", 40, 200), ("físico", 210, 300), ("........", 310, 1300),
                           ("65", 1350, 1390)], 160),
              _entry("c", [("3.", 0, 30), ("Bem-estar", 40, 200), ("intelectual", 210, 360), ("........", 370, 1300),
                           ("95", 1350, 1390)], 220),
              _entry("d", [("4.º", 0, 30), ("Bem-estar", 40, 200), ("relacional", 210, 360), ("........", 370, 1300),
                           ("123", 1340, 1390)], 280)]
    intro = _entry("i", [("INTRODUÇÃO:", 0, 200), ("FELIZ", 210, 300), ("........", 310, 1300), ("9", 1370, 1390)], 20)
    intro.surya_html = "<p>INTRODUÇÃO: FELIZ, ACONTECER.....9</p>"
    p = Page(source=imageio.SourceRef(path="x.pdf"), blocks=[intro] + blocks)
    cleanup.dot_leaders([p])
    assert all(cleanup.TOC in b.flags for b in p.blocks)
    for b in p.blocks:
        b.runs = [r for ln in b.lines for r in [Run(ln.text)]]
    cleanup.toc_entries([p])
    assert [b.text for b in p.ordered_blocks()] == [
        "INTRODUÇÃO: FELIZ, ACONTECER\t9", "1. Bem-estar espiritual\t35", "2. Bem-estar físico\t65",
        "3. Bem-estar intelectual\t95", "4. Bem-estar relacional\t123"]


def test_capa_fotografada_vira_imagem_inteira():
    title = Block(id="t", role=Role.TITLE, bbox=(100, 100, 1500, 400),
                  lines=[Line(bbox=(100, 100, 1500, 400), words=[_w("Seja", 100, 600, h=200, y=400)])])
    art = Block(id="f", role=Role.FIGURE, bbox=(500, 800, 1300, 1800))
    cover = Page(source=imageio.SourceRef(path="x.pdf"), blocks=[title, art], width=1800, height=2800,
                 light={"scale": 1.0, "xh": 50})
    inner = Page(source=imageio.SourceRef(path="x.pdf", frame=1), blocks=[_entry("b", [("texto", 0, 100)], 100)],
                 width=1800, height=2800, light={"scale": 1.0, "xh": 20})
    got = noise.cover_page([cover, inner], lambda p: "capa.jpg")
    assert got is not None and got.image_file == "capa.jpg" and got.bbox == (0, 0, 1800, 2800)
    assert [b.role for b in cover.blocks if b is not got] == [Role.ARTIFACT, Role.ARTIFACT]
    assert noise.cover_page([cover, inner]) is got  # nova análise: a mesma capa
    assert noise.cover_page([inner]) is None  # página de texto comum não é capa


def test_numero_grudado_no_rodape_vira_numero_de_pagina():
    from bookbuilder.layout import running

    def page(i, blocks, num=None):
        return Page(source=imageio.SourceRef(path="x.pdf", frame=i), blocks=blocks, printed_number=num)

    foot = Block(id="f", role=Role.FOOTER, bbox=(100, 2700, 600, 2740), lines=[Line(bbox=(100, 2700, 600, 2740), words=[
        _w("12", 100, 140, y=2740), _w("Tal", 160, 220, y=2740), _w("Ben-Shahar", 230, 600, y=2740)])])
    pages = [page(0, [], "10"), page(1, [], "11"), page(2, [foot])]
    assert running.numbers_in_margins(pages) == 1
    assert foot.lines[0].text == "Tal Ben-Shahar" and foot.bbox[0] == 160
    num = next(b for b in pages[2].blocks if b.role == Role.PAGE_NUMBER)
    assert num.text == "" and num.lines[0].text == "12" and pages[2].printed_number == "12"
    # Número que não é o da sequência (ano, edição) fica no texto.
    foot2 = Block(id="g", role=Role.FOOTER, bbox=(0, 0, 1, 1), lines=[Line(bbox=(0, 0, 1, 1), words=[
        _w("Edição", 0, 100), _w("2020", 110, 200)])])
    assert running.numbers_in_margins([page(0, [], "10"), page(1, [foot2])]) == 0


def test_paragrafos_do_corpo_justificados_com_recuo_igual():
    from bookbuilder.layout.structure import even_body_paragraphs

    def par(bid, first, left, right, align="justify", n=4):
        lines = [Line(bbox=(0, k * 40, 1400, k * 40 + 30), xheight=20.0) for k in range(n)]
        return Block(id=bid, role=Role.BODY, bbox=(0, 0, 1400, n * 40), lines=lines, align=align,
                     indent_first=first, indent_left=left, indent_right=right)

    bs = [par("a", 112, 5, 0), par("b", 121, 7, 34, "left"), par("c", 3, 0, 60), par("d", 98, 26, 0),
          par("epigrafe", -3, 212, 1, n=2), par("assinatura", 0, 1000, 0, "right", n=1)]
    p = Page(source=imageio.SourceRef(path="x.pdf"), blocks=bs, light={"scale": 1.0})
    assert even_body_paragraphs([p]) == 4
    assert [(b.align, b.indent_first, b.indent_left, b.indent_right) for b in bs[:4]] == [
        ("justify", 112, 0, 0), ("justify", 112, 0, 0), ("justify", 0, 0, 0), ("justify", 112, 0, 0)]
    assert bs[4].indent_left == 212 and bs[5].align == "right"
    p.light = None  # capturas de tela: recuos medidos com precisão, ficam como estão
    bs[1].indent_first = 121
    assert even_body_paragraphs([p]) == 0


def test_fusao_chamadas_de_nota_hifen_e_pontos():
    from collections import Counter

    from bookbuilder.layout import fusion

    lex = fusion.Lexicon(Counter(), fusion.load_dictionary())

    def fused(tess, surya):
        runs = fusion.fuse(fusion._tokens_to_runs(fusion.runs_to_tokens([Run(tess)])), surya, lex)
        return "".join(f"^{r.text}" if r.superscript else r.text for r in runs)

    # Chamada de nota que o Tesseract lê como sinais; o Surya lê o número.
    assert fused("estresse (TEPT).?º A maioria", "<p>estresse (TEPT).2 A maioria</p>") == "estresse (TEPT).^2 A maioria"
    assert fused("na loteria.? Quantos", "<p>na loteria.4 Quantos</p>") == "na loteria.^4 Quantos"
    assert fused("é integralidade” !º", "<p>é integralidade”.<sup>10</sup></p>") == "é integralidade”.^10"
    # Divisão silábica do fim da linha mantida pelo Surya; ponto solto do Tesseract.
    assert (fused("pessoas como. animais racionais esse é", "<p>pessoas como animais racio-</p><p>nais — esse é</p>")
            == "pessoas como animais racionais — esse é")
    # Algarismo no lugar de letra.
    assert fused("Para mim, 2 única definição", "<p>Para mim, a única definição</p>") == "Para mim, a única definição"


def test_palavra_partida_com_maiuscula():
    from collections import Counter

    from bookbuilder.layout import fusion

    lex = fusion.Lexicon(Counter(), fusion.load_dictionary())
    assert cleanup._join_split_word("Por que ela é im Portante? E", lex) == "Por que ela é importante? E"
    assert cleanup._join_split_word("viu em São Paulo um Brasil", lex) == "viu em São Paulo um Brasil"


def test_rodape_em_letra_pequena(tmp_path):
    from docx import Document

    from bookbuilder.export import docx_writer
    from bookbuilder.model import Project, Style

    def pg(i, blocks):
        return Page(source=imageio.SourceRef(path=f"p{i}.png"), status=PageStatus.DONE, width=1500, height=2400,
                    blocks=blocks)

    body = [Block(id=f"b{i}", role=Role.BODY, bbox=(150, 300, 1350, 600), runs=[Run("Texto corrido da página.")])
            for i in range(3)]
    foot = [Block(id=f"f{i}", role=Role.FOOTER, bbox=(150, 2300, 600, 2340), runs=[Run("Tal Ben-Shahar")],
                  lines=[Line(bbox=(150, 2300, 600, 2340), words=[_w("Tal", 150, 220, y=2340)])]) for i in range(3)]
    proj = Project(pages=[pg(i, [body[i], foot[i]]) for i in range(3)])
    proj.styles = {"s1": Style(id="s1", name="Corpo do texto", role=Role.BODY, size_pt=11.0)}
    out = tmp_path / "x.docx"
    docx_writer.write(proj, None, str(out))
    d = Document(str(out))
    assert d.styles["Footer"].font.size.pt == 5.0
    par = d.sections[0].footer.paragraphs[0]
    assert par.style.name == "Footer" and "Tal" in par.text


def test_palavra_partida_entre_paginas_fica_em_amarelo():
    from bookbuilder.document import DocReport
    from bookbuilder.layout.structure import continuations

    def blk(bid, txt):
        ln = Line(bbox=(150, 300, 1350, 340), xheight=20.0, words=[_w(t, 150, 200) for t in txt.split()])
        return Block(id=bid, role=Role.BODY, bbox=(150, 300, 1350, 600), lines=[ln], runs=[Run(txt)])

    a = blk("a", "Por outro lado, a busca indireta da felicida-")
    b = blk("b", "— primeiro decompondo-a em seus elementos")
    pages = [Page(source=imageio.SourceRef(path="x.pdf", frame=i), blocks=[x]) for i, x in enumerate((a, b))]
    continuations(pages, __import__("collections").Counter(), DocReport())
    assert b.continues_prev and "juntar_sem_espaco" not in b.flags
    assert a.text == "Por outro lado, a busca indireta da felicida-"  # nada inventado
    assert [r.text for r in a.runs if r.low_conf] == ["felicida-"]


def test_quebras_de_pagina_no_livro_digitalizado():
    from bookbuilder.export.docx_writer import _scan_breaks

    def blk(bid, role, level=0, flags=()):
        return Block(id=bid, role=role, bbox=(0, 0, 10, 10), heading_level=level, flags=list(flags))

    p1 = Page(source=imageio.SourceRef(path="x.pdf"), light={"scale": 1.0},
              blocks=[blk("rosto", Role.BODY), blk("sum", Role.TITLE, 4),
                      blk("e1", Role.BODY, flags=[cleanup.TOC]), blk("e2", Role.BODY, flags=[cleanup.TOC])])
    p2 = Page(source=imageio.SourceRef(path="x.pdf", frame=1), light={"scale": 1.0},
              blocks=[blk("intro", Role.TITLE, 1), blk("t", Role.BODY), blk("cap2", Role.TITLE, 1),
                      blk("secao", Role.TITLE, 3)])
    for i, b in enumerate(p1.blocks + p2.blocks):
        b.order = i
    assert _scan_breaks([p1, p2]) == {"sum", "intro", "cap2"}
    p1.light = p2.light = None  # capturas de tela: sem quebras novas
    assert _scan_breaks([p1, p2]) == set()


def test_entrada_de_sumario_nao_continua_na_pagina_seguinte():
    from collections import Counter

    from bookbuilder.document import DocReport
    from bookbuilder.layout.structure import continuations

    def blk(bid, txt, flags=()):
        ln = Line(bbox=(150, 300, 1350, 340), xheight=20.0, words=[_w(t, 150, 200) for t in txt.split()])
        return Block(id=bid, role=Role.BODY, bbox=(150, 300, 1350, 600), lines=[ln], runs=[Run(txt)], flags=list(flags))

    a = blk("a", "AGRADECIMENTOS\t197", [cleanup.TOC])
    b = blk("b", "ainda mais benéfica quando estamos em baixa")
    pages = [Page(source=imageio.SourceRef(path="x.pdf", frame=i), blocks=[x]) for i, x in enumerate((a, b))]
    continuations(pages, Counter(), DocReport())
    assert not b.continues_prev


def test_st_lido_com_j_vira_stj_e_ordinal_de_turma():
    from bookbuilder.layout import fixes

    assert fixes.fix_text("STJ 5ª Turma") == "STJ 5ª Turma"
    assert fixes.fix_text("ST) 6º Turma, AgRg") == "STJ 6ª Turma, AgRg"
    assert fixes.fix_text("STJ 52 Turma, AgRg") == "STJ 5ª Turma, AgRg"
    assert fixes.fix_text("Tema 2023 Turma") == "Tema 2023 Turma"


def test_coluna_da_direita_nao_vira_recuo_do_texto():
    from bookbuilder.export import docx_writer

    assert docx_writer._percentile([10, 12, 300, 11, 9], 0.1) == 9
    assert docx_writer._percentile([10, 12, 300, 11, 9], 0.9) == 300


def test_erros_recorrentes_de_ocr_em_citacoes_e_frases():
    from bookbuilder.layout.fixes import fix_text

    assert fix_text("STF22 Turma. HC 203217") == "STF 2ª Turma. HC 203217"
    assert fix_text("STJ 52a Turma, REsp") == "STJ 5ª Turma, REsp"
    assert fix_text("mA garantia do crédito") == "A garantia do crédito"
    assert fix_text("1 KO crime de extorsão") == "1 O crime de extorsão"
    assert fix_text("Rel, Min, Sebastião") == "Rel. Min. Sebastião"
    assert fix_text("RESp REsp.1.977.165/MS") == "REsp. 1.977.165/MS"
    assert fix_text("não se contenta! com afirmações") == "não se contenta com afirmações"
    assert fix_text("adolescente de' 12 anos") == "adolescente de 12 anos"
    # Quando a palavra existe de verdade, não mexe.
    assert fix_text("Ministro Min. Sebastião") == "Ministro Min. Sebastião"


def test_palavra_partida_em_minusculas_se_a_junta_existe(lex_fixture=None):
    from bookbuilder.layout import cleanup

    class Lex:
        validas = {"concreto", "concreta", "em", "presa", "empresa", "importante"}

        def valid(self, w):
            return w.lower() in self.validas

    lex = Lex()
    assert cleanup._join_split_word("um caso con creto e claro", lex) == "um caso concreto e claro"
    assert cleanup._join_split_word("uma con creta", lex) == "uma concreta"
    # Duas palavras válidas continuam separadas.
    assert cleanup._join_split_word("uma em presa grande", lex) == "uma em presa grande"
    # Quebra de linha com hífen ("a vio-") não é palavra partida.
    assert cleanup._join_split_word("entre a vio-", lex) == "entre a vio-"


def test_n_de_numero_vira_no():
    from bookbuilder.layout.fixes import fix_text

    assert fix_text("(Súmula n, 231/STJ)") == "(Súmula nº 231/STJ)"
    assert fix_text("AgRg na MPUMP n. 6/DF") == "AgRg na MPUMP nº 6/DF"
    # Letra "n" dentro de palavra ou fora de número fica como está.
    assert fix_text("pena n. grave") == "pena n. grave"
    assert fix_text("em tn. 3") == "em tn. 3"


def test_ordinal_com_ponto_vira_ordinal_sem_ponto():
    from bookbuilder.layout.fixes import fix_text

    assert fix_text("art. 2.º da Lei") == "art. 2º da Lei"
    assert fix_text("§ 1.º, II") == "§ 1º, II"
    assert fix_text("1.ª Turma") == "1ª Turma"
    # Número decimal não é ordinal.
    assert fix_text("valor de 1.5 mil") == "valor de 1.5 mil"


def test_ordinal_de_paragrafo_lido_como_9_e_o():
    from bookbuilder.layout.fixes import fix_text

    assert fix_text("(art. 155, § 49). Em tese") == "(art. 155, § 4º). Em tese"
    assert fix_text("art. 45, §19, do CP") == "art. 45, § 1º, do CP"
    assert fix_text("art. 157, § 29-A, I") == "art. 157, § 2º-A, I"
    assert fix_text("§ 2.9 do art. 155") == "§ 2º do art. 155"
    assert fix_text("nos §§ 19, 3º e 4º") == "nos §§ 1º, 3º e 4º"
    assert fix_text("art. 9o, § 29, da Lei no 10.684/2003") == "art. 9º, § 2º, da Lei nº 10.684/2003"
    # Artigo terminado em 9 é número de verdade.
    assert fix_text("art. 59 do CP") == "art. 59 do CP"
    assert fix_text("§ 10 do art. 4") == "§ 10 do art. 4"
