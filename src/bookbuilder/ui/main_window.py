"""Janela principal: quatro etapas em sequência, menu Arquivo e estado do
projeto (salvar/abrir/retomar)."""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QButtonGroup,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, paths
from .. import project as projmod
from ..model import PageStatus, Project
from .step_export import ExportStep
from .step_images import ImagesStep
from .step_process import ProcessStep
from .step_review import ReviewStep

STEPS = ["1. Escolher imagens", "2. Processar", "3. Revisar", "4. Exportar"]


class AppState:
    def __init__(self):
        self.project: Project | None = None
        self.store: projmod.ProjectStore | None = None
        self.report = None
        self.dirty_pages: set[str] = set()
        self.language_changed = False
        self.unsaved = False

    def ensure_store(self, parent) -> bool:
        """Garante uma pasta de projeto (necessária para processar e retomar)."""
        if self.store is not None:
            return True
        proj = self.project
        base = Path.home() / "Documents" / "BookBuilder"
        base.mkdir(parents=True, exist_ok=True)
        src = Path(proj.source_folder)
        suggestion = base / f"{(src.stem if src.suffix.lower() == '.pdf' else src.name) or 'projeto'}{projmod.SUFFIX}"
        path, _ = QFileDialog.getSaveFileName(parent, "Onde salvar o projeto (para poder continuar depois)?",
                                              str(suggestion), f"Projeto BookBuilder (*{projmod.SUFFIX})")
        if not path:
            return False
        self.store = projmod.ProjectStore(path)
        self.save()
        return True

    def save(self) -> None:
        if self.store is None or self.project is None:
            return
        dirty = [p for p in self.project.pages if p.uid in self.dirty_pages]
        self.store.save(self.project, pages=dirty)
        self.dirty_pages.clear()
        self.unsaved = False


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.state = AppState()
        self.setWindowTitle(f"BookBuilder {__version__} — imagens de páginas para DOCX e EPUB")
        self.resize(1400, 900)
        self.qsettings = QSettings("BookBuilder", "BookBuilder")

        self.images = ImagesStep(self.state)
        self.process = ProcessStep(self.state)
        self.review = ReviewStep(self.state)
        self.export = ExportStep(self.state)
        self.stack = QStackedWidget()
        for w in (self.images, self.process, self.review, self.export):
            self.stack.addWidget(w)

        nav = QHBoxLayout()
        self.nav_group = QButtonGroup(self)
        self.nav_buttons: list[QPushButton] = []
        for i, name in enumerate(STEPS):
            b = QPushButton(name)
            b.setCheckable(True)
            b.setMinimumHeight(34)
            self.nav_group.addButton(b, i)
            self.nav_buttons.append(b)
            nav.addWidget(b)
        self.nav_group.idClicked.connect(self.go)
        nav.addStretch(1)
        self.lbl_project = QLabel("")
        nav.addWidget(self.lbl_project)

        central = QWidget()
        lay = QVBoxLayout(central)
        lay.addLayout(nav)
        lay.addWidget(self.stack, 1)
        self.setCentralWidget(central)
        self.setAcceptDrops(True)

        self.images.open_folder_requested.connect(self.new_from_folder)
        self.images.next_requested.connect(lambda: self.go(1))
        self.images.changed.connect(self._changed)
        self.process.next_requested.connect(lambda: self.go(2))
        self.process.finished.connect(self._processing_finished)
        self.process.project_changed.connect(self._changed)
        self.review.next_requested.connect(lambda: self.go(3))
        self.review.changed.connect(self._changed)
        self.review.reanalyze_requested.connect(self._reanalyze)
        self.review.reprocess_page_requested.connect(self._reprocess_page)

        self._build_menu()
        self.autosave = QTimer(self)
        self.autosave.setInterval(60_000)
        self.autosave.timeout.connect(self._autosave)
        self.autosave.start()
        self.go(0)
        QTimer.singleShot(200, self._check_components)

    # ------------------------------------------------------------------
    def _build_menu(self) -> None:
        m = self.menuBar().addMenu("&Arquivo")
        a_new = QAction("Nova conversão a partir de uma pasta…", self)
        a_new.setShortcut(QKeySequence.StandardKey.New)
        a_new.triggered.connect(self._ask_folder)
        a_open = QAction("Abrir projeto…", self)
        a_open.setShortcut(QKeySequence.StandardKey.Open)
        a_open.triggered.connect(self.open_project)
        a_save = QAction("Salvar projeto", self)
        a_save.setShortcut(QKeySequence.StandardKey.Save)
        a_save.triggered.connect(self.save_project)
        a_saveas = QAction("Salvar projeto como…", self)
        a_saveas.triggered.connect(self.save_project_as)
        a_quit = QAction("Sair", self)
        a_quit.triggered.connect(self.close)
        for a in (a_new, a_open, a_save, a_saveas):
            m.addAction(a)
        self.recent_menu = m.addMenu("Projetos recentes")
        self._fill_recent()
        m.addSeparator()
        m.addAction(a_quit)
        h = self.menuBar().addMenu("A&juda")
        a_about = QAction("Sobre e limitações", self)
        a_about.triggered.connect(self._about)
        h.addAction(a_about)

    def _fill_recent(self) -> None:
        self.recent_menu.clear()
        for p in self.qsettings.value("recentes", [], list) or []:
            if os.path.isdir(p):
                a = QAction(p, self)
                a.triggered.connect(lambda _=False, path=p: self.open_project(path))
                self.recent_menu.addAction(a)
        self.recent_menu.setEnabled(bool(self.recent_menu.actions()))

    def _remember(self, folder: str) -> None:
        rec = [p for p in (self.qsettings.value("recentes", [], list) or []) if p != folder]
        self.qsettings.setValue("recentes", [folder] + rec[:7])
        self._fill_recent()

    def _check_components(self) -> None:
        missing = paths.missing_components()
        if missing:
            QMessageBox.warning(
                self, "Componentes ausentes",
                "Faltam componentes necessários ao OCR:\n\n• " + "\n• ".join(missing)
                + "\n\nExecute “instalar_componentes.bat” na pasta do BookBuilder (requer internet uma única vez).")

    def _about(self) -> None:
        QMessageBox.information(
            self, "Sobre o BookBuilder",
            "BookBuilder converte imagens de páginas em DOCX e EPUB editáveis.\n\n"
            "Tudo é processado neste computador: OCR Tesseract 5 (português e inglês) e modelo de layout "
            "PP-DocLayout V3, sem internet e sem serviços pagos.\n\n"
            "Limitações:\n"
            "• Fontes e tamanhos são estimativas; quando a fonte original não está instalada, usa-se a mais "
            "parecida e isso é informado.\n"
            "• Texto encoberto na imagem (avisos, botões) não pode ser recuperado: aparece como “[…]”.\n"
            "• Tabelas sem linhas visíveis e diagramas podem virar imagens.\n"
            "• Leitores de EPUB podem trocar a fonte e o tamanho definidos.\n"
            "• A separação de cabeçalhos e rodapés é estrutural no DOCX; outros programas podem tratá-la de "
            "formas diferentes.")

    # ------------------------------------------------------------------
    def go(self, i: int) -> None:
        if self.state.project is None and i > 0:
            i = 0
        if i == 2:
            self.review.reload()
        if i == 3:
            self.export.load()
        if i == 1:
            self.process.refresh()
        if self.stack.currentIndex() == 3 and i != 3:
            self.export.store_options()
        self.stack.setCurrentIndex(i)
        self.nav_buttons[i].setChecked(True)
        self._update_nav()

    def _update_nav(self) -> None:
        proj = self.state.project
        has = proj is not None
        done = has and any(p.status == PageStatus.DONE for p in proj.pages)
        analyzed = has and bool(proj.styles)
        self.nav_buttons[1].setEnabled(has)
        self.nav_buttons[2].setEnabled(bool(done and analyzed))
        self.nav_buttons[3].setEnabled(bool(done and analyzed))
        if self.state.store is not None:
            self.lbl_project.setText(f"Projeto: {self.state.store.folder.name}" + (" *" if self.state.unsaved else ""))
        elif has:
            self.lbl_project.setText("Projeto ainda não salvo")
        else:
            self.lbl_project.setText("")

    def _changed(self) -> None:
        self.state.unsaved = True
        self._update_nav()

    def _autosave(self) -> None:
        if self.state.unsaved and self.state.store is not None and not self.process.is_busy():
            self.state.save()
            self._update_nav()

    def _ask_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Escolha a pasta com as imagens das páginas")
        if d:
            self.new_from_folder(d)

    def new_from_folder(self, folder: str) -> None:
        if not self._confirm_discard():
            return
        try:
            proj = projmod.new_project(folder)
        except Exception as e:  # noqa: BLE001 - pasta inacessível, PDF danificado ou protegido
            QMessageBox.warning(self, "Abrir", f"Não foi possível ler “{folder}”: {e}")
            return
        if not proj.pages:
            QMessageBox.information(self, "Pasta", "Nenhuma imagem (PNG, JPG, TIFF, BMP, WebP) ou PDF encontrado.")
            return
        self.state.project = proj
        self.state.store = None
        self.state.report = None
        self.state.unsaved = True
        self.images.reload()
        self.go(0)

    def open_project(self, path: str | None = None) -> None:
        if not self._confirm_discard():
            return
        if not path:
            path = QFileDialog.getExistingDirectory(self, f"Abra a pasta do projeto (*{projmod.SUFFIX})",
                                                    str(Path.home() / "Documents" / "BookBuilder"))
        if not path:
            return
        store = projmod.ProjectStore(path)
        if not store.exists():
            QMessageBox.warning(self, "Abrir projeto", "Esta pasta não contém um projeto do BookBuilder.")
            return
        try:
            proj = store.load()
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Abrir projeto", f"Não foi possível abrir o projeto:\n{e}")
            return
        self.state.project, self.state.store = proj, store
        self.state.report = None
        self.state.unsaved = False
        self._remember(str(store.folder))
        self.images.reload()
        changed = [p for p in proj.active_pages() if p.status == PageStatus.DONE and projmod.needs_processing(p)]
        if changed:
            QMessageBox.information(self, "Projeto", f"{len(changed)} imagens mudaram desde o processamento e serão processadas de novo.")
        if proj.styles:
            # Reconstrói o relatório da análise (rápido) para revisar/exportar.
            self.process.run_analysis()
        self.go(1)

    def save_project(self) -> None:
        if self.state.project is None:
            return
        if self.state.store is None:
            if self.state.ensure_store(self):
                self._remember(str(self.state.store.folder))
        else:
            self.state.save()
        self._update_nav()

    def save_project_as(self) -> None:
        if self.state.project is None:
            return
        old = self.state.store
        self.state.store = None
        if self.state.ensure_store(self):
            # Copia o cache de páginas e figuras para o novo projeto.
            if old is not None:
                import shutil

                for sub in (projmod.PAGES_DIR, projmod.FIGURES_DIR):
                    src = old.folder / sub
                    if src.is_dir():
                        shutil.copytree(src, self.state.store.folder / sub, dirs_exist_ok=True)
            self.state.store.save(self.state.project)
            self._remember(str(self.state.store.folder))
        else:
            self.state.store = old
        self._update_nav()

    def _confirm_discard(self) -> bool:
        if self.state.project is None or not self.state.unsaved:
            return True
        if self.state.store is not None:
            self.state.save()
            return True
        r = QMessageBox.question(self, "Projeto não salvo", "Descartar o projeto atual, que ainda não foi salvo?")
        return r == QMessageBox.StandardButton.Yes

    def _processing_finished(self) -> None:
        self._update_nav()
        if self.state.store is not None:
            self._remember(str(self.state.store.folder))

    def _reanalyze(self) -> None:
        self.state.save()
        self.go(1)
        self.process.run_analysis()

    def _reprocess_page(self, uid: str) -> None:
        proj = self.state.project
        p = proj.page_by_uid(uid) if proj else None
        if p is None:
            return
        p.clear_results()
        self.state.dirty_pages.add(uid)
        self.state.save()
        self.go(1)
        self.process.start()

    def closeEvent(self, ev) -> None:  # noqa: N802
        if self.process.is_busy():
            r = QMessageBox.question(self, "Processamento em andamento",
                                     "O processamento será interrompido; as páginas concluídas ficam salvas. Sair?")
            if r != QMessageBox.StandardButton.Yes:
                ev.ignore()
                return
            if self.process.worker is not None:
                self.process.worker.cancel()
                self.process.worker.wait(120_000)
            if self.process.analysis is not None:
                self.process.analysis.wait(120_000)
        if self.state.project is not None and self.state.unsaved:
            if self.state.store is not None:
                self.state.save()
            else:
                r = QMessageBox.question(self, "Sair", "O projeto não foi salvo. Salvar antes de sair?",
                                         QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                                         | QMessageBox.StandardButton.Cancel)
                if r == QMessageBox.StandardButton.Cancel:
                    ev.ignore()
                    return
                if r == QMessageBox.StandardButton.Yes and not self.state.ensure_store(self):
                    ev.ignore()
                    return
        ev.accept()

    def dragEnterEvent(self, ev):  # noqa: N802
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    def dropEvent(self, ev):  # noqa: N802
        self.images.dropEvent(ev)


