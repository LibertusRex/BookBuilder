"""Montagem dos blocos de uma página a partir do OCR e das regiões do
detector de layout.

Etapas: descartar palavras encobertas por sobreposições → atribuir cada
palavra à menor região que a contém → refazer as linhas dentro de cada
região → dividir regiões de texto em parágrafos → medir alinhamento e
recuos → detectar quadros (caixas com moldura), tabelas e figuras → ordem de
leitura.
"""

from __future__ import annotations

import re
import statistics
from collections import defaultdict
from dataclasses import dataclass, field

import cv2
import numpy as np

from ..model import BBox, Block, Line, Role, Run, TableCell, Word
from ..ocr.tesseract import OcrLine
from ..typography import features
from . import detector, fixes, order

LABEL_ROLE = {
    "header": Role.HEADER,
    "footer": Role.FOOTER,
    "number": Role.PAGE_NUMBER,
    "footnote": Role.FOOTNOTE,
    "doc_title": Role.TITLE,
    "paragraph_title": Role.TITLE,
    "table": Role.TABLE,
    "figure_title": Role.CAPTION,
    "vision_footnote": Role.CAPTION,
}

BULLET_RE = re.compile(r"^([•●▪■◦○\-–—*·]|\(?[a-zA-Z]\)|\(?\d{1,3}[.)]|[ivxlcIVXLC]{1,5}[.)])\s")


@dataclass
class PageAnalysis:
    blocks: list[Block]
    figures: dict[str, np.ndarray] = field(default_factory=dict)  # id do bloco → recorte RGB
    flags: list[str] = field(default_factory=list)
    mean_conf: float = 0.0


def _area(b: BBox) -> int:
    return max(0, b[2] - b[0]) * max(0, b[3] - b[1])


def _inter(a: BBox, b: BBox) -> int:
    return max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))


def _union(boxes) -> BBox:
    boxes = list(boxes)
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def _contains_center(region: BBox, b: BBox, pad: int = 0) -> bool:
    cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    return region[0] - pad <= cx <= region[2] + pad and region[1] - pad <= cy <= region[3] + pad


# ---------------------------------------------------------------------------


@dataclass
class _W:
    word: Word
    line_key: tuple
    tess_xsize: float


def _collect_words(ocr_lines: list[OcrLine], overlays: list[BBox]) -> tuple[list[_W], list[BBox]]:
    """Palavras do OCR, sem as encobertas. Retorna também as caixas das
    palavras descartadas (para marcar blocos encobertos)."""
    words: list[_W] = []
    dropped: list[BBox] = []
    for n, ln in enumerate(ocr_lines):
        key = (ln.block_id, ln.par_id, n)
        for ow in ln.words:
            b = ow.bbox
            if any(_inter(b, o) > 0.15 * max(1, _area(b)) for o in overlays):
                dropped.append(b)
                continue
            hgt = max(8, b[3] - b[1])
            grown = (b[0] - hgt * 2, b[1] - hgt * 2, b[2] + hgt * 2, b[3] + hgt * 2)
            if ow.conf < 70 and any(_inter(grown, o) > 0 for o in overlays):
                dropped.append(b)  # resto da borda/sombra de uma sobreposição
                continue
            chars = [(c.text, c.bbox) for c in ow.chars] if ow.chars else None
            words.append(_W(Word(text=ow.text, bbox=b, conf=ow.conf, chars=chars), key, ln.x_size))
    return words, dropped


def _make_lines(ws: list[_W]) -> list[Line]:
    by_key: dict[tuple, list[_W]] = defaultdict(list)
    for w in ws:
        by_key[w.line_key].append(w)
    lines: list[Line] = []
    for items in by_key.values():
        items.sort(key=lambda w: w.word.bbox[0])
        # Uma linha do Tesseract pode atravessar duas colunas: separa em vãos grandes.
        groups: list[list[_W]] = [[items[0]]]
        xs = statistics.median([w.tess_xsize for w in items]) or 20.0
        for w in items[1:]:
            if w.word.bbox[0] - groups[-1][-1].word.bbox[2] > xs * 2.5:
                groups.append([w])
            else:
                groups[-1].append(w)
        for g in groups:
            words = [w.word for w in g]
            xfallback = statistics.median([w.tess_xsize for w in g]) * 0.62 if g[0].tess_xsize else 10.0
            baseline, xh, ch = features.line_metrics(words, xfallback)
            lines.append(Line(bbox=_union(w.bbox for w in words), words=words, baseline=baseline, xheight=xh, capheight=ch))
    lines.sort(key=lambda ln: (ln.bbox[1], ln.bbox[0]))
    return _attach_superscripts(_merge_same_row(lines))


