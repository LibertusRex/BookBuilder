"""Tarefas em segundo plano: a interface nunca trava.

* ``ThumbLoader``: miniaturas, uma por vez, numa fila de baixa prioridade.
* ``ProcessWorker``: OCR das páginas em processos paralelos (cancelável).
* ``AnalysisWorker``: análise do documento inteiro.
* ``ExportWorker``: gravação do DOCX/EPUB e do relatório.
"""

from __future__ import annotations

import traceback
from dataclasses import asdict

from PySide6.QtCore import QObject, QRunnable, QThread, Signal
from PySide6.QtGui import QImage

from .. import imageio
from ..model import Project, SourceRef
from ..pipeline import Runner


class ThumbSignals(QObject):
    done = Signal(str, QImage)  # uid, imagem


class ThumbTask(QRunnable):
    def __init__(self, ref: SourceRef, rotation: int, size: int, signals: ThumbSignals):
        super().__init__()
        self.ref, self.rotation, self.size, self.signals = ref, rotation, size, signals

    def run(self) -> None:
        try:
            im = imageio.thumbnail(self.ref, self.rotation, self.size).convert("RGB")
            data = im.tobytes("raw", "RGB")
            qimg = QImage(data, im.width, im.height, im.width * 3, QImage.Format.Format_RGB888).copy()
        except Exception:  # noqa: BLE001
            qimg = QImage()
        self.signals.done.emit(f"{self.ref.uid}:{self.rotation}", qimg)


class ProcessWorker(QThread):
    """Tesseract (processos paralelos) + releitura do Surya (placa de
    vídeo), ao mesmo tempo. Os resultados são gravados no projeto à medida
    que ficam prontos; os sinais só informam a interface."""

    tess_done = Signal(str, bool, str, str, float)  # uid, ok, erro, nome, confiança
    surya_done = Signal(str)
    status = Signal(str)
    failed = Signal(str)

    def __init__(self, project: Project, store, workers: int | None = None, redo: bool = False):
        super().__init__()
        self.project, self.store, self.redo = project, store, redo
        self.runner = Runner(workers)
        self.summary: dict = {}

    def cancel(self) -> None:
        self.runner.cancel()

    @property
    def cancelled(self) -> bool:
        return self.runner.cancelled

    def run(self) -> None:
        from ..model import PageStatus
        from ..pipeline import process_project

        try:
            self.summary = process_project(
                self.project, self.store, redo=self.redo, runner=self.runner,
                on_tess_page=lambda p: self.tess_done.emit(p.uid, p.status == PageStatus.DONE, p.error,
                                                           p.source.display_name, float(p.mean_conf or 0)),
                on_surya_page=lambda p: self.surya_done.emit(p.uid),
                on_status=self.status.emit,
            )
        except Exception as e:  # noqa: BLE001
            self.failed.emit(f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


class DownloadWorker(QThread):
    """Baixa o modelo do Surya (1,4 GB), com progresso e cancelamento."""

    progress = Signal(int, int)
    finished_ok = Signal()
    failed = Signal(str)

    def __init__(self):
        super().__init__()
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        from ..ocr import surya

        try:
            surya.download_model(lambda d, t: self.progress.emit(d // (1 << 20), t // (1 << 20)), lambda: self._cancel)
            self.finished_ok.emit()
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))


class AnalysisWorker(QThread):
    message = Signal(str)
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, project: Project, store):
        super().__init__()
        self.project, self.store = project, store

    def run(self) -> None:
        from ..document import analyze_document

        try:
            rep = analyze_document(self.project, self.store, progress=self.message.emit)
            self.finished_ok.emit(rep)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


class ExportWorker(QThread):
    message = Signal(str)
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, project: Project, store, docx_path: str | None, epub_path: str | None, report):
        super().__init__()
        self.project, self.store = project, store
        self.docx_path, self.epub_path, self.report = docx_path, epub_path, report

    def run(self) -> None:
        from ..export import docx_writer, epub_writer
        from ..export import report as rpt

        try:
            notes: list[str] = []
            out = {}
            if self.docx_path:
                self.message.emit("Gravando DOCX…")
                r = docx_writer.write(self.project, self.store, self.docx_path)
                notes += r.notes
                out["docx"] = r
            if self.epub_path:
                self.message.emit("Gravando EPUB…")
                r2 = epub_writer.write(self.project, self.store, self.epub_path)
                notes += r2.notes
                out["epub"] = r2
            text = rpt.text_report(self.project, self.report, notes)
            out["report"] = text
            self.finished_ok.emit(out)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


def settings_dict(project: Project) -> dict:
    return asdict(project.settings)
