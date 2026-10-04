"""Medidas tipográficas tiradas dos pixels de cada linha e palavra.

São estimativas: espessura do traço (para negrito), inclinação (para
itálico), sublinhado contínuo, sobrescrito/subscrito, altura de x e de
maiúsculas. As decisões finais (o que é negrito *em relação ao corpo do
texto*) são tomadas na análise do documento inteiro.
"""

from __future__ import annotations

import statistics

import cv2
import numpy as np

from ..model import BBox, Line, Word

XHEIGHT_CHARS = set("acemnorsuvwxz")
CAP_CHARS = set("ABCDEFGHIKLMNOPRSTUVWXYZ")
DESCENDER_CHARS = set("gjpqyQ,;")


def ink_mask(gray: np.ndarray) -> np.ndarray:
    _, m = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    return m


def line_metrics(words: list[Word], fallback_x: float) -> tuple[int, float, float]:
    """(linha de base, altura de x, altura de maiúsculas) a partir das caixas
    dos caracteres."""
    bottoms, xh, caps = [], [], []
    for w in words:
        for ch, (x0, y0, x1, y1) in (w.chars or []):
            if ch in XHEIGHT_CHARS:
                xh.append(y1 - y0)
                bottoms.append(y1)
            elif ch in CAP_CHARS:
                caps.append(y1 - y0)
                bottoms.append(y1)
            elif ch.isalnum() and ch not in DESCENDER_CHARS and ch.isascii():
                bottoms.append(y1)
    if not bottoms:
        bottoms = [w.bbox[3] for w in words]
    baseline = int(statistics.median(bottoms))
    capheight = float(statistics.median(caps)) if caps else 0.0
    if len(xh) >= 2:
        xheight = float(statistics.median(xh))
    elif capheight:
        xheight = capheight * 0.68  # linha só em maiúsculas: proporção típica x/maiúscula
    else:
        xheight = fallback_x
    if not capheight:
        capheight = xheight * 1.45
    return baseline, xheight, capheight


def stroke_width(mask_crop: np.ndarray) -> float:
    if mask_crop.size == 0 or not mask_crop.any():
        return 0.0
    # Borda de fundo: sem ela, um recorte todo preenchido dá distância "infinita".
    m = np.pad((mask_crop > 0).astype(np.uint8), 1)
    dt = cv2.distanceTransform(m, cv2.DIST_L2, 3).astype(np.float64)
    dil = cv2.dilate(dt, np.ones((3, 3), np.uint8))
    ridge = (dt > 0) & (dt >= dil - 1e-3)
    vals = dt[ridge]
    if vals.size == 0:
        return 0.0
    return float(2.0 * np.median(vals))


def slant(mask_crop: np.ndarray) -> float:
    """Cisalhamento horizontal que deixa os traços mais verticais. Positivo =
    inclinado para a direita (itálico)."""
    h, w = mask_crop.shape
    if h < 8 or w < 8 or not mask_crop.any():
        return 0.0
    src = (mask_crop > 0).astype(np.float64)
    best, best_s = 0.0, -1.0
    pad = int(h * 0.5) + 2
    for s in np.arange(-0.15, 0.46, 0.05):
        m = np.float32([[1, s, pad - s * h], [0, 1, 0]])
        r = cv2.warpAffine(src.astype(np.float32), m, (w + 2 * pad, h), flags=cv2.INTER_NEAREST).astype(np.float64)
        prof = r.sum(axis=0)
        score = float((prof**2).sum())
        if score > best_s:
            best, best_s = float(s), score
    return round(best, 3)


def underline(mask: np.ndarray, bbox: BBox, baseline: int, xh: float) -> bool:
    """Linha contínua logo abaixo da linha de base, cobrindo a palavra.
    Linhas tracejadas (destaques populares do Kindle) não contam."""
    x0, _, x1, _ = bbox
    w = x1 - x0
    if w < 6 or xh <= 0:
        return False
    y_top = baseline + 1
    y_bot = min(mask.shape[0], int(baseline + max(3, xh * 0.6)))
    if y_bot <= y_top:
        return False
    band = mask[y_top:y_bot, x0:x1] > 0
    for row in band:
        if row.mean() >= 0.9:
            # Tracejado tem muitas transições; sublinhado contínuo, poucas.
            trans = np.count_nonzero(row[1:] != row[:-1])
            if trans <= 4:
                return True
    return False


def annotate_words(gray: np.ndarray, lines: list[Line]) -> None:
    """Preenche espessura, inclinação, sublinhado e sobrescrito/subscrito."""
    mask = ink_mask(gray)
    for ln in lines:
        xh = ln.xheight or 10.0
        for w in ln.words:
            x0, y0, x1, y1 = w.bbox
            crop = mask[max(0, y0):max(0, y1), max(0, x0):max(0, x1)]
            w.stroke = stroke_width(crop)
            if len(w.text) >= 3 and (y1 - y0) > 6:
                w.slant = slant(crop)
            w.underline = underline(mask, w.bbox, ln.baseline, xh)
            # Palavra inteira acima/abaixo da linha de base e menor que o corpo.
            height = y1 - y0
            if ln.words and len(ln.words) > 1 and height < xh * 1.15:
                if y1 < ln.baseline - xh * 0.35:
                    w.superscript = True
                elif y0 > ln.baseline - xh * 0.5 and y1 > ln.baseline + xh * 0.25:
                    w.subscript = True


def split_superscript_tail(w: Word, baseline: int, xh: float) -> tuple[str, str] | None:
    """Detecta algarismos/sinais sobrescritos no fim da palavra (chamadas de
    nota: "deuses¹"). Retorna (texto principal, sobrescrito)."""
    if not w.chars or len(w.chars) < 2 or xh <= 0:
        return None
    tail: list[str] = []
    for ch, (cx0, cy0, cx1, cy1) in reversed(w.chars):
        raised = cy1 < baseline - xh * 0.3 and (cy1 - cy0) < xh * 1.3
        if raised and (ch.isdigit() or ch in "*†‡§¹²³⁴⁵⁶⁷⁸⁹⁰"):
            tail.append(ch)
        else:
            break
    if not tail or len(tail) == len(w.chars):
        return None
    sup = "".join(reversed(tail))
    main = "".join(c for c, _ in w.chars[: len(w.chars) - len(tail)])
    return main, sup