SUP_TOKEN = re.compile(r"^[\d*†‡§¹²³⁴⁵⁶⁷⁸⁹⁰]{1,3}[.,;:)]?$")


def _attach_superscripts(lines: list[Line]) -> list[Line]:
    """O OCR às vezes lê uma chamada de nota sobrescrita ("público.¹") como
    uma linha própria, logo acima. Linhas só com sinais curtos e pequenos,
    no alto da linha vizinha e colados ao fim de uma palavra dela, voltam
    para essa linha como palavra sobrescrita."""
    out: list[Line] = []
    small = [ln for ln in lines if all(SUP_TOKEN.match(w.text) for w in ln.words) and len(ln.words) <= 3]
    for ln in lines:
        if ln not in small:
            out.append(ln)
            continue
        h = ln.bbox[3] - ln.bbox[1]
        host = None
        for other in lines:
            if other is ln or other in small:
                continue
            oh = other.bbox[3] - other.bbox[1]
            xh = other.xheight or oh * 0.45
            if h > xh * 1.1:
                continue
            # Base do sinal acima da linha de base da vizinha, topo perto do alto dela.
            if not (other.bbox[1] - xh * 0.8 <= ln.bbox[1] and ln.bbox[3] <= other.baseline - xh * 0.25):
                continue
            ok = True
            for w in ln.words:
                near = [o for o in other.words if 0 <= w.bbox[0] - o.bbox[2] <= xh * 0.8]
                if not near:
                    ok = False
            if ok:
                host = other
                break
        if host is None:
            out.append(ln)
            continue
        for w in ln.words:
            # Traço na meia altura da linha não é chamada de nota.
            w.superscript = any(ch.isalnum() for ch in w.text)
            idx = sum(1 for o in host.words if o.bbox[0] < w.bbox[0])
            host.words.insert(idx, w)
        host.bbox = _union([host.bbox, ln.bbox])
    return out


def _merge_same_row(lines: list[Line]) -> list[Line]:
    """Junta pedaços da mesma linha visual que o Tesseract separou."""
    out: list[Line] = []
    for ln in lines:
        if out:
            prev = out[-1]
            ov = min(prev.bbox[3], ln.bbox[3]) - max(prev.bbox[1], ln.bbox[1])
            h = min(prev.bbox[3] - prev.bbox[1], ln.bbox[3] - ln.bbox[1])
            gap = ln.bbox[0] - prev.bbox[2]
            if h > 0 and ov > 0.6 * h and 0 <= gap < max(prev.xheight, 8) * 2.5 and abs(prev.baseline - ln.baseline) < max(prev.xheight, 8) * 0.4:
                words = prev.words + ln.words
                out[-1] = Line(bbox=_union(w.bbox for w in words), words=words, baseline=prev.baseline,
                               xheight=prev.xheight, capheight=prev.capheight)
                continue
        out.append(ln)
    return out


def _unread_ink(gray: np.ndarray, covered: list[BBox], overlays: list[BBox], xh: float) -> list[BBox]:
    h, w = gray.shape[:2]
    xh = max(6.0, xh)
    ink = (gray < 150).astype(np.uint8)
    cover = np.zeros_like(ink)
    pad = int(xh)
    for x0, y0, x1, y1 in list(covered) + list(overlays):
        cover[max(0, y0 - pad):y1 + pad, max(0, x0 - pad):x1 + pad] = 1
    ink[cover > 0] = 0
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (int(xh * 3) | 1, int(xh * 1.5) | 1))
    blob = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, k)
    n, _, st, _ = cv2.connectedComponentsWithStats(blob, connectivity=8)
    out: list[BBox] = []
    for i in range(1, n):
        x, y, bw, bh, area = st[i]
        if bh < xh * 2 or bw < xh * 8:
            continue  # filetes, pontos, sujeira
        density = ink[y:y + bh, x:x + bw].mean()
        if density < 0.04:
            continue
        out.append((int(x), int(y), int(x + bw), int(y + bh)))
    return out


