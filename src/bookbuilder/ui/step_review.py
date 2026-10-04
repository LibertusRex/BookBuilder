"""Etapa 3 — revisão: imagem original e resultado lado a lado, regiões
marcadas, editor de texto, funções dos blocos (com aplicação por página,
intervalo ou documento), estilos e escala tipográfica."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFontComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .. import review
from ..model import Block, Page, PageStatus, Role, Run, merge_runs
from ..export.report import flag_label
from ..typography import fonts, styles as stylemod
from .pageview import PageView, page_pixmap
from .preview import page_html

PAGE_SIZES = [
    ("Desconhecido", 0, 0), ("A4 (210 × 297 mm)", 210, 297), ("A5 (148 × 210 mm)", 148, 210),
    ("14 × 21 cm", 140, 210), ("16 × 23 cm", 160, 230), ("17 × 24 cm", 170, 240),
    ("Carta (216 × 279 mm)", 216, 279), ("Ofício (216 × 330 mm)", 216, 330),
]
SCALE_MODES = [("auto", "Automático"), ("dpi", "Pelo DPI"), ("page_size", "Pelo tamanho da página"),
               ("body_size", "Pelo tamanho do corpo do texto")]


class ReviewStep(QWidget):
    next_requested = Signal()
    reanalyze_requested = Signal()
    reprocess_page_requested = Signal(str)
    changed = Signal()

    def __init__(self, state):
        super().__init__()
        self.state = state
        self.page: Page | None = None
        self.block: Block | None = None
        self._pix_cache: dict[str, object] = {}

        # --- coluna esquerda: filtro + páginas
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.cmb_filter = QComboBox()
        for k, v in review.FILTERS.items():
            self.cmb_filter.addItem(v, k)
        self.cmb_filter.currentIndexChanged.connect(self.reload_pages)
        self.pages = QListWidget()
        self.pages.currentItemChanged.connect(self._page_selected)
        self.lbl_pages = QLabel("")
        ll.addWidget(QLabel("Mostrar:"))
        ll.addWidget(self.cmb_filter)
        ll.addWidget(self.pages, 1)
        ll.addWidget(self.lbl_pages)

        # --- centro: imagem | prévia
        self.view = PageView()
        self.view.block_clicked.connect(self.select_block)
        self.preview = QTextBrowser()
        self.preview.setOpenLinks(False)
        self.preview.anchorClicked.connect(lambda url: None)
        center = QSplitter(Qt.Orientation.Horizontal)
        vw = QWidget()
        vl = QVBoxLayout(vw)
        vl.setContentsMargins(0, 0, 0, 0)
        vrow = QHBoxLayout()
        vrow.addWidget(QLabel("Imagem original (clique numa região):"))
        vrow.addStretch(1)
        self.chk_regions = QCheckBox("Mostrar regiões")
        self.chk_regions.setChecked(True)
        self.chk_regions.toggled.connect(self._toggle_regions)
        btn_fit = QPushButton("Ajustar")
        btn_fit.clicked.connect(self.view.fit)
        vrow.addWidget(self.chk_regions)
        vrow.addWidget(btn_fit)
        vl.addLayout(vrow)
        vl.addWidget(self.view, 1)
        legend = QLabel(self._legend_html())
        legend.setWordWrap(True)
        vl.addWidget(legend)
        pw = QWidget()
        pl = QVBoxLayout(pw)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.addWidget(QLabel("Resultado (prévia com as fontes estimadas):"))
        pl.addWidget(self.preview, 1)
        center.addWidget(vw)
        center.addWidget(pw)
        center.setSizes([500, 500])

        # --- direita: abas
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_block_tab(), "Bloco")
        self.tabs.addTab(self._build_disputes_tab(), "Divergências")
        self.tabs.addTab(self._build_styles_tab(), "Estilos e fontes")
        self.tabs.addTab(self._build_page_tab(), "Página")

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(left)
        split.addWidget(center)
        split.addWidget(self.tabs)
        split.setSizes([220, 900, 420])

        bottom = QHBoxLayout()
        self.btn_reanalyze = QPushButton("Reanalisar documento (mantém suas correções)")
        self.btn_reanalyze.clicked.connect(self.reanalyze_requested.emit)
        bottom.addWidget(self.btn_reanalyze)
        bottom.addStretch(1)
        self.btn_next = QPushButton("Próximo: exportar  →")
        self.btn_next.setMinimumHeight(36)
        self.btn_next.clicked.connect(self.next_requested.emit)
        bottom.addWidget(self.btn_next)

        lay = QVBoxLayout(self)
        lay.addWidget(split, 1)
        lay.addLayout(bottom)

    # ------------------------------------------------------------------ abas
    def _legend_html(self) -> str:
        from ..model import ROLE_COLORS

        parts = []
        for r in (Role.BODY, Role.TITLE, Role.HEADER, Role.FOOTER, Role.PAGE_NUMBER, Role.FOOTNOTE, Role.FIGURE, Role.ARTIFACT):
            c = "#%02x%02x%02x" % ROLE_COLORS[r]
            parts.append(f"<span style='color:{c}'>■</span> {r.label}")
        return "<small>" + " &nbsp; ".join(parts) + " &nbsp; <span style='color:#c83c3c'>▨</span> Sobreposição</small>"

    def _build_block_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.lbl_block = QLabel("Selecione uma região na imagem.")
        self.lbl_block.setWordWrap(True)
        lay.addWidget(self.lbl_block)

        g = QGroupBox("Função do bloco")
        fl = QFormLayout(g)
        self.cmb_role = QComboBox()
        for r in Role:
            self.cmb_role.addItem(r.label, r.value)
        self.spin_level = QSpinBox()
        self.spin_level.setRange(1, 6)
        self.cmb_scope = QComboBox()
        for k, v in review.SCOPE_LABELS.items():
            self.cmb_scope.addItem(v, k)
        rng = QHBoxLayout()
        self.spin_from = QSpinBox()
        self.spin_to = QSpinBox()
        rng.addWidget(QLabel("de"))
        rng.addWidget(self.spin_from)
        rng.addWidget(QLabel("até"))
        rng.addWidget(self.spin_to)
        self.btn_role = QPushButton("Aplicar função")
        self.btn_role.clicked.connect(self._apply_role)
        fl.addRow("Função:", self.cmb_role)
        fl.addRow("Nível do título:", self.spin_level)
        fl.addRow("Aplicar a:", self.cmb_scope)
        fl.addRow("Intervalo de páginas:", rng)
        fl.addRow(self.btn_role)
        lay.addWidget(g)

        g2 = QGroupBox("Texto do bloco")
        tl = QVBoxLayout(g2)
        fmt = QHBoxLayout()
        for label, slot in (("N", self._fmt_bold), ("I", self._fmt_italic), ("S", self._fmt_underline),
                            ("x²", self._fmt_sup), ("x₂", self._fmt_sub)):
            b = QPushButton(label)
            b.setFixedWidth(36)
            b.clicked.connect(slot)
            fmt.addWidget(b)
        fmt.addStretch(1)
        tl.addLayout(fmt)
        self.editor = QTextEdit()
        self.editor.setAcceptRichText(False)
        tl.addWidget(self.editor, 1)
        row = QHBoxLayout()
        self.btn_save_text = QPushButton("Salvar texto")
        self.btn_save_text.clicked.connect(self._save_text)
        self.cmb_block_style = QComboBox()
        self.cmb_block_style.currentIndexChanged.connect(self._assign_style)
        row.addWidget(self.btn_save_text)
        row.addWidget(QLabel("Estilo:"))
        row.addWidget(self.cmb_block_style, 1)
        tl.addLayout(row)
        tl.addWidget(QLabel("<small>Trechos com fundo amarelo têm baixa confiança no OCR. "
                            "“[…]” marca texto encoberto na imagem original.</small>"))
        lay.addWidget(g2, 1)
        self.lst_flags = QListWidget()
        self.lst_flags.setMaximumHeight(90)
        lay.addWidget(QLabel("Avisos do bloco:"))
        lay.addWidget(self.lst_flags)
        return w

    def _build_styles_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        g = QGroupBox("Escala tipográfica (pixels → pontos)")
        fl = QFormLayout(g)
        self.cmb_scale = QComboBox()
        for k, v in SCALE_MODES:
            self.cmb_scale.addItem(v, k)
        self.spin_dpi = QDoubleSpinBox()
        self.spin_dpi.setRange(0, 2400)
        self.spin_dpi.setDecimals(0)
        self.spin_dpi.setSpecialValueText("do arquivo")
        self.cmb_pagesize = QComboBox()
        for name, wmm, hmm in PAGE_SIZES:
            self.cmb_pagesize.addItem(name, (wmm, hmm))
        self.spin_wmm = QDoubleSpinBox()
        self.spin_wmm.setRange(0, 1000)
        self.spin_wmm.setSuffix(" mm")
        self.cmb_pagesize.currentIndexChanged.connect(lambda: self.spin_wmm.setValue(self.cmb_pagesize.currentData()[0]))
        self.spin_body = QDoubleSpinBox()
        self.spin_body.setRange(5, 30)
        self.spin_body.setSingleStep(0.5)
        self.spin_body.setSuffix(" pt")
        self.lbl_scale = QLabel("")
        self.lbl_scale.setWordWrap(True)
        btn = QPushButton("Recalcular tamanhos")
        btn.clicked.connect(self._apply_scale)
        fl.addRow("Modo:", self.cmb_scale)
        fl.addRow("DPI das imagens:", self.spin_dpi)
        fl.addRow("Tamanho da página:", self.cmb_pagesize)
        fl.addRow("Largura da página:", self.spin_wmm)
        fl.addRow("Corpo do texto:", self.spin_body)
        fl.addRow(btn)
        fl.addRow(self.lbl_scale)
        lay.addWidget(g)
        lay.addWidget(QLabel("<small>Capturas de tela não têm tamanho físico: nelas o tamanho do corpo do texto "
                             "define a escala e as proporções entre títulos e texto são preservadas.</small>"))

        self.tbl_styles = QTableWidget(0, 6)
        self.tbl_styles.setHorizontalHeaderLabels(["Estilo", "Linhas", "Fonte estimada", "Semelhança", "Tamanho", "Situação"])
        self.tbl_styles.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tbl_styles.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tbl_styles.currentCellChanged.connect(self._style_selected)
        lay.addWidget(self.tbl_styles, 1)

        g2 = QGroupBox("Ajustar o estilo selecionado (vale para todos os blocos dele)")
        f2 = QFormLayout(g2)
        self.cmb_font = QFontComboBox()
        self.spin_size = QDoubleSpinBox()
        self.spin_size.setRange(4, 96)
        self.spin_size.setSingleStep(0.5)
        self.spin_size.setSuffix(" pt")
        self.chk_bold = QCheckBox("Negrito")
        self.chk_italic = QCheckBox("Itálico")
        self.btn_style = QPushButton("Aplicar ao estilo")
        self.btn_style.clicked.connect(self._apply_style)
        self.btn_style_reset = QPushButton("Voltar à estimativa")
        self.btn_style_reset.clicked.connect(self._reset_style)
        f2.addRow("Fonte:", self.cmb_font)
        f2.addRow("Tamanho:", self.spin_size)
        bi = QHBoxLayout()
        bi.addWidget(self.chk_bold)
        bi.addWidget(self.chk_italic)
        f2.addRow(bi)
        br = QHBoxLayout()
        br.addWidget(self.btn_style)
        br.addWidget(self.btn_style_reset)
        f2.addRow(br)
        lay.addWidget(g2)
        return w

    # ------------------------------------------------------------------ divergências
    def _build_disputes_tab(self) -> QWidget:
        from PySide6.QtGui import QKeySequence, QShortcut

        w = QWidget()
        lay = QVBoxLayout(w)
        self.lbl_disp_total = QLabel("")
        self.lbl_disp_total.setWordWrap(True)
        lay.addWidget(self.lbl_disp_total)
        lay.addWidget(QLabel("Divergências desta página:"))
        self.lst_disp = QListWidget()
        self.lst_disp.setMaximumHeight(140)
        self.lst_disp.currentRowChanged.connect(self._dispute_selected)
        lay.addWidget(self.lst_disp)
        self.lbl_disp_img = QLabel()
        self.lbl_disp_img.setMinimumHeight(110)
        self.lbl_disp_img.setStyleSheet("background:#ffffff; border:1px solid #888;")
        self.lbl_disp_img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(QLabel("Recorte da imagem original:"))
        lay.addWidget(self.lbl_disp_img)
        self.lbl_disp_tess = QLabel("")
        self.lbl_disp_surya = QLabel("")
        for lb in (self.lbl_disp_tess, self.lbl_disp_surya):
            lb.setWordWrap(True)
            lb.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            lay.addWidget(lb)
        self.chk_disp_all = QCheckBox("Aplicar a todas as divergências iguais no livro")
        lay.addWidget(self.chk_disp_all)
        row = QHBoxLayout()
        self.btn_use_tess = QPushButton("Usar Tesseract  (T)")
        self.btn_use_surya = QPushButton("Usar Surya  (S)")
        self.btn_use_tess.clicked.connect(lambda: self._resolve_dispute("tess"))
        self.btn_use_surya.clicked.connect(lambda: self._resolve_dispute("surya"))
        row.addWidget(self.btn_use_tess)
        row.addWidget(self.btn_use_surya)
        lay.addLayout(row)
        self.btn_disp_next = QPushButton("Próxima divergência no livro  ▶  (N)")
        self.btn_disp_next.clicked.connect(self._next_dispute)
        lay.addWidget(self.btn_disp_next)
        lay.addWidget(QLabel("<small>Para outra correção, edite o texto na aba “Bloco”.</small>"))
        lay.addStretch(1)
        for keyseq, fn in (("T", lambda: self._resolve_dispute("tess")), ("S", lambda: self._resolve_dispute("surya")),
                           ("N", self._next_dispute)):
            sc = QShortcut(QKeySequence(keyseq), w)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc.activated.connect(fn)
        return w

    def _page_disputes(self) -> list[tuple]:
        if self.page is None:
            return []
        return [(b, k) for b in self.page.ordered_blocks() for k in range(len(b.disputes))]

    def _refresh_disputes(self, select: int = 0) -> None:
        proj = self.state.project
        total = len(review.open_disputes(proj)) if proj else 0
        self.lbl_disp_total.setText(f"<b>{total}</b> divergências abertas no livro (trechos em amarelo em que "
                                    "Tesseract e Surya leram coisas diferentes e nem as regras nem o árbitro decidiram).")
        self.lst_disp.blockSignals(True)
        self.lst_disp.clear()
        for b, k in self._page_disputes():
            d = b.disputes[k]
            self.lst_disp.addItem(f"T: {d['tess'] or '∅'}   |   S: {d['surya'] or '∅'}")
        self.lst_disp.blockSignals(False)
        has = self.lst_disp.count() > 0
        for wdg in (self.btn_use_tess, self.btn_use_surya):
            wdg.setEnabled(has)
        self.btn_disp_next.setEnabled(total > 0)
        if has:
            self.lst_disp.setCurrentRow(min(select, self.lst_disp.count() - 1))
            self._dispute_selected(self.lst_disp.currentRow())
        else:
            self.lbl_disp_img.clear()
            self.lbl_disp_tess.setText("")
            self.lbl_disp_surya.setText("")
            self.view.mark(None)

    def _dispute_selected(self, row: int) -> None:
        items = self._page_disputes()
        if row < 0 or row >= len(items):
            return
        b, k = items[row]
        d = b.disputes[k]
        self.block = b
        self.view.select(b.id)
        self.view.mark(d.get("bbox"))
        self.lbl_disp_tess.setText(f"<b>Tesseract:</b> {d['tess'] or '(nada)'}")
        self.lbl_disp_surya.setText(f"<b>Surya:</b> {d['surya'] or '(nada)'}")
        pix = self._pix_cache.get(self.page.uid) if self.page else None
        bbox = d.get("bbox")
        if pix is not None and bbox:
            from PySide6.QtCore import QRect

            x0, y0, x1, y1 = bbox
            h = max(10, y1 - y0)
            r = QRect(int(x0 - h * 2), int(y0 - h * 0.8), int(x1 - x0 + h * 4), int(h * 2.6))
            sub = pix.copy(r.intersected(pix.rect()))
            self.lbl_disp_img.setPixmap(sub.scaled(self.lbl_disp_img.width() - 8, 160,
                                                   Qt.AspectRatioMode.KeepAspectRatio,
                                                   Qt.TransformationMode.SmoothTransformation))
        else:
            self.lbl_disp_img.setText("(sem posição na imagem)")
        self._render_preview()

    def _resolve_dispute(self, choice: str) -> None:
        items = self._page_disputes()
        row = self.lst_disp.currentRow()
        if row < 0 or row >= len(items):
            return
        b, k = items[row]
        changed = review.apply_dispute(self.state.project, b, k, choice, self.chk_disp_all.isChecked())
        self.state.dirty_pages.update(changed)
        self.changed.emit()
        self._render_preview()
        if self._page_disputes():
            self._refresh_disputes(row)
        else:
            self._next_dispute()

    def _next_dispute(self) -> None:
        proj = self.state.project
        if proj is None:
            return
        allp = review.open_disputes(proj)
        if not allp:
            self._refresh_disputes()
            return
        cur = proj.pages.index(self.page) if self.page in proj.pages else -1
        nxt = next((x for x in allp if x[0] > cur), None) or allp[0]
        if nxt[0] != cur:
            for r in range(self.pages.count()):
                if self.pages.item(r).data(Qt.ItemDataRole.UserRole) == nxt[0]:
                    self.pages.setCurrentRow(r)
                    break
            else:
                self.cmb_filter.setCurrentIndex(self.cmb_filter.findData("divergencias"))
                for r in range(self.pages.count()):
                    if self.pages.item(r).data(Qt.ItemDataRole.UserRole) == nxt[0]:
                        self.pages.setCurrentRow(r)
                        break
        self.tabs.setCurrentIndex(1)
        self._refresh_disputes()

    def _build_page_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.lbl_page = QLabel("")
        self.lbl_page.setWordWrap(True)
        lay.addWidget(self.lbl_page)
        self.chk_page_excl = QCheckBox("Excluir esta página da conversão")
        self.chk_page_excl.toggled.connect(self._toggle_page_excluded)
        self.chk_dup_keep = QCheckBox("Incluir mesmo sendo captura repetida")
        self.chk_dup_keep.toggled.connect(self._toggle_dup)
        lay.addWidget(self.chk_page_excl)
        lay.addWidget(self.chk_dup_keep)
        self.btn_reproc = QPushButton("Processar esta página de novo")
        self.btn_reproc.clicked.connect(lambda: self.page and self.reprocess_page_requested.emit(self.page.uid))
        lay.addWidget(self.btn_reproc)
        lay.addWidget(QLabel("Avisos da página:"))
        self.lst_page_flags = QListWidget()
        lay.addWidget(self.lst_page_flags, 1)
        return w

    # ------------------------------------------------------------------ dados
    def reload(self) -> None:
        self.reload_pages()
        self.reload_styles()
        self._load_scale()

    def reload_pages(self) -> None:
        proj = self.state.project
        cur_uid = self.page.uid if self.page else None
        self.pages.blockSignals(True)
        self.pages.clear()
        if proj is None:
            self.pages.blockSignals(False)
            return
        kind = self.cmb_filter.currentData() or "todas"
        idx = review.page_filter(proj, kind)
        if kind != "excluidas":
            idx = [i for i in idx if not proj.pages[i].excluded]
        sel_row = 0
        for row, i in enumerate(idx):
            p = proj.pages[i]
            mark = ""
            if p.status == PageStatus.ERROR:
                mark = "✖ "
            elif p.status != PageStatus.DONE:
                mark = "… "
            elif p.duplicate_of:
                mark = "⧉ "
            elif any(f in ("baixa_confianca", "encoberto", "ilegivel") for b in p.blocks for f in b.flags):
                mark = "⚠ "
            it = QListWidgetItem(f"{mark}{i + 1}. {p.source.display_name}")
            it.setData(Qt.ItemDataRole.UserRole, i)
            if p.duplicate_of or p.excluded:
                it.setForeground(QColor("#999999"))
            self.pages.addItem(it)
            if p.uid == cur_uid:
                sel_row = row
        self.pages.blockSignals(False)
        self.lbl_pages.setText(f"{len(idx)} de {len(proj.pages)} páginas")
        n = len(proj.pages)
        for sp in (self.spin_from, self.spin_to):
            sp.setRange(1, max(1, n))
        if self.pages.count():
            self.pages.setCurrentRow(sel_row)
            self._page_selected(self.pages.currentItem(), None)

    def _page_selected(self, cur, _prev) -> None:
        proj = self.state.project
        if cur is None or proj is None:
            return
        i = cur.data(Qt.ItemDataRole.UserRole)
        self.page = proj.pages[i]
        self.block = None
        self.spin_from.setValue(i + 1)
        self.spin_to.setValue(min(len(proj.pages), i + 1))
        try:
            pix = self._pix_cache.get(self.page.uid) or page_pixmap(self.page)
            if len(self._pix_cache) > 6:
                self._pix_cache.clear()
            self._pix_cache[self.page.uid] = pix
        except Exception as e:  # noqa: BLE001
            pix = None
            QMessageBox.warning(self, "Imagem", f"Não foi possível abrir a imagem: {e}")
        self.view.set_page(self.page, pix)
        self._show_page_info()
        self._render_preview()
        self._show_block()
        self._refresh_disputes()

    def _show_page_info(self) -> None:
        p = self.page
        if p is None:
            return
        txt = [f"<b>{p.source.display_name}</b>", f"Status: {p.status.value}"]
        if p.error:
            txt.append(f"Erro: {p.error}")
        if p.mean_conf:
            txt.append(f"Confiança média do OCR: {p.mean_conf:.0f}%")
        if p.printed_number:
            txt.append(f"Número impresso: {p.printed_number}")
        if p.inverted:
            txt.append("Página em modo escuro (invertida para o OCR)")
        if p.deskew:
            txt.append(f"Inclinação corrigida: {p.deskew:.1f}°")
        self.lbl_page.setText("<br>".join(txt))
        self.chk_page_excl.blockSignals(True)
        self.chk_page_excl.setChecked(p.excluded)
        self.chk_page_excl.blockSignals(False)
        self.chk_dup_keep.blockSignals(True)
        self.chk_dup_keep.setEnabled(bool(p.duplicate_of))
        self.chk_dup_keep.setChecked("manter_duplicata" in p.flags)
        self.chk_dup_keep.blockSignals(False)
        self.lst_page_flags.clear()
        flags = set(p.flags)
        for b in p.blocks:
            flags.update(b.flags)
        for f in sorted(flags):
            self.lst_page_flags.addItem(flag_label(f))
        if p.duplicate_of:
            self.lst_page_flags.addItem("Captura repetida da página anterior (fica fora da exportação)")

    def _render_preview(self) -> None:
        if self.page is None:
            self.preview.clear()
            return
        sb = self.preview.verticalScrollBar().value()
        self.preview.setHtml(page_html(self.state.project, self.page, self.block.id if self.block else None))
        self.preview.verticalScrollBar().setValue(sb)
        if self.block:
            self.preview.scrollToAnchor(self.block.id)

    def select_block(self, block_id: str) -> None:
        if self.page is None:
            return
        self.block = next((b for b in self.page.blocks if b.id == block_id), None)
        self.view.select(block_id)
        self._show_block()
        self._render_preview()
        if self.tabs.currentIndex() != 1:
            self.tabs.setCurrentIndex(0)

    def _show_block(self) -> None:
        b = self.block
        enabled = b is not None
        for wdg in (self.cmb_role, self.spin_level, self.cmb_scope, self.btn_role, self.editor, self.btn_save_text,
                    self.cmb_block_style):
            wdg.setEnabled(enabled)
        self.lst_flags.clear()
        if b is None:
            self.lbl_block.setText("Selecione uma região na imagem.")
            self.editor.clear()
            return
        conf = f"confiança {b.conf:.0f}%" if b.lines else ""
        extra = " · travado pela revisão" if b.role_locked else ""
        self.lbl_block.setText(f"<b>{b.role.label}</b> {conf}{extra}")
        self.cmb_role.setCurrentIndex(self.cmb_role.findData(b.role.value))
        self.spin_level.setValue(b.heading_level or 2)
        self._load_editor(b)
        self.cmb_block_style.blockSignals(True)
        self.cmb_block_style.setCurrentIndex(max(0, self.cmb_block_style.findData(b.style_id)))
        self.cmb_block_style.blockSignals(False)
        for f in b.flags:
            self.lst_flags.addItem(flag_label(f))

    # ------------------------------------------------------------------ editor
    def _load_editor(self, b: Block) -> None:
        self.editor.clear()
        cur = self.editor.textCursor()
        runs = b.runs or [Run(" ".join(ln.text for ln in b.lines))]
        for r in runs:
            f = QTextCharFormat()
            f.setFontWeight(QFont.Weight.Bold if r.bold else QFont.Weight.Normal)
            f.setFontItalic(r.italic)
            f.setFontUnderline(r.underline)
            if r.superscript:
                f.setVerticalAlignment(QTextCharFormat.VerticalAlignment.AlignSuperScript)
            elif r.subscript:
                f.setVerticalAlignment(QTextCharFormat.VerticalAlignment.AlignSubScript)
            if r.low_conf:
                f.setBackground(QColor("#fff3a0"))
                f.setForeground(QColor("#000000"))
            if r.note_ref:
                f.setAnchor(True)
                f.setAnchorHref(r.note_ref)
                f.setForeground(QColor("#1a5fb4"))
            cur.insertText(r.text, f)
        self.editor.moveCursor(QTextCursor.MoveOperation.Start)

    def _editor_runs(self) -> list[Run]:
        doc = self.editor.document()
        runs: list[Run] = []
        block = doc.begin()
        first = True
        while block.isValid():
            if not first:
                runs.append(Run("\n"))
            first = False
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                if frag.isValid():
                    f = frag.charFormat()
                    va = f.verticalAlignment()
                    href = f.anchorHref() if f.isAnchor() else ""
                    runs.append(Run(
                        frag.text(),
                        bold=f.fontWeight() >= QFont.Weight.Bold,
                        italic=f.fontItalic(),
                        underline=f.fontUnderline(),
                        superscript=va == QTextCharFormat.VerticalAlignment.AlignSuperScript,
                        subscript=va == QTextCharFormat.VerticalAlignment.AlignSubScript,
                        note_ref=href or None,
                    ))
                it += 1
            block = block.next()
        return merge_runs(runs)

    def _merge_fmt(self, fn) -> None:
        cur = self.editor.textCursor()
        if not cur.hasSelection():
            return
        f = QTextCharFormat()
        fn(f, cur.charFormat())
        cur.mergeCharFormat(f)

    def _fmt_bold(self):
        self._merge_fmt(lambda f, c: f.setFontWeight(QFont.Weight.Normal if c.fontWeight() >= QFont.Weight.Bold else QFont.Weight.Bold))

    def _fmt_italic(self):
        self._merge_fmt(lambda f, c: f.setFontItalic(not c.fontItalic()))

    def _fmt_underline(self):
        self._merge_fmt(lambda f, c: f.setFontUnderline(not c.fontUnderline()))

    def _fmt_sup(self):
        self._merge_fmt(lambda f, c: f.setVerticalAlignment(
            QTextCharFormat.VerticalAlignment.AlignNormal if c.verticalAlignment() == QTextCharFormat.VerticalAlignment.AlignSuperScript
            else QTextCharFormat.VerticalAlignment.AlignSuperScript))

    def _fmt_sub(self):
        self._merge_fmt(lambda f, c: f.setVerticalAlignment(
            QTextCharFormat.VerticalAlignment.AlignNormal if c.verticalAlignment() == QTextCharFormat.VerticalAlignment.AlignSubScript
            else QTextCharFormat.VerticalAlignment.AlignSubScript))

    def _save_text(self) -> None:
        if self.block is None:
            return
        review.set_text(self.block, self._editor_runs())
        self._after_change()

    # ------------------------------------------------------------------ função
    def _apply_role(self) -> None:
        if self.block is None or self.page is None:
            return
        role = Role(self.cmb_role.currentData())
        scope = self.cmb_scope.currentData()
        rng = (self.spin_from.value() - 1, self.spin_to.value() - 1)
        n = review.set_role(self.state.project, self.page, self.block, role, scope, rng)
        if role == Role.TITLE:
            review.set_heading_level(self.block, self.spin_level.value())
        self.view.refresh_regions()
        self.view.select(self.block.id)
        self._after_change()
        self.lbl_block.setText(self.lbl_block.text() + f"<br><small>{n} bloco(s) alterado(s).</small>")

    def _assign_style(self) -> None:
        if self.block is None:
            return
        sid = self.cmb_block_style.currentData()
        if sid and sid != self.block.style_id:
            review.assign_style(self.state.project, self.block, sid)
            self._after_change()

    # ------------------------------------------------------------------ estilos
    def reload_styles(self) -> None:
        proj = self.state.project
        self.tbl_styles.setRowCount(0)
        self.cmb_block_style.blockSignals(True)
        self.cmb_block_style.clear()
        if proj is None:
            self.cmb_block_style.blockSignals(False)
            return
        sts = list(proj.styles.values())
        self.tbl_styles.setRowCount(len(sts))
        for r, s in enumerate(sts):
            status = []
            if s.family_override:
                status.append("escolhida por você" + (" — NÃO INSTALADA" if not fonts.is_installed(s.family_override) else ""))
            else:
                status.append("substituição (fonte original não identificada com certeza)")
            vals = [s.name, str(s.sample_count), f"{s.family} ({s.category_label()})", f"{s.match_score:.0%}",
                    f"{s.size:g} pt" + (" (ajustado)" if s.size_override else ""), "; ".join(status)]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                it.setData(Qt.ItemDataRole.UserRole, s.id)
                if c == 2:
                    it.setFont(QFont(s.family))
                self.tbl_styles.setItem(r, c, it)
            self.cmb_block_style.addItem(s.name, s.id)
        self.tbl_styles.resizeColumnsToContents()
        self.cmb_block_style.blockSignals(False)

    def _current_style(self):
        proj = self.state.project
        row = self.tbl_styles.currentRow()
        if proj is None or row < 0:
            return None
        it = self.tbl_styles.item(row, 0)
        return proj.styles.get(it.data(Qt.ItemDataRole.UserRole)) if it else None

    def _style_selected(self, *args) -> None:
        s = self._current_style()
        if s is None:
            return
        self.cmb_font.setCurrentFont(QFont(s.family))
        self.spin_size.setValue(s.size)
        self.chk_bold.setChecked(s.bold)
        self.chk_italic.setChecked(s.italic)

    def _apply_style(self) -> None:
        s = self._current_style()
        if s is None:
            return
        review.update_style(s, family=self.cmb_font.currentFont().family(), size=self.spin_size.value(),
                            bold=self.chk_bold.isChecked(), italic=self.chk_italic.isChecked())
        self.reload_styles()
        self._after_change()

    def _reset_style(self) -> None:
        s = self._current_style()
        if s is None:
            return
        s.family_override = ""
        s.size_override = 0.0
        self.reload_styles()
        self._after_change()

    def _load_scale(self) -> None:
        proj = self.state.project
        if proj is None:
            return
        st = proj.settings
        self.cmb_scale.setCurrentIndex(max(0, self.cmb_scale.findData(st.scale_mode)))
        self.spin_dpi.setValue(st.dpi_override)
        self.spin_wmm.setValue(st.page_width_mm)
        self.spin_body.setValue(st.body_size_pt)
        self.lbl_scale.setText(f"Escala atual: {proj.analysis.get('scale_source', '—')}")

    def _apply_scale(self) -> None:
        proj = self.state.project
        if proj is None:
            return
        st = proj.settings
        st.scale_mode = self.cmb_scale.currentData()
        st.dpi_override = self.spin_dpi.value()
        st.page_width_mm = self.spin_wmm.value()
        hmm = self.cmb_pagesize.currentData()[1]
        st.page_height_mm = hmm if hmm and abs(self.cmb_pagesize.currentData()[0] - st.page_width_mm) < 0.5 else 0
        st.body_size_pt = self.spin_body.value()
        stylemod.resize_all(proj)
        self._load_scale()
        self.reload_styles()
        self._after_change()

    # ------------------------------------------------------------------ página
    def _toggle_page_excluded(self, on: bool) -> None:
        if self.page is not None:
            self.page.excluded = on
            self._after_change()

    def _toggle_dup(self, keep: bool) -> None:
        p = self.page
        if p is None or not p.duplicate_of:
            return
        if keep:
            p.flags.append("manter_duplicata")
            p.duplicate_of = None
        self._after_change()
        self.reload_pages()

    def _toggle_regions(self, on: bool) -> None:
        self.view.show_regions = on
        self.view.refresh_regions()

    def _after_change(self) -> None:
        if self.page is not None:
            self.state.dirty_pages.add(self.page.uid)
        self._render_preview()
        self._show_block()
        self.changed.emit()
