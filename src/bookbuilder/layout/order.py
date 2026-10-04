"""Ordem de leitura por corte recursivo XY.

A página é dividida recursivamente por corredores verticais de espaço em
branco que atravessam todo o conjunto (colunas, da esquerda para a direita)
e, quando não há corredor, por faixas horizontais (de cima para baixo). Um
título de largura total impede o corredor vertical, então ele é separado
primeiro, e as colunas abaixo dele são lidas uma de cada vez.
"""

from __future__ import annotations

from ..model import BBox


def _gaps(intervals: list[tuple[int, int]], min_gap: int) -> list[tuple[int, int]]:
    """Intervalos vazios entre a união dos intervalos dados."""
    if not intervals:
        return []
    iv = sorted(intervals)
    gaps = []
    cur_end = iv[0][1]
    for a, b in iv[1:]:
        if a - cur_end >= min_gap:
            gaps.append((cur_end, a))
        cur_end = max(cur_end, b)
    return gaps


def xycut(boxes: list[BBox], min_gap_y: int = 6, min_gap_x: int = 20) -> list[int]:
    """Índices das caixas na ordem de leitura."""
    idx = list(range(len(boxes)))
    out: list[int] = []
    _cut(boxes, idx, out, min_gap_y, min_gap_x, depth=0)
    return out


def _cut(boxes: list[BBox], idx: list[int], out: list[int], gy: int, gx: int, depth: int) -> None:
    if len(idx) <= 1 or depth > 60:
        out.extend(sorted(idx, key=lambda i: (boxes[i][1], boxes[i][0])))
        return
    # 1) Corte vertical: colunas da esquerda para a direita.
    xs = [(boxes[i][0], boxes[i][2]) for i in idx]
    gaps = _gaps(xs, gx)
    if gaps:
        groups = _split(idx, gaps, key=lambda i: (boxes[i][0] + boxes[i][2]) / 2)
        if len(groups) > 1:
            for g in groups:
                _cut(boxes, g, out, gy, gx, depth + 1)
            return
    # 2) Corte horizontal: faixas de cima para baixo.
    ys = [(boxes[i][1], boxes[i][3]) for i in idx]
    gaps = _gaps(ys, gy)
    if gaps:
        groups = _split(idx, [g for g in gaps], key=lambda i: (boxes[i][1] + boxes[i][3]) / 2)
        if len(groups) > 1:
            for g in groups:
                _cut(boxes, g, out, gy, gx, depth + 1)
            return
    # 3) Sem corte possível (caixas sobrepostas): de cima para baixo.
    out.extend(sorted(idx, key=lambda i: (boxes[i][1], boxes[i][0])))


def _split(idx: list[int], gaps: list[tuple[int, int]], key) -> list[list[int]]:
    cuts = [(a + b) / 2 for a, b in gaps]
    groups: list[list[int]] = [[] for _ in range(len(cuts) + 1)]
    for i in idx:
        k = key(i)
        pos = sum(1 for c in cuts if k > c)
        groups[pos].append(i)
    return [g for g in groups if g]


def column_of(box: BBox, columns: list[tuple[int, int]]) -> int:
    cx = (box[0] + box[2]) / 2
    for n, (a, b) in enumerate(columns):
        if a <= cx <= b:
            return n
    return 0


def detect_columns(boxes: list[BBox], page_width: int, min_gap: int = 30) -> list[tuple[int, int]]:
    """Colunas de texto da página a partir das caixas de corpo."""
    if not boxes:
        return [(0, page_width)]
    narrow = [b for b in boxes if (b[2] - b[0]) < page_width * 0.6]
    if len(narrow) < 2:
        return [(min(b[0] for b in boxes), max(b[2] for b in boxes))]
    gaps = _gaps([(b[0], b[2]) for b in narrow], min_gap)
    if not gaps:
        return [(min(b[0] for b in boxes), max(b[2] for b in boxes))]
    edges = [min(b[0] for b in narrow)] + [x for g in gaps for x in g] + [max(b[2] for b in narrow)]
    return [(edges[i], edges[i + 1]) for i in range(0, len(edges), 2)]
