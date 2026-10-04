"""Árbitro por recorte: terceira opinião para as divergências Tesseract ×
Surya que nenhuma regra decidiu.

Para cada divergência, recorta da imagem a região das palavras envolvidas
(com uma margem), amplia 3× e relê:

* com o Tesseract em modo de linha (outra segmentação, outra escala);
* com o Surya, se houver placa de vídeo (o mesmo modelo, mas sobre um
  recorte pequeno, que muda o contexto).

Cada leitura nova vota na alternativa que contém. Junto com os votos das
leituras originais (uma para cada lado), vence quem tiver mais votos. Empate
→ a divergência continua aberta (em amarelo), para a revisão.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable

import cv2
import numpy as np

from ..layout.fusion import SEP_KEY, html_to_text, key, letters
from . import tesseract

log = logging.getLogger(__name__)


@dataclass
class Job:
    page_uid: str
    block_id: str
    dispute: dict
    crop: np.ndarray


def _tokens(text: str) -> list[str]:
    return [letters(key(t)) for t in text.split() if letters(key(t))]


def _contains(reading: str, candidate: str) -> bool:
    """A leitura contém o candidato como sequência contígua de palavras?"""
    cand = _tokens(candidate)
    if not cand:
        return False
    toks = _tokens(reading)
    n = len(cand)
    return any(toks[i:i + n] == cand for i in range(len(toks) - n + 1))


def vote(tess: str, surya: str, readings: list[str]) -> str | None:
    """'tess' / 'surya' / None (empate)."""
    vt, vs = 1, 1  # as leituras originais
    for r in readings:
        has_t = _contains(r, tess) if tess else None
        has_s = _contains(r, surya) if surya else None
        # Alternativa vazia (um motor não leu nada ali): vence se a leitura nova
        # também não contém o texto do outro lado.
        if not tess:
            has_t = not has_s
        if not surya:
            has_s = not has_t
        if has_t and not has_s:
            vt += 1
        elif has_s and not has_t:
            vs += 1
    if vt > vs:
        return "tess"
    if vs > vt:
        return "surya"
    return None


def make_crop(gray: np.ndarray, bbox) -> np.ndarray | None:
    if not bbox:
        return None
    x0, y0, x1, y1 = (int(v) for v in bbox)
    h = max(8, y1 - y0)
    px, py = int(h * 0.6), int(h * 0.35)
    H, W = gray.shape[:2]
    c = gray[max(0, y0 - py):min(H, y1 + py), max(0, x0 - px):min(W, x1 + px)]
    if c.size == 0:
        return None
    return c


def arbitrate(jobs: list[Job], surya_url: str | None, lang: str = "por",
              progress: Callable[[int, int], None] | None = None) -> dict[tuple[str, str, str], str | None]:
    """Decide as divergências. Retorna {(página, bloco, chave): 'tess'|'surya'|None}."""
    from . import surya as surya_mod

    def one(job: Job):
        d = job.dispute
        readings = []
        big = cv2.resize(job.crop, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        big = cv2.copyMakeBorder(big, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)
        try:
            lines = tesseract.ocr_hocr(big, lang=lang, psm=6)
            readings.append(" ".join(w.text for ln in lines for w in ln.words))
        except Exception as e:  # noqa: BLE001
            log.debug("árbitro: tesseract falhou: %s", e)
        if surya_url:
            try:
                readings.append(html_to_text(surya_mod.ocr_block(surya_url, job.crop, len(d["tess"]) + len(d["surya"]) + 40)))
            except Exception as e:  # noqa: BLE001
                log.debug("árbitro: surya falhou: %s", e)
        return (job.page_uid, job.block_id, d["tess"] + SEP_KEY + d["surya"]), vote(d["tess"], d["surya"], readings)

    out: dict = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for k, (res_key, choice) in enumerate(ex.map(one, jobs), 1):
            out[res_key] = choice
            if progress:
                progress(k, len(jobs))
    return out
