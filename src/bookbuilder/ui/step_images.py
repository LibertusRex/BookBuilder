"""Etapa 1 — escolher a pasta, conferir e ordenar as páginas.

Miniaturas carregadas em segundo plano; arrastar para reordenar; girar e
excluir sem tocar nos arquivos originais."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QThreadPool, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..imageio import natural_key
from .workers import ThumbSignals, ThumbTask

THUMB = 170
LANGS = [("por", "Português"), ("eng", "Inglês"), ("por+eng", "Português e inglês (documento misto)")]


class ImagesStep(QWidget):
    open_folder_requested = Signal(str)
    next_requested = Signal()
    changed = Signal()

    def __init__(self, state):
        super().__init__()
        self.state = state
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        self.signals = ThumbSignals()
        self.signals.done.connect(self._thumb_ready)
        self._pending: set[str] = set()
        self._placeholder = self._make_placeholder()

        self.setAcceptDrops(True)
        top = QHBoxLayout()
        self.btn_open = QPushButton("Abrir pasta de imagens…")
        self.btn_open.setMinimumHeight(36)
        self.btn_open.clicked.connect(self._choose_folder)
        self.lbl_folder = QLabel("Nenhuma pasta aberta. Você também pode arrastar uma pasta para esta janela "
                                 "(ou um arquivo .docx, para convertê-lo em EPUB).")
        self.lbl_folder.setWordWrap(True)
        self.btn_docx = QPushButton("Converter DOCX em EPUB…")
        self.btn_docx.setMinimumHeight(36)
        self.btn_docx.setToolTip("Converte um documento do Word (por exemplo, o livro já revisado) em EPUB")
        self.btn_docx.clicked.connect(lambda: self._docx_to_epub(""))
        top.addWidget(self.btn_open)
        top.addWidget(self.lbl_folder, 1)
        top.addWidget(self.btn_docx)

        tools = QHBoxLayout()
        self.btn_left = QPushButton("↺ Girar à esquerda")
        self.btn_right = QPushButton("↻ Girar à direita")
        self.btn_excl = QPushButton("Excluir / incluir na conversão")
        self.btn_sort = QPushButton("Restaurar ordem natural")
        self.btn_left.clicked.connect(lambda: self._rotate(-90))
        self.btn_right.clicked.connect(lambda: self._rotate(90))
        self.btn_excl.clicked.connect(self._toggle_exclude)
        self.btn_sort.clicked.connect(self._natural_order)
        for b in (self.btn_left, self.btn_right, self.btn_excl, self.btn_sort):
            tools.addWidget(b)
        tools.addStretch(1)
        tools.addWidget(QLabel("Idioma do texto:"))
        self.cmb_lang = QComboBox()
        for code, label in LANGS:
            self.cmb_lang.addItem(label, code)
        self.cmb_lang.currentIndexChanged.connect(self._lang_changed)
        tools.addWidget(self.cmb_lang)

        self.list = QListWidget()
        self.list.setViewMode(QListWidget.ViewMode.IconMode)
        self.list.setIconSize(QSize(THUMB, THUMB))
        self.list.setGridSize(QSize(THUMB + 30, THUMB + 48))
        self.list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list.setMovement(QListWidget.Movement.Snap)
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.setWordWrap(True)
        self.list.setUniformItemSizes(True)
        self.list.model().rowsMoved.connect(self._rows_moved)
        self.list.verticalScrollBar().valueChanged.connect(lambda _: self._load_visible())

        bottom = QHBoxLayout()
        self.lbl_count = QLabel("")
        self.btn_next = QPushButton("Próximo: processar  →")
        self.btn_next.setMinimumHeight(36)
        self.btn_next.clicked.connect(self.next_requested.emit)
        bottom.addWidget(self.lbl_count, 1)
        bottom.addWidget(QLabel("Dica: arraste as miniaturas para mudar a ordem. As imagens originais nunca são alteradas."))
        bottom.addWidget(self.btn_next)

        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addLayout(tools)
        lay.addWidget(self.list, 1)
        lay.addLayout(bottom)

    # ------------------------------------------------------------------
    def _make_placeholder(self) -> QIcon:
        pm = QPixmap(THUMB, THUMB)
        pm.fill(QColor("#dddddd"))
        return QIcon(pm)

    def _choose_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Escolha a pasta com as imagens das páginas")
        if d:
            self.open_folder_requested.emit(d)

    def _docx_to_epub(self, path: str) -> None:
        from .docx_dialog import DocxToEpubDialog

        DocxToEpubDialog(self, path).exec()

    def dragEnterEvent(self, ev):  # noqa: N802
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    def dropEvent(self, ev):  # noqa: N802
        for url in ev.mimeData().urls():
            path = url.toLocalFile()
            import os

            if os.path.isdir(path):
                self.open_folder_requested.emit(path)
                return
            if os.path.isfile(path) and path.lower().endswith(".docx"):
                self._docx_to_epub(path)
                return
            if os.path.isfile(path):
                self.open_folder_requested.emit(os.path.dirname(path))
                return

    # ------------------------------------------------------------------
    def reload(self) -> None:
        proj = self.state.project
        self.list.clear()
        if proj is None:
            self.lbl_folder.setText("Nenhuma pasta aberta. Você também pode arrastar uma pasta para esta janela.")
            self._update_count()
            return
        self.lbl_folder.setText(f"Pasta: {proj.source_folder}")
        idx = self.cmb_lang.findData(proj.settings.language)
        self.cmb_lang.blockSignals(True)
        self.cmb_lang.setCurrentIndex(max(0, idx))
        self.cmb_lang.blockSignals(False)
        for p in proj.pages:
            it = QListWidgetItem(self._placeholder, p.source.display_name)
            it.setData(Qt.ItemDataRole.UserRole, p.uid)
            it.setSizeHint(QSize(THUMB + 24, THUMB + 44))
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsDragEnabled)
            self._style_item(it, p)
            self.list.addItem(it)
        self._update_count()
        self._load_visible()

    def _style_item(self, it: QListWidgetItem, p) -> None:
        name = p.source.display_name
        extra = []
        if p.rotation:
            extra.append(f"girada {p.rotation}°")
        if p.excluded:
            extra.append("EXCLUÍDA")
        it.setText(name + ("\n" + ", ".join(extra) if extra else ""))
        it.setForeground(QColor("#999999") if p.excluded else QColor("#000000"))

    def _load_visible(self) -> None:
        proj = self.state.project
        if proj is None:
            return
        vp = self.list.viewport().rect()
        by_uid = {p.uid: p for p in proj.pages}
        for i in range(self.list.count()):
            it = self.list.item(i)
            if not self.list.visualItemRect(it).intersects(vp.adjusted(0, -400, 0, 800)):
                continue
            p = by_uid.get(it.data(Qt.ItemDataRole.UserRole))
            if p is None:
                continue
            key = f"{p.uid}:{p.rotation}"
            if it.data(Qt.ItemDataRole.UserRole + 1) == key or key in self._pending:
                continue
            self._pending.add(key)
            self.pool.start(ThumbTask(p.source, p.rotation, THUMB, self.signals))

    def _thumb_ready(self, key: str, img: QImage) -> None:
        self._pending.discard(key)
        uid = key.split(":")[0]
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.data(Qt.ItemDataRole.UserRole) == uid:
                if not img.isNull():
                    it.setIcon(QIcon(QPixmap.fromImage(img)))
                it.setData(Qt.ItemDataRole.UserRole + 1, key)
                break

    def showEvent(self, ev):  # noqa: N802
        super().showEvent(ev)
        self._load_visible()

    def resizeEvent(self, ev):  # noqa: N802
        super().resizeEvent(ev)
        self._load_visible()

    # ------------------------------------------------------------------
    def _selected_pages(self):
        proj = self.state.project
        uids = {it.data(Qt.ItemDataRole.UserRole) for it in self.list.selectedItems()}
        return [p for p in proj.pages if p.uid in uids] if proj else []

    def _rotate(self, delta: int) -> None:
        for p in self._selected_pages():
            p.rotation = (p.rotation + delta) % 360
            p.clear_results()  # precisa ser processada de novo
        self._refresh_items()
        self.changed.emit()

    def _toggle_exclude(self) -> None:
        pages = self._selected_pages()
        if not pages:
            return
        target = not all(p.excluded for p in pages)
        for p in pages:
            p.excluded = target
        self._refresh_items()
        self.changed.emit()

    def _refresh_items(self) -> None:
        proj = self.state.project
        by_uid = {p.uid: p for p in proj.pages}
        for i in range(self.list.count()):
            it = self.list.item(i)
            p = by_uid[it.data(Qt.ItemDataRole.UserRole)]
            self._style_item(it, p)
        self._update_count()
        self._load_visible()

    def _rows_moved(self, *args) -> None:
        proj = self.state.project
        if proj is None:
            return
        by_uid = {p.uid: p for p in proj.pages}
        proj.pages = [by_uid[self.list.item(i).data(Qt.ItemDataRole.UserRole)] for i in range(self.list.count())]
        self.changed.emit()

    def _natural_order(self) -> None:
        proj = self.state.project
        if proj is None:
            return
        proj.pages.sort(key=lambda p: (natural_key(p.source.path), p.source.frame))
        self.reload()
        self.changed.emit()

    def _lang_changed(self) -> None:
        proj = self.state.project
        if proj is None:
            return
        new = self.cmb_lang.currentData()
        if new != proj.settings.language:
            proj.settings.language = new
            self.state.language_changed = True
            self.changed.emit()

    def _update_count(self) -> None:
        proj = self.state.project
        if proj is None:
            self.lbl_count.setText("")
            self.btn_next.setEnabled(False)
            return
        n = len(proj.pages)
        ex = sum(p.excluded for p in proj.pages)
        self.lbl_count.setText(f"{n} páginas — {n - ex} serão convertidas" + (f", {ex} excluídas" if ex else ""))
        self.btn_next.setEnabled(n - ex > 0)
