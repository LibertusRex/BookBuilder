"""Testes unitários das regras (sem OCR)."""

from collections import Counter

import numpy as np

from bookbuilder.imageio import natural_key
from bookbuilder.layout import order, running, text
from bookbuilder.layout.fixes import fix_roman_after_label, vocab_correct
from bookbuilder.model import Block, Line, Page, Project, Role, Run, SourceRef, Word
from bookbuilder.ocr.preprocess import estimate_skew, remove_dashed_rules, rotate


def test_ordem_natural():
    nomes = ["pagina 10.png", "pagina 2.png", "pagina 1.png", "Pagina 9.png"]
    assert sorted(nomes, key=natural_key) == ["pagina 1.png", "pagina 2.png", "Pagina 9.png", "pagina 10.png"]


def test_hifen_silabico_e_da_palavra():
    vocab = Counter({"criminalidade": 3, "contra-ataque": 2, "dá": 5})
    assert text.keep_hyphen("crimi", "nalidade", vocab) is False  # divisão silábica: une
    assert text.keep_hyphen("contra", "ataque", vocab) is True  # hífen da palavra
    assert text.keep_hyphen("acreditava", "se", vocab) is True  # ênclise
    assert text.keep_hyphen("dar", "lhe", vocab) is True
    assert text.keep_hyphen("Jean", "Paul", vocab) is True  # nome composto
    assert text.keep_hyphen("ex", "presidente", Counter()) is True  # prefixo que sempre leva hífen
    assert text.keep_hyphen("pala", "vra", Counter()) is False  # regra geral


def _word(t, x0, x1, y0=100, y1=130, conf=95.0):
    return Word(t, (x0, y0, x1, y1), conf)


def test_reconstrucao_de_paragrafo_com_hifenizacao():
    l1 = Line((0, 100, 1000, 130), [_word("A", 0, 20), _word("crimi-", 30, 120)], baseline=128, xheight=18)
    l2 = Line((0, 150, 1000, 180), [_word("nalidade", 0, 120, 150, 180), _word("acreditava-", 130, 300, 150, 180)],
              baseline=178, xheight=18)
    l3 = Line((0, 200, 1000, 230), [_word("se", 0, 40, 200, 230), _word("tudo.", 50, 120, 200, 230)], baseline=228, xheight=18)
    b = Block("b", Role.BODY, (0, 100, 1000, 230), lines=[l1, l2, l3], align="justify")
    runs = text.build_runs(b, Counter({"criminalidade": 2}), lambda w, ln: False, lambda w, ln: False)
    assert "".join(r.text for r in runs) == "A criminalidade acreditava-se tudo."


def test_italico_nao_picota_expressao():
    words = [_word("Lições", 0, 100), _word("de", 110, 140), _word("direito", 150, 260), _word("penal", 270, 350)]
    ln = Line((0, 100, 400, 130), words, baseline=128, xheight=18)
    b = Block("b", Role.BODY, ln.bbox, lines=[ln])
    runs = text.build_runs(b, Counter(), lambda w, l: False, lambda w, l: w.text != "de")
    it = [r for r in runs if r.italic]
    assert len(it) == 1 and it[0].text == "Lições de direito penal"


def test_ordem_de_leitura_duas_colunas_com_titulo():
    caixas = [
        (100, 50, 900, 90),  # título de largura total
        (100, 120, 480, 400),  # coluna esquerda, parágrafo 1
        (100, 420, 480, 700),  # coluna esquerda, parágrafo 2
        (520, 120, 900, 380),  # coluna direita, parágrafo 1
        (520, 400, 900, 700),  # coluna direita, parágrafo 2
        (100, 750, 900, 800),  # nota de largura total
    ]
    assert order.xycut(caixas) == [0, 1, 2, 3, 4, 5]


def test_numero_de_pagina():
    assert running.page_number_value("Page 69 of 2245") == 69
    assert running.page_number_value("— 12 —") == 12
    assert running.page_number_value("xiv") == 14
    assert running.page_number_value("Capítulo 3") is None


