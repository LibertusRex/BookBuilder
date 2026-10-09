"""Processamento das páginas: imagem → pré-processamento → layout → OCR →
blocos. Cada página é independente e roda num processo separado; o resultado
é gravado no cache do projeto assim que fica pronto, o que permite cancelar e
retomar sem refazer páginas concluídas.
"""

from __future__ import annotations

import logging
import os
import threading
import traceback
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from dataclasses import dataclass
from typing import Callable, Iterable

import cv2
import numpy as np

from . import imageio
from .layout import detector
from .layout import page as page_layout
from .model import Page, PageStatus, Settings
from .ocr import preprocess, tesseract

log = logging.getLogger(__name__)


@dataclass
class PageJob:
    page: dict  # Page.to_dict() sem blocos
    settings: dict
    figures_dir: str


# Largura (lado menor) de trabalho das páginas de PDFs de digitalização. O
# aplicativo recorta a folha, então a largura em pixels é proporcional à
# resolução da foto; igualá-la deixa as letras do mesmo tamanho em todas as
# páginas (fotos tiradas de distâncias diferentes), o que mantém os estilos.
SCAN_WIDTH = 1800


def _scaled(rgb: np.ndarray, scale: float) -> np.ndarray:
    if abs(scale - 1.0) < 0.01:
        return rgb
    interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
    return cv2.resize(rgb, None, fx=scale, fy=scale, interpolation=interp)


def _light(rgb: np.ndarray, scan: str) -> tuple[np.ndarray, dict | None]:
    """Páginas digitalizadas/fotografadas: escala igualada (PDF),
    iluminação igualada (sombras da mão, do celular, da lombada) e linhas
    endireitadas (folha curvada). Figuras grandes ficam com o fundo do papel
    em volta; uma página que é quase toda figura (capa) só tem a escala
    ajustada. O que foi feito fica em ``Page.light`` e é refeito igual por
    ``apply_light`` (Surya, árbitro, revisão)."""
    if not scan:
        return rgb, None
    scale = round(SCAN_WIDTH / min(rgb.shape[:2]), 4) if scan == "pdf" else 1.0
    rgb = _scaled(rgb, scale)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    xh = preprocess.text_height(gray)
    flat = preprocess.flatten_illumination(rgb, xh)
    g = cv2.cvtColor(flat, cv2.COLOR_RGB2GRAY)
    regions = detector.detect(preprocess.normalize(g, *preprocess.estimate_levels(g)))
    keep = [list(map(int, r.bbox)) for r in regions if r.label in detector.FIGURE_LABELS]
    area = sum((b[2] - b[0]) * (b[3] - b[1]) for b in keep)
    if area > 0.5 * gray.size:
        return rgb, {"scale": scale, "xh": 0}
    light = {"scale": scale, "xh": round(xh, 2), "keep": keep}
    if keep:
        flat = preprocess.flatten_illumination(rgb, xh, keep)
        g = cv2.cvtColor(flat, cv2.COLOR_RGB2GRAY)
    warp = preprocess.fit_warp(preprocess.normalize(g, *preprocess.estimate_levels(g)), xh * 0.6)
    if warp:
        light["warp"] = warp
        flat = preprocess.apply_warp(flat, warp)
    return flat, light


def apply_light(rgb: np.ndarray, light: dict | None) -> np.ndarray:
    if not light:
        return rgb
    rgb = _scaled(rgb, light.get("scale", 1.0))
    if not light.get("xh"):
        return rgb
    rgb = preprocess.flatten_illumination(rgb, light["xh"], [tuple(b) for b in light.get("keep", [])])
    return preprocess.apply_warp(rgb, light.get("warp"))


