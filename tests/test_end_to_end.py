"""Teste de ponta a ponta com o livro sintético: OCR real (Tesseract),
detector de layout, análise do documento e exportação DOCX/EPUB, com
verificações estruturais.

Leva cerca de um minuto (OCR de 7 páginas)."""

from __future__ import annotations

import dataclasses
import zipfile

import pytest

from bookbuilder import paths, validate
from bookbuilder import project as projmod
from bookbuilder.document import analyze_document, flow_pages
from bookbuilder.export import docx_writer, epub_writer
from bookbuilder.model import Page, PageStatus, Role
from bookbuilder.pipeline import PageJob, Runner

from .synth import BOOK, CHAPTER, COL_LEFT, COL_RIGHT, make_book

pytestmark = pytest.mark.skipif(bool(paths.missing_components()), reason="componentes de OCR ausentes")


@pytest.fixture(scope="module")
def resultado(tmp_path_factory):
    base = tmp_path_factory.mktemp("e2e")
    syn = make_book(base / "imagens")
    proj = projmod.new_project(str(syn.folder))
    store = projmod.ProjectStore(base / "proj")
    store.save(proj)
    jobs = [PageJob(p.to_dict(), dataclasses.asdict(proj.settings), str(store.figures_dir)) for p in proj.pages]
    by = {p.uid: i for i, p in enumerate(proj.pages)}

    def on(d):
        pg = Page.from_dict(d)
        proj.pages[by[pg.uid]] = pg

    Runner(4).run(jobs, on)
    rep = analyze_document(proj, store)
    proj.export.title = "Livro de testes"
    docx = str(base / "saida.docx")
    epub = str(base / "saida.epub")
    rd = docx_writer.write(proj, store, docx)
    re_ = epub_writer.write(proj, store, epub)
    return syn, proj, rep, docx, epub, rd, re_


def test_ordem_natural_dos_arquivos(resultado):
    syn, proj, *_ = resultado
    assert [p.source.display_name for p in proj.pages] == syn.files


def test_todas_processadas(resultado):
    _, proj, *_ = resultado
    assert all(p.status == PageStatus.DONE for p in proj.pages), [p.error for p in proj.pages]


def test_acentos_preservados(resultado):
    _, proj, *_ = resultado
    txt = " ".join(b.text for p in flow_pages(proj) for b in p.blocks)
    for palavra in ("administração", "pública", "três", "órgãos", "eficiência", "Introdução"):
        assert palavra in txt, palavra


def test_cabecalhos_e_numeros_classificados(resultado):
    _, proj, *_ = resultado
    roles = [(b.role, b.text or " ".join(ln.text for ln in b.lines)) for p in proj.pages for b in p.blocks]
    heads = [t for r, t in roles if r == Role.HEADER]
    nums = [t for r, t in roles if r == Role.PAGE_NUMBER]
    assert sum(BOOK in t for t in heads) >= 2 and sum("Introdução" in t for t in heads) >= 2
    assert len(nums) >= 6


def test_titulos_em_tamanhos_diferentes(resultado):
    _, proj, *_ = resultado
    titles = [b for p in proj.pages for b in p.blocks if b.role == Role.TITLE]
    t1 = next(b for b in titles if "INTRODUÇÃO AO TEMA" in b.text.upper())
    t2 = next(b for b in titles if "princípios da administração" in b.text)
    s1, s2 = proj.styles[t1.style_id], proj.styles[t2.style_id]
    body = next(s for s in proj.styles.values() if s.name == "Corpo do texto")
    assert s1.size > s2.size > body.size
    assert t1.heading_level < t2.heading_level


def test_nota_de_rodape_preservada_e_ligada(resultado):
    syn, proj, *_ = resultado
    notes = [b for p in proj.pages for b in p.blocks if b.role == Role.FOOTNOTE]
    assert notes and syn.note_text[:30] in notes[0].text
    refs = [r for p in proj.pages for b in p.blocks for r in b.runs if r.note_ref]
    assert refs and refs[0].note_ref == notes[0].note_label


def test_duas_colunas_em_ordem(resultado):
    _, proj, *_ = resultado
    page = proj.pages[2]
    txt = " ".join(b.text for b in page.ordered_blocks())
    assert txt.index("primeira coluna") < txt.index("segunda coluna")
    left = next(b for b in page.blocks if "primeira coluna" in b.text)
    assert "segunda coluna" not in left.text  # colunas não se misturam linha a linha
    assert COL_LEFT.split()[-1].rstrip(".") in left.text
    assert COL_RIGHT.split()[0] in txt


def test_pagina_inclinada_corrigida(resultado):
    _, proj, *_ = resultado
    p = proj.pages[3]
    assert abs(abs(p.deskew) - 2.0) <= 0.4
    assert p.mean_conf > 85


def test_trecho_ilegivel_sinalizado(resultado):
    _, proj, *_ = resultado
    p = proj.pages[4]
    flags = {f for b in p.blocks for f in b.flags}
    assert flags & {"baixa_confianca", "ilegivel"}


def test_docx_estrutura(resultado):
    syn, proj, rep, docx, *_ = resultado
    c = validate.check_docx(docx, [BOOK, CHAPTER], expect_footnotes=1,
                            body_must_contain=["administração pública deve observar"])
    assert c.ok, c.messages
    with zipfile.ZipFile(docx) as z:
        names = z.namelist()
        assert any(n.startswith("word/header") for n in names)
        assert any(n.startswith("word/footer") for n in names)
        settings = z.read("word/settings.xml").decode("utf-8")
        assert "evenAndOddHeaders" in settings  # cabeçalhos alternados


def test_epub_estrutura(resultado):
    syn, proj, rep, docx, epub, *_ = resultado
    c = validate.check_epub(epub, [BOOK, CHAPTER], expect_footnotes=1,
                            body_must_contain=["administração pública deve observar", "primeira coluna"])
    assert c.ok, c.messages


def test_docx_de_volta_para_epub(resultado, tmp_path):
    """O DOCX exportado (ou revisado no Word) também vira EPUB."""
    from bookbuilder.docx_import import docx_to_epub

    syn, proj, rep, docx, *_ = resultado
    out = str(tmp_path / "de_docx.epub")
    imp, er = docx_to_epub(docx, out)
    assert imp.footnotes == 1 and imp.headings >= 2
    c = validate.check_epub(out, [BOOK, CHAPTER], expect_footnotes=1,
                            body_must_contain=["administração pública deve observar", "primeira coluna"])
    assert c.ok, c.messages
