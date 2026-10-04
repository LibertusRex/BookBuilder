"""Detecção de regiões da página (título, texto, tabela, figura, cabeçalho,
rodapé, nota...) com o modelo PP-DocLayout V3 em ONNX, executado localmente
pelo onnxruntime no processador.

O modelo é carregado uma vez por processo. Sem o modelo, a análise segue só
com as regras (``available()`` informa isso à interface).
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

import cv2
import numpy as np

from .. import paths
from ..model import BBox

log = logging.getLogger(__name__)

_engine = None
_lock = threading.Lock()


@dataclass
class Region:
    label: str
    score: float
    bbox: BBox


# Rótulos do PP-DocLayout V3 agrupados no que a análise usa.
TEXT_LABELS = {"text", "content", "abstract", "reference", "reference_content", "aside_text", "algorithm"}
TITLE_LABELS = {"doc_title", "paragraph_title"}
FIGURE_LABELS = {"image", "chart", "header_image", "footer_image", "seal"}
FORMULA_LABELS = {"display_formula", "inline_formula", "formula_number"}


def available() -> bool:
    return paths.layout_model().is_file()


def _get_engine():
    global _engine
    with _lock:
        if _engine is None:
            logging.getLogger("rapid_layout").setLevel(logging.WARNING)
            from rapid_layout import RapidLayout

            _engine = RapidLayout(model_type="pp_doc_layoutv3", model_dir_or_path=str(paths.layout_model()))
        return _engine


def detect(gray: np.ndarray, min_score: float = 0.35) -> list[Region]:
    """Regiões detectadas na imagem de trabalho (cinza, texto escuro)."""
    if not available():
        return []
    img = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR) if gray.ndim == 2 else gray
    out = _get_engine()(img)
    regions: list[Region] = []
    h, w = gray.shape[:2]
    if out.boxes is None:
        return regions
    for box, label, score in zip(out.boxes, out.class_names, out.scores):
        if score < min_score:
            continue
        x0, y0, x1, y1 = (int(round(v)) for v in box)
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(w, x1), min(h, y1)
        if x1 - x0 < 4 or y1 - y0 < 4:
            continue
        regions.append(Region(str(label), float(score), (x0, y0, x1, y1)))
    return _dedupe(regions)


def _dedupe(regions: list[Region]) -> list[Region]:
    """Remove caixas quase idênticas (o detector às vezes repete regiões)."""
    regions = sorted(regions, key=lambda r: -r.score)
    kept: list[Region] = []
    for r in regions:
        if any(_iou(r.bbox, k.bbox) > 0.7 for k in kept):
            continue
        kept.append(r)
    return kept


def _iou(a: BBox, b: BBox) -> float:
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0
