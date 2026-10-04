"""Visualizador da imagem original com as regiões identificadas marcadas
por cor (função de cada bloco). Clique numa região para selecioná-la."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPen, QPixmap, QWheelEvent
from PySide6.QtWidgets import QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsScene, QGraphicsView

from .. import imageio
from ..model import ROLE_COLORS, Block, Page
from ..ocr import preprocess


def page_pixmap(page: Page) -> QPixmap:
    rgb = imageio.load_rgb(page.source, (page.rotation + page.auto_rotation) % 360)
    if page.deskew:
        rgb = preprocess.work_view(rgb, page.deskew, page.inverted)
    rgb = np.ascontiguousarray(rgb)
    h, w = rgb.shape[:2]
    img = QImage(rgb.data, w, h, w * 3, QImage.Format.Format_RGB888).copy()
    return QPixmap.fromImage(img)


class _RegionItem(QGraphicsRectItem):
    def __init__(self, block: Block, view: "PageView"):
        x0, y0, x1, y1 = block.bbox
        super().__init__(QRectF(x0, y0, x1 - x0, y1 - y0))
        self.block = block
        self.view = view
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.set_selected(False)
        tip = f"{block.role.label}"
        if block.heading_level and block.role.value == "titulo":
            tip += f" (nível {block.heading_level})"
        if block.flags:
            tip += "\n" + ", ".join(block.flags)
        self.setToolTip(tip)

    def set_selected(self, sel: bool) -> None:
        r, g, b = ROLE_COLORS[self.block.role]
        pen = QPen(QColor(r, g, b), 6 if sel else 3)
        if self.block.role.is_running:
            pen.setStyle(Qt.PenStyle.DashLine)
        self.setPen(pen)
        self.setBrush(QBrush(QColor(r, g, b, 70 if sel else 28)))

    def mousePressEvent(self, ev):  # noqa: N802
        self.view.block_clicked.emit(self.block.id)
        ev.accept()


class PageView(QGraphicsView):
    block_clicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setBackgroundBrush(QColor("#2b2b2b"))
        self._items: dict[str, _RegionItem] = {}
        self._page: Page | None = None
        self.show_regions = True
        self._auto_fit = True  # ajusta ao redimensionar, até o usuário dar zoom

    def set_page(self, page: Page | None, pixmap: QPixmap | None) -> None:
        sc = self.scene()
        sc.clear()
        self._mark = None
        self._items = {}
        self._page = page
        if page is None or pixmap is None:
            return
        sc.addItem(QGraphicsPixmapItem(pixmap))
        sc.setSceneRect(QRectF(pixmap.rect()))
        for o in page.overlays:
            x0, y0, x1, y1 = o.bbox
            it = sc.addRect(QRectF(x0, y0, x1 - x0, y1 - y0), QPen(QColor(200, 60, 60), 3, Qt.PenStyle.DotLine),
                            QBrush(QColor(200, 60, 60, 40), Qt.BrushStyle.BDiagPattern))
            it.setToolTip("Sobreposição da tela (aviso/botão): o texto embaixo é desconhecido")
        self.refresh_regions()
        self._auto_fit = True
        self.fit()

    def refresh_regions(self) -> None:
        sc = self.scene()
        for it in self._items.values():
            sc.removeItem(it)
        self._items = {}
        if self._page is None or not self.show_regions:
            return
        for b in self._page.blocks:
            it = _RegionItem(b, self)
            sc.addItem(it)
            self._items[b.id] = it

    def select(self, block_id: str | None) -> None:
        for bid, it in self._items.items():
            it.set_selected(bid == block_id)
        if block_id in self._items:
            self.ensureVisible(self._items[block_id], 40, 40)

    def mark(self, bbox) -> None:
        """Destaca uma região (ex.: a palavra de uma divergência)."""
        sc = self.scene()
        if getattr(self, "_mark", None) is not None:
            try:
                sc.removeItem(self._mark)
            except RuntimeError:
                pass
            self._mark = None
        if not bbox:
            return
        x0, y0, x1, y1 = bbox
        pad = 6
        self._mark = sc.addRect(QRectF(x0 - pad, y0 - pad, x1 - x0 + 2 * pad, y1 - y0 + 2 * pad),
                                QPen(QColor(220, 20, 60), 5), QBrush(QColor(255, 230, 0, 70)))
        self._mark.setZValue(10)
        self.ensureVisible(self._mark, 80, 80)

    def wheelEvent(self, ev: QWheelEvent) -> None:  # noqa: N802
        if ev.modifiers() & Qt.KeyboardModifier.ControlModifier:
            f = 1.15 if ev.angleDelta().y() > 0 else 1 / 1.15
            self.scale(f, f)
            self._auto_fit = False
        else:
            super().wheelEvent(ev)

    def fit(self) -> None:
        self._auto_fit = True
        if self.scene().sceneRect().isValid():
            self.fitInView(self.scene().sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self, ev) -> None:  # noqa: N802
        super().resizeEvent(ev)
        if self._auto_fit:
            self.fit()

    def showEvent(self, ev) -> None:  # noqa: N802
        super().showEvent(ev)
        if self._auto_fit:
            self.fit()
