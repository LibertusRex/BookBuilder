"""Pré-processamento da imagem da página antes do OCR.

* Converte para tons de cinza com texto escuro sobre fundo claro (páginas em
  modo escuro, como capturas do Kindle à noite, são invertidas).
* Detecta sobreposições que não pertencem ao documento: avisos do sistema,
  botões e barras do aplicativo leitor. Elas são apagadas da imagem de
  trabalho e registradas, para que o texto encoberto seja sinalizado e nunca
  inventado.
* Corrige a inclinação.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from ..model import BBox


@dataclass
class Prepared:
    gray: np.ndarray  # imagem de trabalho: texto escuro, fundo branco, endireitada
    color: np.ndarray  # mesma geometria, em cores (para recortar figuras)
    inverted: bool
    deskew: float  # graus aplicados (anti-horário positivo, convenção do OpenCV)
    overlays: list[BBox] = field(default_factory=list)
    ink_level: int = 0
    bg_level: int = 255


def estimate_levels(gray: np.ndarray) -> tuple[int, int]:
    """Nível do fundo (moda) e da tinta (percentil do lado oposto)."""
    small = gray[:: max(1, gray.shape[0] // 600), :: max(1, gray.shape[1] // 600)]
    hist = np.bincount(small.ravel(), minlength=256)
    bg = int(np.argmax(hist))
    if bg < 128:
        ink = int(np.percentile(small[small > bg + 40], 90)) if np.any(small > bg + 40) else 255
    else:
        ink = int(np.percentile(small[small < bg - 40], 10)) if np.any(small < bg - 40) else 0
    return bg, ink


def _cuts_text(norm: np.ndarray, box: BBox, xh: int) -> bool:
    """A região corta linhas de texto? Há tinta encostada nas bordas laterais,
    nas faixas de altura da região (o texto continua por baixo dela). Quadros
    do próprio livro têm espaço livre em volta."""
    h, w = norm.shape[:2]
    x0, y0, x1, y1 = box
    band = max(6, xh)
    gap = 3
    hits = 0
    for a, b in ((x0 - gap - band, x0 - gap), (x1 + gap, x1 + gap + band)):
        a, b = max(0, a), min(w, b)
        if b - a < 3:
            continue
        strip = norm[y0:y1, a:b] < 128
        if strip.size == 0:
            continue
        rows = strip.any(axis=1)
        if rows.mean() > 0.15:
            hits += 1
    return hits > 0


def detect_overlays(rgb: np.ndarray, bg: int, ink: int, xheight_hint: int = 20,
                    protect: list[BBox] | None = None, norm: np.ndarray | None = None) -> list[BBox]:
    """Regiões lisas e grandes, de cor diferente do fundo e da tinta, ou com
    cor saturada (o texto do livro é neutro). Traços finos de letras e as
    bordas de quadros somem na abertura morfológica; o que sobra são painéis,
    botões e avisos sobrepostos.

    ``protect``: caixas de figuras/tabelas detectadas, que não são tratadas
    como sobreposição.
    """
    h, w = rgb.shape[:2]
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    maxc = rgb.max(axis=2).astype(np.int16)
    minc = rgb.min(axis=2).astype(np.int16)
    sat = maxc - minc
    lo, hi = sorted((bg, ink))
    span = max(1, hi - lo)
    # Meios-tons: claramente diferentes do fundo e da tinta.
    mid = (np.abs(gray.astype(np.int16) - bg) > max(14, span * 0.08)) & (
        np.abs(gray.astype(np.int16) - ink) > span * 0.30
    )
    colorful = sat > 45
    cand = ((mid | colorful).astype(np.uint8)) * 255
    k = max(7, int(xheight_hint * 0.6)) | 1
    opened = cv2.morphologyEx(cand, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
    opened = cv2.morphologyEx(opened, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(opened, connectivity=8)
    boxes: list[BBox] = []
    min_area = max(1200, int((xheight_hint * 2) ** 2))
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if area < min_area or bw < xheight_hint or bh < xheight_hint:
            continue
        if bw > 0.97 * w and bh > 0.97 * h:
            continue  # página inteira (fundo cinza): não é sobreposição
        fill = area / float(bw * bh)
        if fill < 0.35:
            continue
        box = (int(x), int(y), int(x + bw), int(y + bh))
        if protect and any(_overlap_frac(box, p) > 0.5 for p in protect):
            continue
        if norm is not None:
            region_sat = float(np.median(sat[y:y + bh, x:x + bw]))
            in_margin = box[0] > w * 0.88 or box[2] < w * 0.12 or box[3] < h * 0.025 or box[1] > h * 0.975
            if not (region_sat > 45 or in_margin or _cuts_text(norm, box, xheight_hint)):
                continue  # quadro do próprio documento (com espaço em volta): é conteúdo
        boxes.append(box)
    # Margem de segurança: avisos têm bordas arredondadas e sombra.
    pad = max(4, xheight_hint // 3)
    return [(max(0, x0 - pad), max(0, y0 - pad), min(w, x1 + pad), min(h, y1 + pad)) for x0, y0, x1, y1 in boxes]


def _overlap_frac(a: BBox, b: BBox) -> float:
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    area = max(1, (a[2] - a[0]) * (a[3] - a[1]))
    return ix * iy / area


def normalize(gray: np.ndarray, bg: int, ink: int) -> np.ndarray:
    """Estica o contraste para tinta preta e fundo branco."""
    if bg < ink:  # modo escuro
        gray = 255 - gray
        bg, ink = 255 - bg, 255 - ink
    lo, hi = ink, bg
    if hi - lo < 30:
        return gray
    out = (gray.astype(np.float32) - lo) * (255.0 / (hi - lo))
    return np.clip(out, 0, 255).astype(np.uint8)


def remove_dashed_rules(gray: np.ndarray) -> int:
    """Apaga sublinhados tracejados (ex.: "destaques populares" do Kindle):
    fileiras de traços curtos e finos, isolados e regularmente espaçados.
    Eles confundem o OCR e fazem linhas inteiras sumirem. Retorna quantas
    fileiras foram removidas."""
    ink = (gray < 128).astype(np.uint8)
    n, lab, st, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    if n <= 1:
        return 0
    x, y, w, h = st[1:, 0], st[1:, 1], st[1:, 2], st[1:, 3]
    thin = np.where((h <= 5) & (w >= 3) & (w <= 40) & (w >= 2 * h))[0]
    if thin.size < 8:
        return 0
    rows: dict[int, list[int]] = {}
    for i in thin:
        rows.setdefault(int((y[i] + h[i] / 2) // 3), []).append(int(i))
    removed = 0
    kill: list[int] = []
    page_w = gray.shape[1]
    for key in sorted(rows):
        idx = rows[key] + rows.get(key + 1, [])
        if len(idx) < 8:
            continue
        xs = np.sort(x[idx])
        gaps = np.diff(xs)
        gaps = gaps[gaps > 0]
        if gaps.size < 6 or xs[-1] - xs[0] < page_w * 0.1:
            continue
        med = float(np.median(gaps))
        regular = np.mean(np.abs(gaps - med) <= max(2.0, med * 0.25))
        if regular < 0.6:
            continue
        kill.extend(i + 1 for i in idx)
        rows[key + 1] = []
        removed += 1
    if kill:
        gray[np.isin(lab, np.array(kill, dtype=lab.dtype))] = 255
    return removed


def estimate_skew(gray_dark_text: np.ndarray, max_angle: float = 6.0) -> tuple[float, float]:
    """Ângulo (graus) que maximiza a nitidez do perfil horizontal de tinta.
    Retorna (ângulo, ganho relativo sobre 0°)."""
    h, w = gray_dark_text.shape
    scale = 900.0 / max(w, h)
    small = cv2.resize(gray_dark_text, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
    ink = (small < 140).astype(np.float32)
    if ink.sum() < 200:
        return 0.0, 0.0
    center = (small.shape[1] / 2, small.shape[0] / 2)

    def score(angle: float) -> float:
        m = cv2.getRotationMatrix2D(center, angle, 1.0)
        r = cv2.warpAffine(ink, m, (small.shape[1], small.shape[0]), flags=cv2.INTER_NEAREST)
        prof = r.sum(axis=1)
        return float(np.var(prof))

    base = score(0.0)
    best_a, best_s = 0.0, base
    for a in np.arange(-max_angle, max_angle + 0.01, 0.5):
        s = score(float(a))
        if s > best_s:
            best_a, best_s = float(a), s
    for a in np.arange(best_a - 0.5, best_a + 0.51, 0.1):
        s = score(float(a))
        if s > best_s:
            best_a, best_s = float(a), s
    gain = (best_s - base) / base if base > 0 else 0.0
    return round(best_a, 2), gain


def rotate(img: np.ndarray, angle: float, border: int | tuple[int, int, int]) -> np.ndarray:
    if abs(angle) < 1e-3:
        return img
    h, w = img.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(img, m, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT, borderValue=border)


def prepare(rgb: np.ndarray, protect: list[BBox] | None = None, deskew: bool = True,
            xheight_hint: int = 20) -> Prepared:
    gray0 = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    bg, ink = estimate_levels(gray0)
    inverted = bg < ink
    gray = normalize(gray0, bg, ink)
    overlays = detect_overlays(rgb, bg, ink, xheight_hint=xheight_hint, protect=protect, norm=gray)
    for x0, y0, x1, y1 in overlays:
        gray[y0:y1, x0:x1] = 255
    remove_dashed_rules(gray)
    angle = 0.0
    if deskew:
        a, gain = estimate_skew(gray)
        if abs(a) >= 0.15 and gain > 0.03:
            angle = a
    color = rgb
    if angle:
        gray = rotate(gray, angle, 255)
        color = rotate(rgb, angle, (int(bg),) * 3)
        overlays = [_rotate_box(b, angle, gray.shape) for b in overlays]
    return Prepared(gray=gray, color=color, inverted=inverted, deskew=angle, overlays=overlays,
                    ink_level=ink, bg_level=bg)


def _rotate_box(box: BBox, angle: float, shape) -> BBox:
    h, w = shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    x0, y0, x1, y1 = box
    pts = np.array([[x0, y0, 1], [x1, y0, 1], [x0, y1, 1], [x1, y1, 1]], dtype=np.float32)
    r = pts @ m.T
    return (int(max(0, r[:, 0].min())), int(max(0, r[:, 1].min())), int(min(w, r[:, 0].max())), int(min(h, r[:, 1].max())))


def work_view(rgb: np.ndarray, deskew: float, inverted: bool) -> np.ndarray:
    """Imagem para exibir na revisão, na mesma geometria das coordenadas."""
    if deskew:
        gray0 = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        bg, _ = estimate_levels(gray0)
        rgb = rotate(rgb, deskew, (int(bg),) * 3)
    return rgb