def _prepare_all(rgb: np.ndarray, settings: Settings, scan: str = ""):
    """Normaliza, detecta layout e sobreposições, endireita. Retorna
    (preparo, regiões, imagem usada, iluminação)."""
    rgb, light = _light(rgb, scan)
    gray0 = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    bg, ink = preprocess.estimate_levels(gray0)
    norm = preprocess.normalize(gray0, bg, ink)
    regions = detector.detect(norm)
    protect = [r.bbox for r in regions if r.label in detector.FIGURE_LABELS or r.label == "table"]
    text_h = [r.bbox[3] - r.bbox[1] for r in regions if r.label in ("header", "footer")]
    xh_hint = int(np.median(text_h) * 0.5) if text_h else max(12, rgb.shape[0] // 140)
    prep = preprocess.prepare(rgb, protect=protect, deskew=True, xheight_hint=xh_hint, overlays=not scan)
    if prep.deskew:
        regions = detector.detect(prep.gray)
    return prep, regions, light


def process_page(job: PageJob) -> dict:
    """Executado no processo de trabalho. Nunca levanta exceção: erros voltam
    na própria página, com status de erro."""
    page = Page.from_dict(job.page)
    settings = Settings(**job.settings)
    try:
        page.clear_results()
        page.dpi = imageio.read_dpi(page.source)
        rot = page.rotation
        scan = imageio.scan_kind(page.source)
        rgb = imageio.load_rgb(page.source, rot)
        prep, regions, light = _prepare_all(rgb, settings, scan)
        lines = tesseract.ocr_hocr(prep.gray, lang=settings.language, psm=3)
        conf = _mean_conf(lines)
        page.auto_rotation = 0
        if settings.detect_orientation and (not lines or conf < 45):
            turn, oconf = tesseract.detect_orientation(prep.gray)
            if turn and oconf >= 2.0:
                rgb2 = imageio.load_rgb(page.source, (rot + turn) % 360)
                prep2, regions2, light2 = _prepare_all(rgb2, settings, scan)
                lines2 = tesseract.ocr_hocr(prep2.gray, lang=settings.language, psm=3)
                if _mean_conf(lines2) > conf:
                    rgb, prep, regions, lines, light = rgb2, prep2, regions2, lines2, light2
                    page.auto_rotation = turn
        page.height, page.width = prep.gray.shape[:2]
        page.inverted = prep.inverted
        page.deskew = prep.deskew
        page.light = light
        page.overlays = [_overlay(b) for b in prep.overlays]
        analysis = page_layout.analyze(prep.gray, prep.color, lines, regions, prep.overlays,
                                       page_key=page.uid[:8], low_conf=settings.low_conf_threshold,
                                       reocr=lambda crop, multi: _reocr(crop, multi, settings.language))
        page.blocks = analysis.blocks
        page.flags = analysis.flags
        page.mean_conf = analysis.mean_conf
        os.makedirs(job.figures_dir, exist_ok=True)
        for bid, crop in analysis.figures.items():
            name = f"{bid}.png"
            ok, buf = cv2.imencode(".png", cv2.cvtColor(crop, cv2.COLOR_RGB2BGR))
            if ok:
                with open(os.path.join(job.figures_dir, name), "wb") as f:
                    f.write(buf.tobytes())
                for b in page.blocks:
                    if b.id == bid:
                        b.image_file = name
        if prep.deskew:
            page.flags.append("inclinacao_corrigida")
        if page.auto_rotation:
            page.flags.append("orientacao_corrigida")
        page.status = PageStatus.DONE
    except Exception as e:  # noqa: BLE001 - qualquer falha vira erro da página
        page.status = PageStatus.ERROR
        page.error = f"{type(e).__name__}: {e}"
        log.debug("falha na página %s\n%s", page.source.path, traceback.format_exc())
    return page.to_dict()


def _reocr(crop: np.ndarray, multi: bool, lang: str):
    """OCR de um recorte ampliado 2×: melhora títulos em fontes estreitas,
    claras ou com pontos pequenos ("2.1.2.1")."""
    big = cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    plain = tesseract.ocr_hocr(big, lang=lang, psm=6 if multi else 7)
    # Variante com traço engrossado (fontes finas/claras); fica a de maior confiança.
    thick = cv2.erode(big, np.ones((3, 3), np.uint8))
    bold = tesseract.ocr_hocr(thick, lang=lang, psm=6 if multi else 7)
    return bold if _mean_conf(bold) > _mean_conf(plain) + 1 else plain


def _overlay(b):
    from .model import Overlay

    return Overlay(bbox=b)


def _mean_conf(lines) -> float:
    vals = [w.conf for ln in lines for w in ln.words]
    return float(np.mean(vals)) if vals else 0.0


def work_gray(page: Page) -> tuple[np.ndarray, np.ndarray]:
    """Reconstrói a imagem de trabalho (cinza normalizada e cor) de uma página
    já processada, na mesma geometria das coordenadas dos blocos."""
    rgb = apply_light(imageio.load_rgb(page.source, (page.rotation + page.auto_rotation) % 360), page.light)
    gray0 = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    bg, ink = preprocess.estimate_levels(gray0)
    gray = preprocess.normalize(gray0, bg, ink)
    if page.deskew:
        gray = preprocess.rotate(gray, page.deskew, 255)
        rgb = preprocess.rotate(rgb, page.deskew, (int(bg),) * 3)
    for o in page.overlays:  # coordenadas já na geometria endireitada
        x0, y0, x1, y1 = o.bbox
        gray[y0:y1, x0:x1] = 255
    preprocess.remove_dashed_rules(gray)
    return gray, rgb


# ---------------------------------------------------------------------------


class Runner:
    """Executa páginas em paralelo, com cancelamento seguro.

    ``on_result(page_dict)`` é chamado no processo principal a cada página
    concluída (com sucesso ou erro)."""

    def __init__(self, workers: int | None = None):
        cpu = os.cpu_count() or 4
        self.workers = workers or max(1, min(8, cpu - 1))
        self._cancel = False
        self._executor: ProcessPoolExecutor | None = None

    def cancel(self) -> None:
        self._cancel = True

    @property
    def cancelled(self) -> bool:
        return self._cancel

    def run(self, jobs: Iterable[PageJob], on_result: Callable[[dict], None]) -> None:
        jobs = list(jobs)
        if not jobs:
            return
        self._executor = ProcessPoolExecutor(max_workers=self.workers)
        pending: set[Future] = set()
        it = iter(jobs)
        try:
            # Mantém poucas páginas em voo: memória controlada e cancelamento rápido.
            for _ in range(self.workers * 2):
                j = next(it, None)
                if j is None:
                    break
                pending.add(self._executor.submit(process_page, j))
            while pending:
                done, pending = wait(pending, timeout=0.5, return_when=FIRST_COMPLETED)
                for f in done:
                    try:
                        on_result(f.result())
                    except Exception:  # noqa: BLE001
                        log.exception("falha ao receber resultado")
                if self._cancel:
                    # Páginas que ainda não começaram são canceladas; as que já
                    # estão em execução terminam e o resultado é aproveitado.
                    running = {f for f in pending if not f.cancel()}
                    for f in running:
                        try:
                            on_result(f.result())
                        except Exception:  # noqa: BLE001
                            log.exception("falha ao receber resultado")
                    pending = set()
                    break
                for _ in range(len(done)):
                    j = next(it, None)
                    if j is None:
                        break
                    pending.add(self._executor.submit(process_page, j))
        finally:
            self._executor.shutdown(wait=True, cancel_futures=True)
            self._executor = None


# ---------------------------------------------------------------------------
# Orquestração: Tesseract (processos) + Surya (placa de vídeo) em paralelo


def surya_ready(project) -> tuple[bool, str]:
    """(usar o Surya?, explicação) conforme a opção do projeto e o computador."""
    from .ocr import surya

    if not project.settings.use_surya:
        return False, "Surya desligado nas opções"
    st = surya.status()
    return st.available, st.reason


def process_project(project, store, workers: int | None = None, redo: bool = False,
                    on_tess_page: Callable | None = None, on_surya_page: Callable | None = None,
                    on_status: Callable[[str], None] | None = None, runner: "Runner | None" = None,
                    use_surya: bool | None = None) -> dict:
    """Processa as páginas pendentes do projeto. O Tesseract roda em
    processos paralelos; cada página concluída entra na fila do Surya (se
    disponível), que relê os blocos na placa de vídeo ao mesmo tempo.
    Retorna um resumo. Pode ser cancelado por ``runner.cancel()``."""
    from . import project as projmod
    from .model import Page, PageStatus
    from .ocr import surya

    say = on_status or (lambda m: None)
    if redo:
        for p in project.active_pages():
            p.clear_results()
    todo = [p for p in project.active_pages() if projmod.needs_processing(p)]
    if use_surya is None:
        use_surya, why = surya_ready(project)
    else:
        why = ""
    runner = runner or Runner(workers)
    summary = {"tesseract": 0, "surya": 0, "surya_used": bool(use_surya), "surya_reason": why}
    server = stage = None
    lock = threading.Lock()
    by_uid = {p.uid: i for i, p in enumerate(project.pages)}

    def surya_done(page):
        store.save_page(page)
        with lock:
            summary["surya"] += 1
        if on_surya_page:
            on_surya_page(page)

    if use_surya:
        say("Iniciando o Surya na placa de vídeo…")
        server = surya.Server(parallel=8)
        try:
            server.start()
        except Exception as e:  # noqa: BLE001 - segue só com o Tesseract
            log.exception("Surya não iniciou")
            say(f"O Surya não pôde ser iniciado ({e}); seguindo só com o Tesseract.")
            server = None
            summary["surya_used"] = False
        if server is not None:
            stage = surya.Stage(server, lambda p: work_gray(p)[0], surya_done)
            for p in project.active_pages():
                if p not in todo and surya.needs_surya(p):
                    stage.submit(p)

    def on_result(d: dict) -> None:
        page = Page.from_dict(d)
        i = by_uid[page.uid]
        page.rotation = project.pages[i].rotation
        page.excluded = project.pages[i].excluded
        project.pages[i] = page
        store.save_page(page)
        summary["tesseract"] += 1
        if on_tess_page:
            on_tess_page(page)
        if stage is not None and page.status == PageStatus.DONE:
            stage.submit(page)

    try:
        if todo:
            jobs = [PageJob(p.to_dict(), _settings_dict(project), str(store.figures_dir)) for p in todo]
            say(f"Reconhecendo o texto de {len(jobs)} páginas…")
            runner.run(jobs, on_result)
        if stage is not None:
            if runner.cancelled:
                stage.cancel()
            else:
                say("Aguardando a releitura do Surya terminar…")
                while not stage.wait(1.0):
                    if runner.cancelled:
                        stage.cancel()
                        break
    finally:
        if stage is not None:
            stage.close()
        if server is not None:
            server.stop()
    return summary


def _settings_dict(project) -> dict:
    import dataclasses

    return dataclasses.asdict(project.settings)
