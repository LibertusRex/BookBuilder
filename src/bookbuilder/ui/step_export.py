"""Etapa 4 — exportar DOCX e/ou EPUB, com metadados e relatório final."""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from ..export import report as rpt
from .workers import ExportWorker

LANG_CODES = [("pt-BR", "Português (Brasil)"), ("pt-PT", "Português (Portugal)"), ("en", "Inglês"), ("es", "Espanhol")]
INVALID = '<>:"/\\|?*'


class ExportStep(QWidget):
    def __init__(self, state):
        super().__init__()
        self.state = state
        self.worker: ExportWorker | None = None
        self.last_paths: list[str] = []

        g1 = QGroupBox("Arquivos")
        f1 = QFormLayout(g1)
        row = QHBoxLayout()
        self.ed_dir = QLineEdit()
        btn_dir = QPushButton("Escolher…")
        btn_dir.clicked.connect(self._choose_dir)
        row.addWidget(self.ed_dir, 1)
        row.addWidget(btn_dir)
        self.ed_name = QLineEdit()
        fmts = QHBoxLayout()
        self.chk_docx = QCheckBox("DOCX (Word)")
        self.chk_epub = QCheckBox("EPUB 3")
        fmts.addWidget(self.chk_docx)
        fmts.addWidget(self.chk_epub)
        fmts.addStretch(1)
        f1.addRow("Pasta de destino:", row)
        f1.addRow("Nome dos arquivos:", self.ed_name)
        f1.addRow("Gerar:", fmts)

        g2 = QGroupBox("Dados do livro (usados no EPUB e nas propriedades do DOCX)")
        f2 = QFormLayout(g2)
        self.ed_title = QLineEdit()
        self.ed_author = QLineEdit()
        self.ed_publisher = QLineEdit()
        self.cmb_lang = QComboBox()
        for code, label in LANG_CODES:
            self.cmb_lang.addItem(label, code)
        crow = QHBoxLayout()
        self.ed_cover = QLineEdit()
        self.ed_cover.setPlaceholderText("opcional")
        btn_cover = QPushButton("Escolher imagem…")
        btn_cover.clicked.connect(self._choose_cover)
        crow.addWidget(self.ed_cover, 1)
        crow.addWidget(btn_cover)
        f2.addRow("Título:", self.ed_title)
        f2.addRow("Autor:", self.ed_author)
        f2.addRow("Editora:", self.ed_publisher)
        f2.addRow("Idioma:", self.cmb_lang)
        f2.addRow("Capa:", crow)

        g3 = QGroupBox("Opções do DOCX")
        v3 = QVBoxLayout(g3)
        self.rb_continuous = QRadioButton("Texto contínuo (recomendado para edição)")
        self.rb_pages = QRadioButton("Preservar as quebras das páginas originais")
        self.chk_hf = QCheckBox("Cabeçalhos e rodapés nas áreas próprias do Word (fora do corpo do texto)")
        self.chk_hl = QCheckBox("Destacar em amarelo os trechos de baixa confiança")
        for w in (self.rb_continuous, self.rb_pages, self.chk_hf, self.chk_hl):
            v3.addWidget(w)

        g4 = QGroupBox("Opções do EPUB")
        v4 = QVBoxLayout(g4)
        self.chk_epub_low = QCheckBox("Marcar trechos de baixa confiança")
        v4.addWidget(self.chk_epub_low)
        info = QLabel("O EPUB é de texto fluido: cabeçalhos, rodapés e números de página impressos são omitidos, e o "
                      "leitor faz a própria paginação. As fontes e os tamanhos estimados vão no CSS, mas aplicativos "
                      "de leitura podem substituí-los pelas preferências do leitor.")
        info.setWordWrap(True)
        v4.addWidget(info)

        self.btn_export = QPushButton("Exportar")
        self.btn_export.setMinimumHeight(40)
        self.btn_export.clicked.connect(self.export)
        self.btn_open_dir = QPushButton("Abrir pasta de destino")
        self.btn_open_dir.clicked.connect(self._open_dir)
        self.lbl_status = QLabel("")
        self.txt_report = QPlainTextEdit()
        self.txt_report.setReadOnly(True)

        left = QVBoxLayout()
        for g in (g1, g2, g3, g4):
            left.addWidget(g)
        brow = QHBoxLayout()
        brow.addWidget(self.btn_export)
        brow.addWidget(self.btn_open_dir)
        left.addLayout(brow)
        left.addWidget(self.lbl_status)
        left.addStretch(1)
        right = QVBoxLayout()
        right.addWidget(QLabel("Relatório:"))
        right.addWidget(self.txt_report, 1)
        lay = QHBoxLayout(self)
        lw = QWidget()
        lw.setLayout(left)
        lay.addWidget(lw, 1)
        rw = QWidget()
        rw.setLayout(right)
        lay.addWidget(rw, 1)

    def load(self) -> None:
        proj = self.state.project
        if proj is None:
            return
        e = proj.export
        self.ed_dir.setText(e.output_dir)
        self.ed_name.setText(e.base_name)
        self.chk_docx.setChecked(e.make_docx)
        self.chk_epub.setChecked(e.make_epub)
        self.ed_title.setText(e.title)
        self.ed_author.setText(e.author)
        self.ed_publisher.setText(e.publisher)
        self.cmb_lang.setCurrentIndex(max(0, self.cmb_lang.findData(e.language)))
        self.ed_cover.setText(e.cover_image)
        self.rb_pages.setChecked(e.docx_page_breaks)
        self.rb_continuous.setChecked(not e.docx_page_breaks)
        self.chk_hf.setChecked(e.docx_headers_footers)
        self.chk_hl.setChecked(e.highlight_low_conf)
        self.chk_epub_low.setChecked(e.epub_mark_low_conf)
        if self.state.report is not None:
            self.txt_report.setPlainText(rpt.text_report(proj, self.state.report))

    def store_options(self) -> None:
        proj = self.state.project
        if proj is None:
            return
        e = proj.export
        e.output_dir = self.ed_dir.text().strip()
        e.base_name = self.ed_name.text().strip()
        e.make_docx = self.chk_docx.isChecked()
        e.make_epub = self.chk_epub.isChecked()
        e.title = self.ed_title.text().strip()
        e.author = self.ed_author.text().strip()
        e.publisher = self.ed_publisher.text().strip()
        e.language = self.cmb_lang.currentData()
        e.cover_image = self.ed_cover.text().strip()
        e.docx_page_breaks = self.rb_pages.isChecked()
        e.docx_headers_footers = self.chk_hf.isChecked()
        e.highlight_low_conf = self.chk_hl.isChecked()
        e.epub_mark_low_conf = self.chk_epub_low.isChecked()

    def _choose_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Pasta de destino", self.ed_dir.text())
        if d:
            self.ed_dir.setText(d)

    def _choose_cover(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, "Imagem da capa", "", "Imagens (*.jpg *.jpeg *.png)")
        if f:
            self.ed_cover.setText(f)

    def _open_dir(self) -> None:
        d = self.ed_dir.text().strip()
        if d and os.path.isdir(d):
            QDesktopServices.openUrl(QUrl.fromLocalFile(d))

    def export(self) -> None:
        proj = self.state.project
        if proj is None or (self.worker and self.worker.isRunning()):
            return
        self.store_options()
        e = proj.export
        if not (e.make_docx or e.make_epub):
            QMessageBox.information(self, "Exportar", "Marque DOCX, EPUB ou ambos.")
            return
        if not e.output_dir or not os.path.isdir(e.output_dir):
            QMessageBox.warning(self, "Exportar", "Escolha uma pasta de destino existente.")
            return
        name = e.base_name or "documento"
        if any(ch in name for ch in INVALID):
            QMessageBox.warning(self, "Exportar", f"O nome não pode conter os caracteres {INVALID}")
            return
        if self.state.report is None:
            QMessageBox.information(self, "Exportar", "Processe as páginas (etapa 2) antes de exportar.")
            return
        out = Path(e.output_dir)
        docx_path = str(out / f"{name}.docx") if e.make_docx else None
        epub_path = str(out / f"{name}.epub") if e.make_epub else None
        existing = [p for p in (docx_path, epub_path) if p and os.path.exists(p)]
        if existing:
            r = QMessageBox.question(self, "Substituir arquivos?",
                                     "Já existem arquivos com esse nome:\n" + "\n".join(existing) + "\n\nSubstituir?")
            if r != QMessageBox.StandardButton.Yes:
                return
        self.state.save()
        self.btn_export.setEnabled(False)
        self.lbl_status.setText("Exportando…")
        self.worker = ExportWorker(proj, self.state.store, docx_path, epub_path, self.state.report)
        self.worker.message.connect(self.lbl_status.setText)
        self.worker.finished_ok.connect(lambda res: self._done(res, out, name))
        self.worker.failed.connect(self._failed)
        self.worker.finished.connect(lambda: self.btn_export.setEnabled(True))
        self.worker.start()

    def _done(self, res: dict, out: Path, name: str) -> None:
        text = res["report"]
        self.txt_report.setPlainText(text)
        rep_path = out / f"{name}-relatorio.html"
        try:
            rep_path.write_text(rpt.html_report(text), encoding="utf-8")
        except OSError:
            pass
        files = []
        if "docx" in res:
            files.append(res["docx"].path)
        if "epub" in res:
            files.append(res["epub"].path)
        self.last_paths = files
        self.lbl_status.setText("Pronto! Arquivos gerados:\n" + "\n".join(files) + f"\nRelatório: {rep_path}")
        self.state.save()

    def _failed(self, msg: str) -> None:
        self.lbl_status.setText("Falha na exportação.")
        QMessageBox.critical(self, "Falha na exportação", msg[:2500])