def _has_own_background(gray: np.ndarray, box: BBox) -> bool:
    """Região com fundo diferente do fundo da página (imagem embutida: uma
    ilustração de fundo claro numa página em modo escuro aparece escura na
    imagem de trabalho)."""
    x0, y0, x1, y1 = box
    crop = gray[y0:y1, x0:x1]
    if crop.size < 400 or min(crop.shape) < 12:
        return False
    # O fundo se vê na borda da região (o miolo pode ter letras grandes).
    k = max(2, min(crop.shape) // 20)
    ring = np.concatenate([crop[:k].ravel(), crop[-k:].ravel(), crop[:, :k].ravel(), crop[:, -k:].ravel()])
    return float(np.median(ring)) < 140


def _paragraph_like(lines: list[Line]) -> bool:
    """Linhas de texto corrido: várias, longas e alinhadas à esquerda."""
    if len(lines) < 3:
        return False
    xh = statistics.median([ln.xheight for ln in lines]) or 10
    left = statistics.median([ln.bbox[0] for ln in lines])
    aligned = np.mean([abs(ln.bbox[0] - left) < xh * 1.5 or abs(ln.bbox[0] - left - xh * 3) < xh * 1.5 for ln in lines])
    words_per_line = np.mean([len(ln.words) for ln in lines])
    return aligned >= 0.7 and words_per_line >= 5


def _placeholders(blk: Block, hidden: list[BBox], text_right: int) -> None:
    """Marca "[…]" onde o texto estava encoberto por uma sobreposição: na
    linha (no ponto exato) ou como linha própria entre linhas visíveis."""
    if not hidden or not blk.lines:
        return
    x0b, y0b, x1b, y1b = blk.bbox
    pitch = blk.line_pitch or (blk.lines[0].bbox[3] - blk.lines[0].bbox[1]) * 1.4
    for hb in hidden:
        if hb[2] < x0b or hb[0] >= text_right or hb[3] < y0b - pitch or hb[1] > y1b + pitch:
            continue
        placed = False
        for ln in blk.lines:
            ov = min(ln.bbox[3], hb[3]) - max(ln.bbox[1], hb[1])
            if ov > 0.4 * (ln.bbox[3] - ln.bbox[1]):
                placed = True
                if any(w.text == "[…]" for w in ln.words):
                    continue
                x = hb[0]
                idx = sum(1 for w in ln.words if w.bbox[0] < x)
                ln.words.insert(idx, Word("[…]", (hb[0], ln.bbox[1], min(hb[2], text_right), ln.bbox[3]), 0.0))
        if not placed and y0b < (hb[1] + hb[3]) / 2 < y1b:
            # Linha inteira encoberta entre duas linhas visíveis.
            ref = blk.lines[0]
            new_line = Line(bbox=(x0b, hb[1], x1b, hb[3]), words=[Word("[…]", (x0b, hb[1], x1b, hb[3]), 0.0)],
                            baseline=hb[3], xheight=ref.xheight, capheight=ref.capheight)
            blk.lines.append(new_line)
            blk.lines.sort(key=lambda ln: ln.bbox[1])


# ---------------------------------------------------------------------------
# Quadros com moldura


def _frame_lines(gray: np.ndarray) -> tuple[list[BBox], list[BBox]]:
    """Segmentos de linha horizontais e verticais finos e longos."""
    h, w = gray.shape
    _, inv = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY_INV)
    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (max(40, w // 6), 1))
    vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(40, h // 20)))
    hor = cv2.morphologyEx(inv, cv2.MORPH_OPEN, hk)
    ver = cv2.morphologyEx(inv, cv2.MORPH_OPEN, vk)
    hs, vs = [], []
    for m, out in ((hor, hs), (ver, vs)):
        n, _, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
        for i in range(1, n):
            x, y, bw, bh, _ = stats[i]
            out.append((int(x), int(y), int(x + bw), int(y + bh)))
    return hs, vs


def detect_frames(gray: np.ndarray) -> list[tuple[BBox, bool, bool]]:
    """Caixas delimitadas por duas linhas verticais (esquerda e direita).
    Retorna (caixa, aberta_em_cima, aberta_embaixo): um quadro que continua
    de/para outra página não tem a linha horizontal correspondente."""
    hs, vs = _frame_lines(gray)
    vs = [v for v in vs if (v[2] - v[0]) <= 8]
    frames: list[tuple[BBox, bool, bool]] = []
    used = set()
    vs.sort(key=lambda v: v[0])
    for i, a in enumerate(vs):
        if i in used:
            continue
        for j in range(i + 1, len(vs)):
            b = vs[j]
            if j in used:
                continue
            if b[0] - a[2] < gray.shape[1] * 0.2:
                continue
            ov = min(a[3], b[3]) - max(a[1], b[1])
            if ov < 0.8 * min(a[3] - a[1], b[3] - b[1]):
                continue
            box = (a[0], min(a[1], b[1]), b[2], max(a[3], b[3]))
            tol = 25
            top = any(abs(hl[1] - box[1]) < tol and hl[0] <= box[0] + tol * 2 and hl[2] >= box[2] - tol * 2 for hl in hs)
            bot = any(abs(hl[3] - box[3]) < tol and hl[0] <= box[0] + tol * 2 and hl[2] >= box[2] - tol * 2 for hl in hs)
            frames.append((box, not top, not bot))
            used.update((i, j))
            break
    # Quadros fechados formados só por linhas horizontais + cantos arredondados:
    # as verticais curtas não aparecem; aceita pares de horizontais com texto entre elas
    # apenas quando houver verticais parciais (evita confundir com filetes de nota).
    return frames


# ---------------------------------------------------------------------------
# Tabelas


def _table_cells(gray: np.ndarray, region: BBox, lines: list[Line]) -> list[TableCell] | None:
    x0, y0, x1, y1 = region
    crop = gray[y0:y1, x0:x1]
    if crop.size == 0:
        return None
    hs, vs = _frame_lines(np.pad(crop, 2, constant_values=255))
    row_edges = sorted({y0 + h[1] for h in hs if (h[2] - h[0]) > 0.5 * (x1 - x0)})
    col_edges = sorted({x0 + v[0] for v in vs if (v[3] - v[1]) > 0.5 * (y1 - y0)})
    words = [w for ln in lines for w in ln.words]
    if len(row_edges) >= 3 and len(col_edges) >= 3:
        rows = list(zip(_dedup_edges(row_edges)[:-1], _dedup_edges(row_edges)[1:]))
        cols = list(zip(_dedup_edges(col_edges)[:-1], _dedup_edges(col_edges)[1:]))
    else:
        # Sem grade: colunas por corredores verticais vazios alinhados entre as linhas.
        if len(lines) < 2:
            return None
        gaps = order._gaps([(w.bbox[0], w.bbox[2]) for w in words], min_gap=max(18, int(statistics.median([ln.xheight for ln in lines]) * 1.8)))
        if not gaps:
            return None
        edges = [x0] + [int((a + b) / 2) for a, b in gaps] + [x1]
        cols = list(zip(edges[:-1], edges[1:]))
        rows = [(ln.bbox[1] - 2, ln.bbox[3] + 2) for ln in lines]
    if len(cols) < 2 or len(rows) < 2:
        return None
    cells: list[TableCell] = []
    for r, (ra, rb) in enumerate(rows):
        for c, (ca, cb) in enumerate(cols):
            ws = [w for w in words if ca <= (w.bbox[0] + w.bbox[2]) / 2 < cb and ra <= (w.bbox[1] + w.bbox[3]) / 2 < rb]
            ws.sort(key=lambda w: (w.bbox[1] // 10, w.bbox[0]))
            cells.append(TableCell(row=r, col=c, text=" ".join(w.text for w in ws), bbox=(ca, ra, cb, rb)))
    filled = sum(1 for c in cells if c.text)
    if filled < max(2, len(cells) * 0.3):
        return None
    return cells


def _dedup_edges(edges: list[int], tol: int = 6) -> list[int]:
    out: list[int] = []
    for e in edges:
        if not out or e - out[-1] > tol:
            out.append(e)
    return out


# ---------------------------------------------------------------------------
# Novo OCR de recortes


def _reocr_lines(gray: np.ndarray, rbox: BBox, lines: list[Line], reocr) -> list[Line]:
    pad = 6
    x0, y0 = max(0, rbox[0] - pad), max(0, rbox[1] - pad)
    x1, y1 = min(gray.shape[1], rbox[2] + pad), min(gray.shape[0], rbox[3] + pad)
    crop = gray[y0:y1, x0:x1]
    if crop.size == 0:
        return lines
    try:
        ocr = reocr(crop, len(lines) > 1)
    except Exception:  # noqa: BLE001 - o resultado original continua válido
        return lines
    words_new = [x for ln in ocr for x in ln.words]
    if not words_new:
        return lines
    old_conf = float(np.mean([w.conf for ln in lines for w in ln.words]))
    new_conf = float(np.mean([w.conf for w in words_new]))
    if new_conf < old_conf - 2:
        return lines

    def tr(b):  # recorte ampliado 2× → página
        return (x0 + b[0] // 2, y0 + b[1] // 2, x0 + b[2] // 2, y0 + b[3] // 2)

    ws: list[_W] = []
    for n, ln in enumerate(ocr):
        for ow in ln.words:
            chars = [(c.text, tr(c.bbox)) for c in ow.chars] if ow.chars else None
            ws.append(_W(Word(text=ow.text, bbox=tr(ow.bbox), conf=ow.conf, chars=chars), ("r", "", n), ln.x_size / 2))
    return _make_lines(ws) or lines


# ---------------------------------------------------------------------------
# Parágrafos


def _split_paragraphs(lines: list[Line], left: int, right: int, label: str) -> list[list[Line]]:
    if not lines:
        return []
    if label in detector.TITLE_LABELS or label in ("header", "footer", "number"):
        return [lines]
    xh = statistics.median([ln.xheight for ln in lines]) or 10.0
    pitches = [b.baseline - a.baseline for a, b in zip(lines, lines[1:]) if b.baseline > a.baseline]
    pitch = statistics.median(pitches) if pitches else xh * 3
    # Recuo de primeira linha típico desta região.
    paras: list[list[Line]] = [[lines[0]]]
    for prev, ln in zip(lines, lines[1:]):
        indent = ln.bbox[0] - left
        prev_indent = prev.bbox[0] - left
        gap = ln.baseline - prev.baseline
        prev_short = prev.bbox[2] < right - xh * 2.5
        prev_text = prev.text.rstrip()
        prev_ends = prev_text.endswith((".", ":", "!", "?", "”", '"', ")", ";")) or prev_text.endswith(".”")
        starts_upper = bool(ln.text[:1]) and (ln.text[0].isupper() or ln.text[0].isdigit() or ln.text[0] in "“\"(—–-•")
        new = False
        if gap > pitch * 1.45:
            new = True
        elif indent > xh * 1.2 and prev_indent < indent - xh * 0.8:
            new = True  # recuo de primeira linha
        elif BULLET_RE.match(ln.text) and prev_short:
            new = True
        elif prev_short and prev_ends and starts_upper and indent < xh * 1.2 and len(paras[-1]) >= 1:
            # Parágrafos sem recuo (blocos): linha anterior curta terminando em pontuação.
            new = prev.bbox[2] < right - xh * 6
        if new:
            paras.append([ln])
        else:
            paras[-1].append(ln)
    return paras


def _alignment(lines: list[Line], left: int, right: int, xh: float) -> str:
    if not lines:
        return "left"
    tol = max(4.0, xh * 0.8)
    if len(lines) == 1:
        ln = lines[0]
        lgap, rgap = ln.bbox[0] - left, right - ln.bbox[2]
        if lgap > xh * 3 and abs(lgap - rgap) < max(tol * 2, (right - left) * 0.04):
            return "center"
        if lgap > xh * 3 and rgap < tol:
            return "right"
        return "left"
    body = lines[:-1]
    lefts = [ln.bbox[0] for ln in lines[1:]]
    rights = [ln.bbox[2] for ln in body]
    left_ok = np.mean([abs(x - left) < tol for x in lefts]) >= 0.8
    right_ok = np.mean([abs(x - right) < tol for x in rights]) >= 0.8
    if left_ok and right_ok:
        return "justify"
    centers = [((ln.bbox[0] + ln.bbox[2]) / 2) for ln in lines]
    mid = (left + right) / 2
    if not left_ok and np.mean([abs(c - mid) < tol * 1.5 for c in centers]) >= 0.8:
        return "center"
    if right_ok and not left_ok:
        return "right"
    return "left"


# ---------------------------------------------------------------------------


def _is_noise(text: str, conf: float) -> bool:
    alnum = sum(ch.isalnum() for ch in text)
    if alnum == 0:
        return True  # só traços/pontuação: filetes e ornamentos
    return alnum <= 2 and conf < 60


DASH_TOKEN = re.compile(r"^[—–\-_~]+[“”\"]?$")
NUM_TOKEN = re.compile(r"^\d+(\.\d+)*\.?$")


def _clean_title_words(words: list[Word]) -> list[Word]:
    """Remove traços que o OCR inventa no espaço largo (tabulação) entre o
    número e o texto do título: "4. — CRIMINOLOGIA" → "4. CRIMINOLOGIA"."""
    out: list[Word] = []
    for w in words:
        if out and NUM_TOKEN.match(out[-1].text):
            if DASH_TOKEN.match(w.text):
                continue
            if w.text[:1] in "—–“\"" and len(w.text) > 2:
                w.text = w.text.lstrip("—–“\"")
        out.append(w)
    return out


def analyze(gray: np.ndarray, color: np.ndarray, ocr_lines: list[OcrLine], regions: list[detector.Region],
            overlays: list[BBox], page_key: str, low_conf: float = 60.0, reocr=None) -> PageAnalysis:
    """``reocr(recorte_cinza, varias_linhas) -> list[OcrLine]`` refaz o OCR de
    um recorte ampliado 2× (títulos em fontes estreitas ou claras)."""
    h, w = gray.shape[:2]
    words, dropped = _collect_words(ocr_lines, overlays)
    flags: list[str] = []
    if dropped:
        flags.append("sobreposicao")

    # Regiões: as do detector; palavras fora delas formam regiões próprias.
    regs = [r for r in regions if r.label not in detector.FORMULA_LABELS or r.label == "display_formula"]
    assigned: dict[int, list[_W]] = defaultdict(list)
    orphans: list[_W] = []
    by_area = sorted(range(len(regs)), key=lambda i: _area(regs[i].bbox))
    for wd in words:
        target = None
        for i in by_area:
            if _contains_center(regs[i].bbox, wd.word.bbox, pad=4):
                target = i
                break
        if target is None:
            orphans.append(wd)
        else:
            assigned[target].append(wd)

    groups: list[tuple[str, float, BBox, list[_W]]] = []
    illegible: list[tuple[str, BBox]] = []
    for i, r in enumerate(regs):
        if r.label in detector.FIGURE_LABELS:
            groups.append((r.label, r.score, r.bbox, assigned.get(i, [])))
        elif assigned.get(i):
            ws_i = assigned[i]
            groups.append((r.label, r.score, r.bbox, ws_i))
            # Região grande com pouquíssimo texto lido: parte ilegível.
            covered = sum(_area(x.word.bbox) for x in ws_i)
            if r.score >= 0.6 and r.label in detector.TEXT_LABELS and covered < _area(r.bbox) * 0.08                     and (r.bbox[3] - r.bbox[1]) > 60:
                illegible.append((r.label, r.bbox))
        elif r.score >= 0.6 and r.label in detector.TEXT_LABELS | detector.TITLE_LABELS                 and not any(_inter(r.bbox, o) > 0.3 * _area(r.bbox) for o in overlays):
            illegible.append((r.label, r.bbox))
    if orphans:
        by_block: dict[str, list[_W]] = defaultdict(list)
        for wd in orphans:
            by_block[wd.line_key[0]].append(wd)
        for ws in by_block.values():
            groups.append(("text", 0.0, _union(x.word.bbox for x in ws), ws))

    # Medidas da área de texto da página (para recuos e alinhamento).
    all_lines_tmp = _make_lines(words) if words else []
    body_lines = [ln for ln in all_lines_tmp if len(ln.words) >= 4]
    if body_lines:
        text_left = int(np.percentile([ln.bbox[0] for ln in body_lines], 10))
        text_right = int(np.percentile([ln.bbox[2] for ln in body_lines], 90))
    else:
        text_left, text_right = int(w * 0.1), int(w * 0.9)
    page_xh = statistics.median([ln.xheight for ln in body_lines]) if body_lines else 10.0

    frames = detect_frames(gray)

    blocks: list[Block] = []
    figures: dict[str, np.ndarray] = {}
    n = 0
    for label, score, rbox, ws in groups:
        own_bg = _has_own_background(gray, rbox)
        if label in detector.FIGURE_LABELS or (own_bg and label not in ("header", "footer", "number")):
            lines_f = _make_lines(ws) if ws else []
            # Figura sem fundo próprio com texto corrido legível (ex.: quadro
            # sem moldura que o detector chamou de imagem) vira texto.
            if not own_bg and _paragraph_like(lines_f):
                label = "text"
            else:
                bid = f"{page_key}-b{n}"
                n += 1
                x0, y0, x1, y1 = rbox
                figures[bid] = color[y0:y1, x0:x1].copy()
                fig = Block(id=bid, role=Role.FIGURE, bbox=rbox, kind=label, conf=score * 100)
                # Texto lido dentro da imagem: descrição alternativa (não vira parágrafo).
                alt = " ".join(ln.text for ln in lines_f if ln.words)
                if alt:
                    fig.runs = [Run(alt)]
                blocks.append(fig)
                continue
        lines = _make_lines(ws)
        lines = [ln for ln in lines if not _is_noise(ln.text, float(np.mean([x.conf for x in ln.words])))]
        if not lines:
            continue
        if reocr is not None and label != "table" and (
            label in detector.TITLE_LABELS
            or (len(lines) <= 2 and min(x.conf for ln in lines for x in ln.words) < 85)
        ):
            lines = _reocr_lines(gray, rbox, lines, reocr)
        if label in detector.TITLE_LABELS or len(lines) <= 2:
            fixes.fix_title_lines(lines, gray)
        if label in detector.TITLE_LABELS:
            for ln in lines:
                ln.words = _clean_title_words(ln.words)
        if label == "table":
            cells = _table_cells(gray, rbox, lines)
            bid = f"{page_key}-b{n}"
            n += 1
            blk = Block(id=bid, role=Role.TABLE, bbox=rbox, kind=label, lines=lines,
                        conf=float(np.mean([wd.conf for ln in lines for wd in ln.words])))
            if cells:
                blk.table = cells
            else:
                blk.flags.append("tabela_incerta")
                figures[bid] = color[rbox[1]:rbox[3], rbox[0]:rbox[2]].copy()
            blocks.append(blk)
            continue
        left = min(ln.bbox[0] for ln in lines)
        right = max(ln.bbox[2] for ln in lines)
        # Recuo de primeira linha conta a partir da margem das demais linhas.
        if len(lines) >= 2:
            left = min(left, int(statistics.median([ln.bbox[0] for ln in lines[1:]])))
        for para in _split_paragraphs(lines, left, right, label):
            bid = f"{page_key}-b{n}"
            n += 1
            role = LABEL_ROLE.get(label, Role.BODY)
            if role == Role.BODY and BULLET_RE.match(para[0].text):
                role = Role.LIST_ITEM
            xh = statistics.median([ln.xheight for ln in para]) or page_xh
            bbox = _union(ln.bbox for ln in para)
            confs = [wd.conf for ln in para for wd in ln.words]
            pitches = [b.baseline - a.baseline for a, b in zip(para, para[1:]) if b.baseline > a.baseline]
            later = [ln.bbox[0] for ln in para[1:]]
            base_left = int(statistics.median(later)) if later else para[0].bbox[0]
            blk = Block(
                id=bid,
                role=role,
                bbox=bbox,
                kind=label,
                lines=para,
                conf=float(np.mean(confs)) if confs else 0.0,
                align=_alignment(para, min(left, text_left) if label in detector.TITLE_LABELS else left,
                                 max(right, text_right) if label in detector.TITLE_LABELS else right, xh),
                indent_first=float(para[0].bbox[0] - base_left) if len(para) > 1 else 0.0,
                indent_left=float(base_left - text_left),
                indent_right=float(max(0, text_right - bbox[2])) if len(para) > 1 else 0.0,
                line_pitch=float(statistics.median(pitches)) if pitches else 0.0,
            )
            # Área onde o texto do bloco deveria estar: até a margem direita do
            # texto e meia linha acima/abaixo. Sobreposições na margem (fora do
            # texto) não encobrem nada.
            reach = (bbox[0], int(bbox[1] - xh), max(bbox[2], text_right), int(bbox[3] + xh * 1.5))
            hidden = [o for o in list(overlays) + dropped
                      if _inter(reach, o) > 0 and o[0] < text_right - xh and o[2] > bbox[0] + xh]
            if hidden and label not in ("header", "footer", "number"):
                blk.occluded = True
                blk.flags.append("encoberto")
                _placeholders(blk, hidden, text_right)
            if blk.conf < 75 or any(c < low_conf for c in confs):
                blk.flags.append("baixa_confianca")
            blocks.append(blk)

    # Manchas de tinta que nenhum bloco cobre (texto borrado demais para o
    # detector e para o OCR): viram imagem recortada do original, marcada como
    # ilegível. Nada se perde em silêncio e nada é inventado.
    for rbox in _unread_ink(gray, [b.bbox for b in blocks] + [r for _, r in illegible], overlays, page_xh):
        bid = f"{page_key}-b{n}"
        n += 1
        figures[bid] = color[rbox[1]:rbox[3], rbox[0]:rbox[2]].copy()
        blocks.append(Block(id=bid, role=Role.FIGURE, bbox=rbox, kind="nao_lido", conf=0.0, flags=["ilegivel"]))

    for label, rbox in illegible:
        bid = f"{page_key}-b{n}"
        n += 1
        h_box = rbox[3] - rbox[1]
        ph = Word("[trecho ilegível]", rbox, 0.0)
        blocks.append(Block(id=bid, role=Role.TITLE if label in detector.TITLE_LABELS else Role.BODY, bbox=rbox,
                            kind=label, conf=0.0, flags=["ilegivel"],
                            lines=[Line(bbox=rbox, words=[ph], baseline=rbox[3], xheight=max(8.0, h_box * 0.3))]))

    # Quadros: blocos dentro de uma moldura.
    for k, (fbox, open_top, open_bot) in enumerate(frames):
        members = [b for b in blocks if _contains_center(fbox, b.bbox) and b.role not in (Role.HEADER, Role.FOOTER, Role.PAGE_NUMBER)]
        if not members:
            continue
        box_id = f"{page_key}-q{k}" + ("^" if open_top else "") + ("v" if open_bot else "")
        for b in members:
            b.box_id = box_id

    # Ordem de leitura.
    main = [b for b in blocks if b.role not in (Role.HEADER, Role.FOOTER, Role.PAGE_NUMBER, Role.FOOTNOTE)]
    tops = [b for b in blocks if b.role == Role.HEADER]
    bottoms = [b for b in blocks if b.role in (Role.FOOTER, Role.PAGE_NUMBER)]
    notes = [b for b in blocks if b.role == Role.FOOTNOTE]
    seq = sorted(tops, key=lambda b: (b.bbox[1], b.bbox[0]))
    idx = order.xycut([b.bbox for b in main], min_gap_y=max(4, int(page_xh * 0.5)), min_gap_x=max(15, int(page_xh * 1.5)))
    seq += [main[i] for i in idx]
    seq += sorted(notes, key=lambda b: (b.bbox[1], b.bbox[0]))
    seq += sorted(bottoms, key=lambda b: (b.bbox[1], b.bbox[0]))
    columns = order.detect_columns([b.bbox for b in main if b.role == Role.BODY and len(b.lines) >= 2
                                    and (b.bbox[2] - b.bbox[0]) > w * 0.25], w)
    for i, b in enumerate(seq):
        b.order = i
        b.column = order.column_of(b.bbox, columns)
    if len(columns) > 1:
        flags.append(f"colunas:{len(columns)}")

    # Espaço antes de cada bloco (em px), dentro da mesma coluna.
    last_bottom: dict[int, int] = {}
    for b in seq:
        if b.column in last_bottom:
            b.space_before = float(max(0, b.bbox[1] - last_bottom[b.column]))
        last_bottom[b.column] = b.bbox[3]

    features.annotate_words(gray, [ln for b in seq for ln in b.lines])
    for b in seq:
        for ln in b.lines:
            for wd in ln.words:
                split = features.split_superscript_tail(wd, ln.baseline, ln.xheight)
                if split:
                    wd.text = split[0] + split[1]
                    wd.tail_sup = split[1]
                wd.chars = None  # caixas de caractere não são guardadas no projeto
    confs = [wd.conf for b in seq for ln in b.lines for wd in ln.words]
    mean_conf = float(np.mean(confs)) if confs else 0.0
    if not confs:
        flags.append("sem_texto")
    elif mean_conf < 70:
        flags.append("baixa_confianca")
    return PageAnalysis(blocks=seq, figures=figures, flags=flags, mean_conf=mean_conf)
