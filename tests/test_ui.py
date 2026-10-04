"""Teste de fumaça da interface (sem janela visível): abre um projeto,
percorre as quatro etapas, seleciona um bloco, muda sua função e edita um
estilo."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from bookbuilder.model import Block, Line, Page, PageStatus, Project, Role, Run, SourceRef, Style, Word  # noqa: E402


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _projeto(tmp_path) -> Project:
    from PIL import Image

    img = tmp_path / "p1.png"
    Image.new("RGB", (800, 1100), "white").save(img)
    pg = Page(SourceRef(str(img)), width=800, height=1100, status=PageStatus.DONE)
    w = [Word("Texto", (100, 300, 200, 330), 95), Word("revisado", (210, 300, 330, 330), 40)]
    pg.blocks = [
        Block("b1", Role.BODY, (100, 300, 700, 330), lines=[Line((100, 300, 700, 330), w, 328, 15)],
              runs=[Run("Texto "), Run("revisado", low_conf=True)], style_id="s1", flags=["baixa_confianca"]),
        Block("b2", Role.HEADER, (100, 40, 700, 60), lines=[Line((100, 40, 700, 60), [Word("CABEÇALHO", (100, 40, 300, 60), 95)], 58, 9)]),
    ]
    proj = Project(source_folder=str(tmp_path), pages=[pg])
    proj.styles = {"s1": Style("s1", "Corpo do texto", size_pt=11, detected_family="Georgia", xheight_px=15)}
    proj.analysis["px_per_pt"] = 4.0
    return proj


def test_fluxo_da_interface(app, tmp_path):
    from bookbuilder.document import DocReport
    from bookbuilder.ui.main_window import MainWindow

    w = MainWindow()
    w.state.project = _projeto(tmp_path)
    w.state.report = DocReport()
    w.images.reload()
    assert w.images.list.count() == 1
    for i in range(4):
        w.go(i)
        assert w.stack.currentIndex() == i
    w.go(2)
    rv = w.review
    assert rv.page is not None
    rv.select_block("b1")
    assert rv.block.id == "b1"
    assert "revisado" in rv.editor.toPlainText()
    # Muda a função do bloco para título.
    rv.cmb_role.setCurrentIndex(rv.cmb_role.findData(Role.TITLE.value))
    rv._apply_role()
    assert rv.block.role == Role.TITLE and rv.block.role_locked
    # Edita o texto.
    rv.editor.setPlainText("Texto corrigido")
    rv._save_text()
    assert rv.block.text == "Texto corrigido" and rv.block.user_edited
    # Ajusta o estilo (vale para todos os blocos dele).
    rv.tbl_styles.setCurrentCell(0, 0)
    rv.spin_size.setValue(12.5)
    rv._apply_style()
    assert w.state.project.styles["s1"].size == 12.5
    w.state.unsaved = False
    w.close()
