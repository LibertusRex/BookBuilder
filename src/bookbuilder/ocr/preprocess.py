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


def text_height(gray: np.ndarray) -> float:
    """Altura típica das letras (px), pelos componentes de um limiar
    adaptativo: funciona mesmo com sombra e fundo desigual."""
    h, w = gray.shape[:2]
    f = max(1, max(h, w) // 1600)
    small = gray[::f, ::f]
    blk = max(15, (min(small.shape) // 40) | 1)
    ink = cv2.adaptiveThreshold(small, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, blk, 15)
    n, _, st, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    hs = st[1:, 3][(st[1:, 3] >= 4) & (st[1:, 3] <= small.shape[0] // 15) & (st[1:, 2] <= small.shape[1] // 10)]
    if hs.size >= 50:
        hs = hs[hs >= 0.4 * np.percentile(hs, 90)]  # sem pontos, vírgulas e pontilhados
    if hs.size < 50:
        return max(12.0, h / 110)
    return float(np.median(hs)) * f


def flatten_illumination(rgb: np.ndarray, xh: float, keep: list[BBox] | None = None) -> np.ndarray:
    """Iguala a iluminação de páginas fotografadas/digitalizadas: estima o
    papel sem as letras (fechamento morfológico), canal por canal, e divide a
    imagem por ele. O papel fica branco por igual (também sem a cor da luz),
    e o texto na sombra recupera o contraste; as bordas nítidas de sombras
    (mão, celular) são acompanhadas.

    ``keep``: figuras, cujo "fundo" vem do papel em volta (senão uma foto
    escura seria clareada até sumir)."""
    h, w = rgb.shape[:2]
    f = 4
    small = cv2.resize(rgb, (max(1, w // f), max(1, h // f)), interpolation=cv2.INTER_AREA)
    k = max(5, int(xh * 2.5 / f)) | 1
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    mask = None
    if keep:
        mask = np.zeros(small.shape[:2], np.uint8)
        for x0, y0, x1, y1 in keep:
            mask[max(0, y0 // f - 2):y1 // f + 2, max(0, x0 // f - 2):x1 // f + 2] = 255
    chans = []
    for c in range(3):
        bg = cv2.morphologyEx(np.ascontiguousarray(small[:, :, c]), cv2.MORPH_CLOSE, kernel)
        bg = cv2.medianBlur(bg, k)
        if mask is not None:
            bg = cv2.inpaint(bg, mask, 5, cv2.INPAINT_TELEA)
        chans.append(bg)
    bg = cv2.resize(np.dstack(chans), (w, h), interpolation=cv2.INTER_LINEAR).astype(np.float32)
    out = rgb.astype(np.float32) * (255.0 / np.maximum(bg, 1.0))
    return np.clip(out, 0, 255).astype(np.uint8)


def _warp_terms(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Monômios xⁱ·yʲ com i ≥ 1: os termos só em y equivalem a deslocar uma
    linha inteira e ficam com a posição própria de cada linha."""
    return np.stack([x ** i * y ** j for i in range(1, 5) for j in range(0, 3) if i + j <= 5], axis=-1)


def fit_warp(gray: np.ndarray, xh: float) -> dict | None:
    """Curvatura da página (perto da lombada, folha que não ficou plana),
    medida pelas próprias linhas de texto: cada linha deveria ser reta e
    horizontal. Ajusta um deslocamento vertical suave f(x, y) comum a todas
    as linhas (mínimos quadrados, com pesos que descartam figuras e fios).
    Retorna None quando a página já é plana ou não há linhas suficientes.

    ``gray``: imagem normalizada (texto escuro); ``xh``: altura de x, px."""
    h, w = gray.shape
    ink = (gray < 128).astype(np.uint8)
    kx = max(3, int(xh * 1.2))
    lines = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (kx, max(1, int(xh * 0.15)))))
    lines = cv2.morphologyEx(lines, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (kx, 1)))
    n, lab, st, _ = cv2.connectedComponentsWithStats(lines, connectivity=8)
    step = max(4, int(xh))
    xs, ys, ids = [], [], []
    for i in range(1, n):
        x, y, bw, bh, _ = st[i]
        if bw < 6 * xh or bh > 3.0 * xh or bh < 0.4 * xh:
            continue
        sub = (lab[y:y + bh, x:x + bw] == i) & (ink[y:y + bh, x:x + bw] > 0)
        prof = np.arange(bh, dtype=np.float32)[:, None]
        for cx in range(0, bw - step + 1, step):
            col = sub[:, cx:cx + step]
            mass = col.sum()
            if mass == 0:
                continue
            xs.append(x + cx + step / 2)
            ys.append(y + float((col * prof).sum() / mass))
            ids.append(i)
    if len(set(ids)) < 8 or len(xs) < 100:
        return None
    xs, ys = np.array(xs), np.array(ys)
    uniq, inv = np.unique(np.array(ids), return_inverse=True)
    T = _warp_terms(xs / w, ys / h)
    L = np.zeros((len(xs), len(uniq)))
    L[np.arange(len(xs)), inv] = 1
    A = np.hstack([T, L])
    wgt = np.ones(len(xs))
    for _ in range(4):
        sol, *_ = np.linalg.lstsq(A * wgt[:, None], ys * wgt, rcond=None)
        res = A @ sol - ys
        s = max(1.0, float(np.median(np.abs(res))) * 1.5)
        wgt = 1 / np.maximum(1, np.abs(res) / (2 * s))
    box = [float(xs.min() / w), float(ys.min() / h), float(xs.max() / w), float(ys.max() / h)]
    warp = {"c": [round(float(c), 4) for c in sol[:T.shape[1]]], "box": [round(b, 4) for b in box]}
    f = _warp_field(warp, w, h, every=8)
    if np.abs(f).max() < 0.3 * xh or float(np.median(np.abs(res))) > 0.25 * xh:
        return None  # já plana, ou o ajuste não explica as linhas
    return warp


def _warp_field(warp: dict, w: int, h: int, every: int = 1) -> np.ndarray:
    """Deslocamento vertical f(x, y), px, relativo ao centro de cada linha.
    Fora da área do texto o campo não é extrapolado (fica o da borda)."""
    x0, y0, x1, y1 = warp["box"]
    gx = np.clip(np.arange(0, w, every, dtype=np.float32) / w, x0, x1)
    gy = np.clip(np.arange(0, h, every, dtype=np.float32) / h, y0, y1)
    X, Y = np.meshgrid(gx, gy)
    c = np.asarray(warp["c"], dtype=np.float32)
    f = _warp_terms(X, Y) @ c
    xm = np.clip(np.float32(0.5), x0, x1)
    fm = _warp_terms(np.full_like(gy, xm), gy) @ c
    return f - fm[:, None]


def apply_warp(img: np.ndarray, warp: dict | None) -> np.ndarray:
    """Endireita as linhas segundo ``fit_warp`` (mesma geometria sempre)."""
    if not warp:
        return img
    h, w = img.shape[:2]
    small = _warp_field(warp, w, h, every=4).astype(np.float32)
    f = cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)
    mx, my = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
    border = (255, 255, 255) if img.ndim == 3 else 255
    return cv2.remap(img, mx, my + f, cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT, borderValue=border)


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
            xheight_hint: int = 20, overlays: bool = True) -> Prepared:
    """``overlays=False`` para páginas digitalizadas/fotografadas: não há
    interface de leitor a apagar, e sombras não podem ser tomadas por ela."""
    gray0 = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    bg, ink = estimate_levels(gray0)
    inverted = bg < ink
    gray = normalize(gray0, bg, ink)
    overlays = detect_overlays(rgb, bg, ink, xheight_hint=xheight_hint, protect=protect, norm=gray) if overlays else []
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