def _page(i, blocks, h=1000):
    p = Page(SourceRef(f"p{i}.png"), width=800, height=h)
    p.blocks = blocks
    return p


def _blk(bid, role, text_, y0, y1, kind="text", x0=100, x1=700, xh=10.0):
    ws = [Word(t, (x0 + 60 * k, y0, x0 + 60 * k + 50, y1), 95) for k, t in enumerate(text_.split())]
    return Block(bid, role, (x0, y0, x1, y1), kind=kind, lines=[Line((x0, y0, x1, y1), ws, y1, xh)])


def test_cabecalhos_alternados_rodape_numerado_e_titulo_preservado():
    pages = []
    for i in range(8):
        blocks = []
        if i > 0:  # a primeira página (abertura de capítulo) não tem cabeçalho
            head = "O LIVRO DOS TESTES" if i % 2 == 0 else "Capítulo 1 Introdução"
            blocks.append(_blk(f"h{i}", Role.BODY, head, 40, 60))
        if i == 0:
            blocks.append(_blk("t0", Role.TITLE, "Capítulo 1 Introdução", 80, 130, kind="doc_title", xh=25))
        blocks.append(_blk(f"c{i}", Role.BODY, "texto do corpo da página número " + str(i), 300, 600))
        blocks.append(_blk(f"n{i}", Role.BODY, str(10 + i), 950, 970, x0=390, x1=410))
        pages.append(_page(i, blocks))
    res = running.classify(pages)
    for i, p in enumerate(pages):
        roles = {b.id: b.role for b in p.blocks}
        if i > 0:
            assert roles[f"h{i}"] == Role.HEADER
        assert roles[f"n{i}"] == Role.PAGE_NUMBER
        assert roles[f"c{i}"] == Role.BODY
    assert pages[0].blocks[0].role == Role.TITLE  # título de capítulo não vira cabeçalho
    assert res.printed_numbers[pages[3].uid] == 13


def test_bloco_isolado_na_margem_nao_vira_cabecalho():
    pages = [_page(i, [_blk(f"c{i}", Role.BODY, "corpo " * 8, 300, 600)]) for i in range(5)]
    primeira_linha = _blk("x", Role.BODY, "Uma frase longa que começa o texto logo no alto desta página", 70, 95)
    pages[2].blocks.append(primeira_linha)
    running.classify(pages)
    assert primeira_linha.role == Role.BODY


def test_correcoes_pelo_vocabulario():
    vocab = Counter({"criminologia": 6, "por": 30, "isso": 12})
    w = Word("GRIMINOLOGIA", (0, 0, 10, 10), 70)
    assert vocab_correct(w, vocab) and w.text == "CRIMINOLOGIA" and w.conf < 60
    w3 = Word("tribunal", (0, 0, 10, 10), 70)
    assert not vocab_correct(w3, Counter({"tribunal": 5}))


def test_romanos_depois_de_capitulo():
    ws = [Word("CAPÍTULO", (0, 0, 1, 1), 90), Word("|!", (0, 0, 1, 1), 90)]
    fix_roman_after_label(ws)
    assert ws[1].text == "II"


def test_tracejado_removido_texto_mantido():
    img = np.full((200, 600), 255, np.uint8)
    img[50:80, 100:110] = 0  # um "glifo"
    for x in range(20, 580, 13):
        img[120:122, x:x + 5] = 0  # sublinhado tracejado
    removed = remove_dashed_rules(img)
    assert removed >= 1
    assert img[120:122].min() == 255
    assert img[50:80, 100:110].max() == 0


def test_inclinacao_estimada():
    img = np.full((800, 600), 255, np.uint8)
    for y in range(100, 700, 40):
        img[y:y + 12, 60:540] = 0
    skewed = rotate(img, 3.0, 255)
    ang, gain = estimate_skew(skewed)
    assert abs(ang + 3.0) <= 0.3 and gain > 0


