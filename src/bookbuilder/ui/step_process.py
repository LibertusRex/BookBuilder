"""Etapa 2 — OCR e análise da estrutura, com progresso por página,
cancelamento seguro e retomada. Com placa de vídeo dedicada, o Surya relê
o texto ao mesmo tempo (ligado por padrão; pode ser desligado)."""

from __future__ import annotations

import os
import time

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .. import project as projmod
from ..model import PageStatus
from ..ocr import surya
from .workers import AnalysisWorker, DownloadWorker, ProcessWorker


class ProcessStep(QWidget):
    finished = Signal()  # OCR + análise concluídos
    next_requested = Signal()
    project_changed = Signal()

    def __init__(self, state):
        super().__init__()
        self.state = state
        self.worker: ProcessWorker | None = None
        self.analysis: AnalysisWorker | None = None
        self.download: DownloadWorker | None = None
        self.t0 = 0.0
        self.total = 0
        self.done = 0
        self.surya_total = 0
        self.surya_count = 0

        self.lbl_info = QLabel("")
        self.lbl_info.setWordWrap(True)
        opts = QHBoxLayout()
        self.chk_redo = QCheckBox("Processar de novo também as páginas já concluídas")
        opts.addWidget(self.chk_redo)
        opts.addStretch(1)
        opts.addWidget(QLabel("Processos em paralelo:"))
        self.spin_workers = QSpinBox()
        cpu = os.cpu_count() or 4
        self.spin_workers.setRange(1, max(1, cpu))
        self.spin_workers.setValue(max(1, min(8, cpu - 1)))
        opts.addWidget(self.spin_workers)

        # Surya
        g = QGroupBox("Releitura com o Surya (placa de vídeo)")
        gl = QVBoxLayout(g)
        srow = QHBoxLayout()
        self.chk_surya = QCheckBox("Reler o texto com o Surya, combinando com o Tesseract (recomendado)")
        self.chk_surya.toggled.connect(self._surya_toggled)
        self.btn_download = QPushButton("Baixar o modelo do Surya (1,4 GB)…")
        self.btn_download.clicked.connect(self._download)
        srow.addWidget(self.chk_surya, 1)
        srow.addWidget(self.btn_download)
        gl.addLayout(srow)
        self.lbl_surya = QLabel("")
        self.lbl_surya.setWordWrap(True)
        gl.addWidget(self.lbl_surya)
        self.bar_dl = QProgressBar()
        self.bar_dl.setVisible(False)
        gl.addWidget(self.bar_dl)

        btns = QHBoxLayout()
        self.btn_start = QPushButton("Iniciar processamento")
        self.btn_start.setMinimumHeight(36)
        self.btn_cancel = QPushButton("Cancelar")
        self.btn_cancel.setEnabled(False)
        self.btn_analyze = QPushButton("Refazer só a análise do documento")
        self.btn_start.clicked.connect(self.start)
        self.btn_cancel.clicked.connect(self.cancel)
        self.btn_analyze.clicked.connect(self.run_analysis)
        btns.addWidget(self.btn_start)
        btns.addWidget(self.btn_cancel)
        btns.addWidget(self.btn_analyze)
        btns.addStretch(1)

        self.bar = QProgressBar()
        self.bar.setFormat("Tesseract: %v de %m páginas")
        self.bar_surya = QProgressBar()
        self.bar_surya.setFormat("Surya: %v de %m páginas")
        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        self.log = QListWidget()

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        self.btn_next = QPushButton("Próximo: revisar  →")
        self.btn_next.setMinimumHeight(36)
        self.btn_next.clicked.connect(self.next_requested.emit)
        bottom.addWidget(self.btn_next)

        lay = QVBoxLayout(self)
        lay.addWidget(self.lbl_info)
        lay.addLayout(opts)
        lay.addWidget(g)
        lay.addLayout(btns)
        lay.addWidget(self.bar)
        lay.addWidget(self.bar_surya)
        lay.addWidget(self.lbl_status)
        lay.addWidget(QLabel("Páginas processadas:"))
        lay.addWidget(self.log, 1)
        lay.addLayout(bottom)

    # ------------------------------------------------------------------ Surya
    def _refresh_surya(self) -> None:
        proj = self.state.project
        st = surya.status()
        busy = self.is_busy() or (self.download is not None and self.download.isRunning())
        self.chk_surya.blockSignals(True)
        if st.available:
            self.chk_surya.setEnabled(not busy)
            self.chk_surya.setChecked(bool(proj and proj.settings.use_surya))
            self.lbl_surya.setText(
                f"Disponível — {st.reason}. O Surya relê cada bloco e o resultado é combinado com o Tesseract: "
                "letra única diverge → Tesseract; palavras juntas, §, travessões, romanos, numeração, acentos "
                "válidos e itálico → Surya; outras divergências → Surya com destaque amarelo. "
                "Fica mais lento (cerca de 7 s por página).")
            self.btn_download.setVisible(False)
        elif st.needs_download:
            self.chk_surya.setEnabled(False)
            self.chk_surya.setChecked(False)
            self.lbl_surya.setText(f"Placa de vídeo encontrada ({st.gpu.name}), mas o modelo do Surya ainda não foi "
                                   "baixado. O download é feito uma única vez e o modelo fica neste computador.")
            self.btn_download.setVisible(True)
            self.btn_download.setEnabled(not busy)
        else:
            self.chk_surya.setEnabled(False)
            self.chk_surya.setChecked(False)
            self.lbl_surya.setText(f"Surya desligado: {st.reason}. O texto será lido só pelo Tesseract.")
            self.btn_download.setVisible(False)
        self.chk_surya.blockSignals(False)

    def _surya_toggled(self, on: bool) -> None:
        proj = self.state.project
        if proj is not None and proj.settings.use_surya != on:
            proj.settings.use_surya = on
            self.project_changed.emit()

    def _download(self) -> None:
        r = QMessageBox.question(
            self, "Baixar o modelo do Surya",
            "O modelo do Surya tem cerca de 1,4 GB e será baixado da Hugging Face (datalab-to/surya-ocr-2-gguf), "
            "com conferência de integridade (SHA-256). Licença do modelo: AI Pubs OpenRAIL-M modificada — uso "
            "pessoal livre.\n\nDepois do download, o reconhecimento continua 100% local. Baixar agora?")
        if r != QMessageBox.StandardButton.Yes:
            return
        self.download = DownloadWorker()
        self.bar_dl.setVisible(True)
        self.bar_dl.setFormat("Baixando: %v de %m MB")
        self.download.progress.connect(lambda d, t: (self.bar_dl.setRange(0, t), self.bar_dl.setValue(d)))
        self.download.finished_ok.connect(self._download_ok)
        self.download.failed.connect(lambda m: QMessageBox.warning(self, "Download", f"O download falhou: {m}"))
        self.download.finished.connect(self._download_finished)
        self.download.start()
        self._refresh_surya()

    def _download_ok(self) -> None:
        if self.state.project is not None:
            self.state.project.settings.use_surya = True
        QMessageBox.information(self, "Surya", "Modelo do Surya instalado.")

    def _download_finished(self) -> None:
        self.download = None
        self.bar_dl.setVisible(False)
        self._refresh_surya()

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        proj = self.state.project
        busy = self.is_busy()
        self._refresh_surya()
        if proj is None:
            self.lbl_info.setText("Abra uma pasta de imagens na etapa 1.")
            self.btn_start.setEnabled(False)
            self.btn_analyze.setEnabled(False)
            self.btn_next.setEnabled(False)
            return
        active = proj.active_pages()
        done = [p for p in active if p.status == PageStatus.DONE]
        err = [p for p in active if p.status == PageStatus.ERROR]
        todo = [p for p in active if projmod.needs_processing(p)]
        sur_todo = [p for p in done if surya.needs_surya(p)] if self.chk_surya.isChecked() else []
        txt = (f"{len(active)} páginas selecionadas: {len(done)} concluídas, {len(err)} com falha, "
               f"{len(todo)} a processar" + (f", {len(sur_todo)} a reler com o Surya" if sur_todo else "")
               + ". O processamento acontece inteiramente neste computador.")
        if self.state.store is not None:
            txt += f"\nProjeto salvo em: {self.state.store.folder}"
        self.lbl_info.setText(txt)
        pending = bool(todo or sur_todo)
        self.btn_start.setText("Retomar processamento" if done and pending else "Iniciar processamento")
        self.btn_start.setEnabled(not busy and (pending or self.chk_redo.isChecked() or bool(active)))
        self.btn_analyze.setEnabled(not busy and bool(done))
        self.btn_cancel.setEnabled(busy and self.worker is not None)
        self.btn_next.setEnabled(not busy and bool(done) and bool(proj.styles))

    def is_busy(self) -> bool:
        return (self.worker is not None and self.worker.isRunning()) or (self.analysis is not None and self.analysis.isRunning())

    def start(self) -> None:
        proj = self.state.project
        if proj is None or self.is_busy():
            return
        if not self.state.ensure_store(self):
            return
        redo = self.chk_redo.isChecked() or self.state.language_changed
        self.state.language_changed = False
        self.state.save()
        active = proj.active_pages()
        todo = active if redo else [p for p in active if projmod.needs_processing(p)]
        use_surya = self.chk_surya.isChecked() and self.chk_surya.isEnabled()
        proj.settings.use_surya = use_surya if self.chk_surya.isEnabled() else proj.settings.use_surya
        sur_pending = [p for p in active if p not in todo and surya.needs_surya(p)] if use_surya else []
        if not todo and not sur_pending:
            self.run_analysis()
            return
        self.total, self.done = len(todo), 0
        self.surya_total = (len(todo) + len(sur_pending)) if use_surya else 0
        self.surya_count = 0
        self.bar.setRange(0, max(1, self.total))
        self.bar.setValue(0)
        self.bar_surya.setVisible(use_surya)
        self.bar_surya.setRange(0, max(1, self.surya_total))
        self.bar_surya.setValue(0)
        self.t0 = time.time()
        self.log.clear()
        self.worker = ProcessWorker(proj, self.state.store, self.spin_workers.value(), redo=redo)
        self.worker.tess_done.connect(self._tess_done)
        self.worker.surya_done.connect(self._surya_done)
        self.worker.status.connect(self.lbl_status.setText)
        self.worker.failed.connect(self._failed)
        self.worker.finished.connect(self._ocr_finished)
        self.worker.start()
        self.refresh()

    def cancel(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel()
            self.lbl_status.setText("Cancelando… as páginas em andamento terminam e ficam salvas.")
            self.btn_cancel.setEnabled(False)

    def _eta(self) -> str:
        el = time.time() - self.t0
        parts = []
        if self.total:
            parts.append(f"Tesseract {self.done}/{self.total}")
        if self.surya_total:
            parts.append(f"Surya {self.surya_count}/{self.surya_total}")
        done_frac = 0.0
        if self.surya_total:
            done_frac = self.surya_count / self.surya_total
        elif self.total:
            done_frac = self.done / self.total
        eta = el / done_frac * (1 - done_frac) if done_frac > 0 else 0
        return " — ".join(parts) + f" — decorrido {_fmt(el)}" + (f", restante ≈ {_fmt(eta)}" if eta else "")

    def _tess_done(self, uid: str, ok: bool, error: str, name: str, conf: float) -> None:
        self.done += 1
        self.bar.setValue(self.done)
        msg = f"✔ {name}" if ok else f"✖ {name}: {error}"
        if ok and conf and conf < 75:
            msg += f"  (confiança média {conf:.0f}%)"
        it = QListWidgetItem(msg)
        if not ok:
            it.setForeground(QColor("#b00020"))
        self.log.addItem(it)
        self.log.scrollToBottom()
        self.lbl_status.setText(self._eta())

    def _surya_done(self, uid: str) -> None:
        self.surya_count += 1
        self.bar_surya.setValue(self.surya_count)
        self.lbl_status.setText(self._eta())

    def _failed(self, msg: str) -> None:
        QMessageBox.critical(self, "Falha no processamento", msg[:2000])

    def _ocr_finished(self) -> None:
        cancelled = self.worker.cancelled if self.worker else False
        summary = self.worker.summary if self.worker else {}
        self.worker = None
        self.state.save()
        if summary and not summary.get("surya_used") and self.surya_total:
            self.log.addItem(QListWidgetItem(f"Surya não usado: {summary.get('surya_reason', '')}"))
        if cancelled:
            self.lbl_status.setText(f"Cancelado. {self.done} páginas do Tesseract e {self.surya_count} do Surya foram "
                                    "salvas; use “Retomar processamento” para continuar de onde parou.")
            self.refresh()
            return
        self.run_analysis()

    def run_analysis(self) -> None:
        proj = self.state.project
        if proj is None or self.is_busy():
            return
        if not self.state.ensure_store(self):
            return
        self.bar.setRange(0, 0)
        self.analysis = AnalysisWorker(proj, self.state.store)
        self.analysis.message.connect(self.lbl_status.setText)
        self.analysis.finished_ok.connect(self._analysis_ok)
        self.analysis.failed.connect(self._failed)
        self.analysis.finished.connect(self._analysis_finished)
        self.analysis.start()
        self.refresh()

    def _analysis_ok(self, rep) -> None:
        self.state.report = rep
        self.lbl_status.setText("Análise concluída. Siga para a revisão.")

    def _analysis_finished(self) -> None:
        self.analysis = None
        self.bar.setRange(0, max(1, self.total))
        self.bar.setValue(self.total)
        self.state.save()
        self.refresh()
        self.finished.emit()


def _fmt(sec: float) -> str:
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}min" if h else f"{m}min{s:02d}s"
