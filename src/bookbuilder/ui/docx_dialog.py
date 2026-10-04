"""Converter um DOCX (por exemplo, o livro já revisado no Word) em EPUB."""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class DocxToEpubWorker(QThread):
    finished_ok = Signal(str)
    failed = Signal(str)

    def __init__(self, docx: str, epub: str, title: str, author: str, cover: str):
        super().__init__()
        self.args = (docx, epub, title, author, cover)

    def run(self) -> None:
        import traceback

        from ..docx_import import docx_to_epub
        from .. import validate

        docx, epub, title, author, cover = self.args
        try:
            imp, er = docx_to_epub(docx, epub, title, author, cover)
            chk = validate.check_epub(epub, [], expect_footnotes=imp.footnotes)
            lines = [
                f"EPUB gravado em: {epub}",
                "",
                f"Parágrafos: {imp.paragraphs}; títulos: {imp.headings} (sumário com {er.toc_entries} entradas, "
                f"{er.chapters} capítulos); notas de rodapé: {imp.footnotes}; imagens: {er.images}; "
                f"tabelas: {imp.tables}.",
                "Verificação do EPUB: " + ("sem problemas." if chk.ok else "; ".join(chk.messages)),
                "",
                "Cabeçalhos, rodapés e números de página do Word não vão para o EPUB (a paginação é a do leitor).",
            ] + [f"• {n}" for n in imp.notes + er.notes]
            self.finished_ok.emit("\n".join(lines))
        except Exception as e:  # noqa: BLE001
            self.failed.emit(f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


class DocxToEpubDialog(QDialog):
    def __init__(self, parent=None, docx_path: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Converter DOCX em EPUB")
        self.resize(640, 420)
        self.worker: DocxToEpubWorker | None = None
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Converte um documento do Word (por exemplo, o livro já revisado) em EPUB 3: títulos viram "
                             "o sumário, quadros, listas, notas de rodapé, imagens e links são mantidos."))
        form = QFormLayout()
        self.ed_docx = QLineEdit(docx_path)
        self.ed_epub = QLineEdit()
        self.ed_title = QLineEdit()
        self.ed_author = QLineEdit()
        self.ed_cover = QLineEdit()
        self.ed_cover.setPlaceholderText("opcional")
        self.ed_title.setPlaceholderText("o do documento, ou o nome do arquivo")
        self.ed_author.setPlaceholderText("opcional")
        form.addRow("Documento DOCX:", self._with_button(self.ed_docx, self._choose_docx))
        form.addRow("Salvar EPUB em:", self._with_button(self.ed_epub, self._choose_epub))
        form.addRow("Título:", self.ed_title)
        form.addRow("Autor:", self.ed_author)
        form.addRow("Capa (imagem):", self._with_button(self.ed_cover, self._choose_cover))
        lay.addLayout(form)
        self.out = QPlainTextEdit()
        self.out.setReadOnly(True)
        lay.addWidget(self.out, 1)
        self.buttons = QDialogButtonBox()
        self.btn_go = self.buttons.addButton("Converter", QDialogButtonBox.ButtonRole.AcceptRole)
        self.btn_folder = self.buttons.addButton("Abrir pasta", QDialogButtonBox.ButtonRole.ActionRole)
        self.btn_close = self.buttons.addButton("Fechar", QDialogButtonBox.ButtonRole.RejectRole)
        self.btn_folder.setEnabled(False)
        self.buttons.accepted.connect(self._convert)
        self.buttons.rejected.connect(self.reject)
        self.btn_folder.clicked.connect(self._open_folder)
        lay.addWidget(self.buttons)
        if docx_path:
            self._docx_changed()

    def _with_button(self, edit: QLineEdit, fn) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(edit, 1)
        b = QPushButton("Escolher…")
        b.clicked.connect(fn)
        h.addWidget(b)
        return w

    def _docx_changed(self) -> None:
        d = self.ed_docx.text().strip()
        if d and not self.ed_epub.text().strip():
            self.ed_epub.setText(str(Path(d).with_suffix(".epub")))

    def _choose_docx(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, "Escolha o documento", "", "Documentos do Word (*.docx)")
        if f:
            self.ed_docx.setText(f)
            self.ed_epub.clear()
            self._docx_changed()

    def _choose_epub(self) -> None:
        f, _ = QFileDialog.getSaveFileName(self, "Salvar EPUB", self.ed_epub.text(), "EPUB (*.epub)")
        if f:
            self.ed_epub.setText(f if f.lower().endswith(".epub") else f + ".epub")

    def _choose_cover(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, "Imagem da capa", "", "Imagens (*.jpg *.jpeg *.png)")
        if f:
            self.ed_cover.setText(f)

    def _convert(self) -> None:
        docx = self.ed_docx.text().strip()
        epub = self.ed_epub.text().strip()
        if not docx or not os.path.isfile(docx) or not docx.lower().endswith(".docx"):
            QMessageBox.warning(self, "Converter DOCX em EPUB", "Escolha um arquivo .docx existente.")
            return
        if not epub:
            QMessageBox.warning(self, "Converter DOCX em EPUB", "Escolha onde salvar o EPUB.")
            return
        if os.path.exists(epub) and QMessageBox.question(self, "Converter DOCX em EPUB",
                                                         f"{epub}\n\njá existe. Substituir?") != QMessageBox.StandardButton.Yes:
            return
        self.btn_go.setEnabled(False)
        self.btn_folder.setEnabled(False)
        self.out.setPlainText("Convertendo… (um livro grande pode levar um ou dois minutos)")
        self.worker = DocxToEpubWorker(docx, epub, self.ed_title.text().strip(), self.ed_author.text().strip(),
                                       self.ed_cover.text().strip())
        self.worker.finished_ok.connect(self._done)
        self.worker.failed.connect(self._failed)
        self.worker.start()

    def _done(self, text: str) -> None:
        self.out.setPlainText(text)
        self.btn_go.setEnabled(True)
        self.btn_folder.setEnabled(True)

    def _failed(self, err: str) -> None:
        self.out.setPlainText("Não foi possível converter:\n\n" + err)
        self.btn_go.setEnabled(True)

    def _open_folder(self) -> None:
        folder = str(Path(self.ed_epub.text()).parent)
        if os.path.isdir(folder):
            os.startfile(folder)  # noqa: S606 - pasta escolhida pelo usuário

    def reject(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            QMessageBox.information(self, "Converter DOCX em EPUB", "Aguarde o fim da conversão.")
            return
        super().reject()