def test_serializacao_do_projeto():
    p = Project(source_folder="x")
    pg = Page(SourceRef("a.png"))
    pg.blocks = [Block("b", Role.FOOTNOTE, (0, 0, 1, 1), runs=[Run("texto", italic=True, note_ref="k:1")])]
    p.pages.append(pg)
    q = Project.from_dict(p.to_dict())
    assert q.pages[0].blocks[0].runs[0].italic and q.pages[0].blocks[0].role == Role.FOOTNOTE


def test_paragrafo_e_travessao():
    from bookbuilder.layout.fixes import fix_dashes, fix_section_signs

    ws = [Word("art.", (0, 0, 40, 20), 95), Word("121,", (50, 0, 90, 20), 95), Word("S$", (100, 0, 120, 20), 80),
          Word("2.º,", (130, 0, 170, 20), 95), Word("SS", (180, 0, 200, 20), 80), Word("1.º", (210, 0, 240, 20), 95),
          Word("S", (250, 0, 260, 20), 90), Word("Paulo", (270, 0, 320, 20), 95)]
    assert fix_section_signs(ws) == 2
    assert [w.text for w in ws][2:7] == ["§", "2.º,", "§§", "1.º", "S"]
    d = [Word("lei", (0, 0, 30, 20), 95), Word("-", (40, 10, 58, 12), 90), Word("—,", (60, 10, 110, 12), 90)]
    fix_dashes(d, 18.0)
    assert d[1].text == "—" and d[2].text == "—,"


def test_paragrafo_com_parentese_no_tesseract():
    from bookbuilder.layout.fixes import fix_section_signs

    ws = [Word("pena", (0, 0, 40, 20), 95), Word("(S", (50, 0, 70, 20), 80), Word("1.º),", (80, 0, 120, 20), 95)]
    assert fix_section_signs(ws) == 1 and ws[1].text == "(§"


def test_inciso_l_vira_romano():
    from bookbuilder.layout.fixes import fix_roman_l

    ws = [Word(t, (0, 0, 10, 10), 90) for t in ("(§", "1.º,", "l)", "inciso", "l,", "a", "l)")]
    assert fix_roman_l(ws) == 2
    assert [w.text for w in ws] == ["(§", "1.º,", "I)", "inciso", "I,", "a", "l)"]


def test_inciso_l_no_texto():
    from bookbuilder.layout.fixes import fix_roman_l_text

    assert fix_roman_l_text("injúria (§ 1.º, l) e graves (§ 3.º, inciso |)") == "injúria (§ 1.º, I) e graves (§ 3.º, inciso I)"
    assert fix_roman_l_text("art. 121, § 1.º, 1, e 2") == "art. 121, § 1.º, I, e 2"


def test_acertos_finais_de_texto():
    from collections import Counter

    from bookbuilder.layout.fixes import accent_map, fix_text, restore_accents

    assert fix_text("lugar ermo (inciso), I), se") == "lugar ermo (inciso I), se"
    assert fix_text("60 anos (inciso);I);") == "60 anos (inciso I);"
    assert fix_text("(art. 140, § 1.º, I Te II)") == "(art. 140, § 1.º, I e II)"
    assert fix_text("Art. 161, § 1.%, II") == "Art. 161, § 1.º, II"
    assert fix_text("no art. 155,$  2.9") == "no art. 155, § 2.º"
    assert fix_text("arts. 230 e 247,I e II; item 3.3,“b”). Pena de 1,5 ano") == \
        "arts. 230 e 247, I e II; item 3.3, “b”). Pena de 1,5 ano"
    amap = accent_map(Counter({"vítima": 50, "ímpeto": 12, "muñoz": 15, "peça": 40, "binário": 30, "binario": 2}))
    assert restore_accents("a vitima e o impeto de Munhoz", amap) == "a vítima e o ímpeto de Muñoz"
    assert restore_accents("peca pela doppio binario em http://x.com/ligacao", amap) == "peca pela doppio binario em http://x.com/ligacao"
