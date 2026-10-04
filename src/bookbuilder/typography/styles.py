"""Estilos do documento: agrupamento, negrito/itálico relativos, fonte
estimada e tamanho em pontos.

A conversão de pixels para pontos nunca supõe 1 px = 1 pt. A escala vem, em
ordem de preferência configurável:

* ``dpi``: DPI confiável gravado nas imagens (digitalizações) ou informado;
* ``page_size``: largura física da página informada pelo usuário;
* ``body_size``: tamanho do corpo do texto informado (padrão 11 pt), com as
  demais proporções preservadas — o único caminho possível em capturas de
  tela, que não têm dimensão física.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable

import cv2
import numpy as np

from ..model import Block, Page, Project, Role, Style
from . import fonts

TEXT_ROLES = (Role.BODY, Role.LIST_ITEM, Role.QUOTE)


@dataclass
class BlockFeat:
    xh: float
    stroke: float  # traço / altura de x
    slant: float
    width: float  # largura média do caractere / altura de x
    caps: bool
    group: str


def _group(b: Block) -> str:
    if b.role == Role.TITLE:
        return "titulo"
    if b.role in (Role.HEADER, Role.FOOTER, Role.PAGE_NUMBER):
        return "margem"
    if b.role == Role.FOOTNOTE:
        return "nota"
    if b.role == Role.CAPTION:
        return "legenda"
    if b.box_id:
        return "quadro"
    return "texto"


def block_features(b: Block) -> BlockFeat | None:
    words = [w for ln in b.lines for w in ln.words if w.conf >= 60 and len(w.text) >= 2]
    xhs = [ln.xheight for ln in b.lines if ln.xheight > 0]
    if not words or not xhs:
        return None
    xh = statistics.median(xhs)
    strokes = [w.stroke for w in words if w.stroke > 0]
    slants = [w.slant for w in words if len(w.text) >= 3]
    widths = [(w.bbox[2] - w.bbox[0]) / max(1, len(w.text)) for w in words if len(w.text) >= 3]
    letters = "".join(w.text for w in words if w.text.isalpha())
    caps = len(letters) >= 4 and sum(ch.isupper() for ch in letters) / len(letters) > 0.8
    return BlockFeat(
        xh=xh,
        stroke=(statistics.median(strokes) / xh) if strokes else 0.0,
        slant=statistics.median(slants) if slants else 0.0,
        width=(statistics.median(widths) / xh) if widths else 0.0,
        caps=caps,
        group=_group(b),
    )


@dataclass
class _Cluster:
    group: str
    feats: list[BlockFeat]
    blocks: list[Block]
    cached: BlockFeat | None = None

    def center(self) -> BlockFeat:
        f = self.feats
        return BlockFeat(
            xh=statistics.median(x.xh for x in f),
            stroke=statistics.median(x.stroke for x in f),
            slant=statistics.median(x.slant for x in f),
            width=statistics.median(x.width for x in f),
            caps=sum(x.caps for x in f) > len(f) / 2,
            group=self.group,
        )

    def add(self, f: BlockFeat, b: Block) -> None:
        self.feats.append(f)
        self.blocks.append(b)
        n = len(self.feats)
        if n < 10 or n % 25 == 0:
            self.cached = self.center()


def _close(a: BlockFeat, c: BlockFeat) -> bool:
    if a.group != c.group or a.caps != c.caps:
        return False
    if abs(a.xh - c.xh) > max(1.5, c.xh * 0.12):
        return False
    if c.width and a.width and abs(a.width - c.width) > c.width * 0.15:
        return False
    if c.stroke and a.stroke and abs(a.stroke - c.stroke) > c.stroke * 0.25:
        return False
    if abs(a.slant - c.slant) > 0.12:
        return False
    return True


def cluster(pages: list[Page]) -> list[_Cluster]:
    clusters: list[_Cluster] = []
    for p in pages:
        for b in p.blocks:
            if b.role in (Role.FIGURE, Role.TABLE, Role.ARTIFACT):
                continue
            f = block_features(b)
            if f is None:
                continue
            for c in clusters:
                if c.cached is not None and _close(f, c.cached):
                    c.add(f, b)
                    break
            else:
                clusters.append(_Cluster(f.group, [f], [b], f))
    return clusters


# ---------------------------------------------------------------------------


GROUP_NAMES = {"texto": "Texto", "titulo": "Título", "margem": "Margem", "nota": "Nota de rodapé",
               "legenda": "Legenda", "quadro": "Quadro"}
GROUP_ROLES = {"texto": Role.BODY, "titulo": Role.TITLE, "margem": Role.HEADER, "nota": Role.FOOTNOTE,
               "legenda": Role.CAPTION, "quadro": Role.BODY}


def build_styles(project: Project, pages: list[Page], sample_loader: Callable[[Page], np.ndarray] | None,
                 progress: Callable[[str], None] | None = None) -> dict[str, Style]:
    """Agrupa os blocos em estilos, estima fontes e tamanhos e grava
    ``style_id`` em cada bloco. Estilos editados pelo usuário (fonte ou
    tamanho escolhidos) são preservados pelo nome."""
    old = {s.name: s for s in project.styles.values()}
    clusters = [c for c in cluster(pages) if c.feats]
    if not clusters:
        return {}
    # Corpo: o agrupamento de texto com mais linhas.
    def nlines(c: _Cluster) -> int:
        return sum(len(b.lines) for b in c.blocks)

    text_cl = [c for c in clusters if c.group == "texto"] or clusters
    body = max(text_cl, key=nlines)
    body_c = body.center()

    # Agrupamentos muito pequenos são absorvidos pelo mais próximo do mesmo grupo.
    total_lines = sum(nlines(c) for c in clusters)
    keep: list[_Cluster] = []
    for c in sorted(clusters, key=nlines, reverse=True):
        min_lines = 3 if c.group == "titulo" else max(3, total_lines * 0.002)
        if c is body or nlines(c) >= min_lines:
            keep.append(c)
            continue
        cxh = c.center().xh
        same = [k for k in keep if k.group == c.group and abs(k.center().xh - cxh) <= max(1.5, cxh * 0.15)]
        if same:
            tgt = min(same, key=lambda k: abs(k.center().xh - cxh))
            tgt.feats += c.feats
            tgt.blocks += c.blocks
            tgt.cached = tgt.center()
        else:
            keep.append(c)

    styles: dict[str, Style] = {}
    counters: dict[str, int] = defaultdict(int)
    # Títulos: do maior para o menor.
    ordered = sorted(keep, key=lambda c: (c.group != "texto", c.group, -c.center().xh))
    for c in ordered:
        cen = c.center()
        if c is body:
            name = "Corpo do texto"
        else:
            counters[c.group] += 1
            name = f"{GROUP_NAMES[c.group]} {counters[c.group]}"
        sid = f"s{len(styles) + 1}"
        st = Style(
            id=sid,
            name=name,
            role=GROUP_ROLES[c.group],
            xheight_px=round(cen.xh, 2),
            stroke_ratio=round(cen.stroke, 4),
            condensed=round(cen.width / body_c.width, 3) if body_c.width else 1.0,
            bold=bool(body_c.stroke and cen.stroke > body_c.stroke * 1.3),
            italic=bool(cen.slant - body_c.slant >= 0.12),
            sample_count=sum(len(b.lines) for b in c.blocks),
        )
        prev = old.get(name)
        if prev:
            st.family_override = prev.family_override
            st.size_override = prev.size_override
        styles[sid] = st
        for b in c.blocks:
            b.style_id = sid
        st._cluster = c  # type: ignore[attr-defined]

    _match_fonts(styles, pages, sample_loader, progress)
    _sizes(project, styles, body_style_id=next(s.id for s in styles.values() if s.name == "Corpo do texto"))
    for s in styles.values():
        if hasattr(s, "_cluster"):
            delattr(s, "_cluster")
    return styles


def _match_fonts(styles: dict[str, Style], pages: list[Page], loader, progress, per_style: int = 16,
                 max_pages: int = 80) -> None:
    """Compara cada estilo com as fontes instaladas usando palavras de
    páginas espalhadas pelo documento inteiro (amostras só do começo do
    livro enviesam a escolha)."""
    if loader is None:
        return
    page_index = {}
    for i, p in enumerate(pages):
        for b in p.blocks:
            page_index[id(b)] = (i, p)
    cache: dict[str, np.ndarray] = {}
    for s in styles.values():
        c = s._cluster  # type: ignore[attr-defined]
        blocks = sorted((b for b in c.blocks if id(b) in page_index), key=lambda b: page_index[id(b)][0])
        if not blocks:
            continue
        # Até 2 palavras por bloco, de blocos igualmente espaçados no documento.
        step = max(1, len(blocks) // per_style)
        chosen = blocks[::step][: per_style * 2]
        samples: list[fonts.Sample] = []
        for b in chosen:
            _, p = page_index[id(b)]
            if p.uid not in cache:
                if len(cache) >= max_pages:
                    continue
                if progress:
                    progress(f"Analisando fontes: {s.name}")
                try:
                    cache[p.uid] = loader(p)
                except Exception:  # noqa: BLE001
                    continue
            g = cache[p.uid]
            taken = 0
            for ln in b.lines:
                for w in ln.words:
                    t = w.text.strip(".,;:()“”\"'!?")
                    if w.conf < 88 or len(t) < 4 or not t.isalpha() or w.tail_sup:
                        continue
                    x0, y0, x1, y1 = w.bbox
                    crop = g[y0:y1, x0:x1]
                    if crop.size == 0:
                        continue
                    _, binimg = cv2.threshold(crop, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
                    ys, xs = np.where(binimg > 0)
                    if ys.size == 0:
                        continue
                    binimg = binimg[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
                    samples.append(fonts.Sample(w.text, binimg, ln.xheight or s.xheight_px))
                    taken += 1
                    if taken >= 2:
                        break
                if taken >= 2:
                    break
            if len(samples) >= per_style:
                break
        ranked = fonts.match(samples, want_bold=s.bold, want_italic=s.italic, limit=8)
        if ranked:
            fam, score, cat = ranked[0]
            if score < 0.55:
                # Semelhança fraca: categoria pela maioria das mais parecidas.
                cats = [c for _, _, c in ranked[:5]]
                cat = max(set(cats), key=cats.count)
            s.category = cat
            s.match_score = round(score, 3)
            if score >= 0.55:
                s.detected_family = fam
            else:
                # Semelhança fraca: usa uma fonte comum da categoria, sem fingir precisão.
                s.detected_family = {"serif": "Georgia", "sans": "Segoe UI", "mono": "Consolas"}[cat]
        if s.family_override and not fonts.is_installed(s.family_override):
            s.missing_font = s.family_override


def scale(project: Project, pages: list[Page], body: Style | None) -> tuple[float, str]:
    """(pixels por ponto, origem da escala)."""
    st = project.settings
    mode = st.scale_mode
    dpis = [p.dpi for p in pages if p.dpi > 0]
    widths = [p.width for p in pages if p.width]
    if mode == "auto":
        mode = "dpi" if dpis and len(dpis) >= len(pages) * 0.5 else ("page_size" if st.page_width_mm else "body_size")
    if mode == "dpi":
        dpi = st.dpi_override or (statistics.median(dpis) if dpis else 0)
        if dpi:
            return dpi / 72.0, f"DPI {dpi:.0f}"
        mode = "body_size"
    if mode == "page_size" and st.page_width_mm and widths:
        return statistics.median(widths) / (st.page_width_mm / 25.4 * 72.0), f"largura da página {st.page_width_mm:.0f} mm"
    if body and body.xheight_px:
        xr = fonts.xratio(body.family)
        return body.xheight_px / (xr * st.body_size_pt), f"corpo do texto = {st.body_size_pt:g} pt"
    return 300 / 72.0, "padrão (300 DPI)"


def _sizes(project: Project, styles: dict[str, Style], body_style_id: str) -> None:
    pages = project.active_pages()
    body = styles.get(body_style_id)
    ppp, source = scale(project, pages, body)
    project.analysis["px_per_pt"] = ppp
    project.analysis["scale_source"] = source
    for s in styles.values():
        xr = fonts.xratio(s.family)
        if s.xheight_px and ppp:
            s.size_pt = max(4.0, round(s.xheight_px / (xr * ppp) * 2) / 2)


def resize_all(project: Project) -> None:
    """Recalcula os tamanhos (após o usuário mudar escala ou fontes)."""
    body = next((s for s in project.styles.values() if s.name == "Corpo do texto"), None)
    if body:
        _sizes(project, project.styles, body.id)


# ---------------------------------------------------------------------------
# Negrito e itálico por palavra, relativos ao estilo


def word_formatter(styles: dict[str, Style]):
    body = next((s for s in styles.values() if s.name == "Corpo do texto"), None)
    body_stroke = body.stroke_ratio if body else 0.0

    def is_bold_factory(b: Block):
        st = styles.get(b.style_id or "")
        if st and st.bold:
            return lambda w, ln: True
        ref = (st.stroke_ratio if st else body_stroke) or body_stroke

        def f(w, ln):
            xh = ln.xheight or (st.xheight_px if st else 0)
            if not ref or not xh or not w.stroke or len(w.text) < 2:
                return False
            return (w.stroke / xh) >= ref * 1.3

        return f

    def is_italic_factory(b: Block):
        st = styles.get(b.style_id or "")
        if st and st.italic:
            return lambda w, ln: True
        feats = [w.slant for ln in b.lines for w in ln.words if len(w.text) >= 3]
        base = statistics.median(feats) if len(feats) >= 5 else 0.0
        base = min(base, 0.08)  # um bloco quase todo em itálico tem base no estilo

        def f(w, ln):
            return len(w.text) >= 3 and (w.slant - base) >= 0.14

        return f

    return is_bold_factory, is_italic_factory
