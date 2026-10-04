"""Cabeçalhos, rodapés e números de página: decisão pelo documento inteiro.

Um bloco só vira cabeçalho/rodapé quando há evidência de *repetição*:

* está numa faixa de margem (alto ou pé da página) e
* um texto parecido (números mascarados) aparece na mesma posição em várias
  páginas próximas — inclusive alternando entre páginas pares e ímpares, ou
  mudando por capítulo; ou
* é um número (ou "Página N de M", "N%") que cresce de página para página.

Títulos de capítulo, primeiras linhas do texto e notas de rodapé perto da
margem continuam sendo conteúdo: um título que aparece uma vez em corpo maior
não é cabeçalho, ainda que o detector de layout tenha sugerido isso.

O resultado também identifica artefatos da tela (barra de status do celular)
e o número impresso de cada página.
"""

from __future__ import annotations

import re
import statistics
import unicodedata
from collections import Counter
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from ..model import Block, Page, Role

BAND = 0.14  # fração da altura da página considerada margem
STATUS_BAR_RE = re.compile(r"\b\d{1,2}:\d{2}\b|\b(seg|ter|qua|qui|sex|s[áa]b|dom|mon|tue|wed|thu|fri|sat|sun)\b[.,]?\s+\d", re.I)
ROMAN_RE = re.compile(r"^[ivxlcdm]+$", re.I)
NUMBER_PATTERNS = [
    re.compile(r"^(?:p[áa]g(?:ina)?\.?|page|p\.)?\s*(\d{1,5})(?:\s*(?:of|de|/)\s*(\d{1,5}))?$", re.I),
    re.compile(r"^[-–—]\s*(\d{1,5})\s*[-–—]$"),
    re.compile(r"^(\d{1,5})\s*[|·•]\s*.+$"),
    re.compile(r"^.+\s*[|·•]\s*(\d{1,5})$"),
]
PERCENT_RE = re.compile(r"^\d{1,3}\s*%$")


def normalize(text: str) -> str:
    t = unicodedata.normalize("NFKD", text.casefold())
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    t = re.sub(r"\d+", "#", t)
    t = re.sub(r"[^\w#% ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def page_number_value(text: str) -> int | None:
    t = text.strip()
    for pat in NUMBER_PATTERNS:
        m = pat.match(t)
        if m:
            return int(m.group(1))
    if ROMAN_RE.match(t) and len(t) <= 6:
        return _roman(t)
    return None


def _roman(s: str) -> int | None:
    vals = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
    total, prev = 0, 0
    for ch in reversed(s.lower()):
        v = vals[ch]
        total = total - v if v < prev else total + v
        prev = max(prev, v)
    return total or None


@dataclass
class Candidate:
    page_idx: int  # posição entre as páginas ativas
    block: Block
    pos: str  # "top" | "bottom"
    rel_y: float
    rel_x: float
    norm: str
    xheight: float
    number: int | None


@dataclass
class RunningResult:
    headers: dict[str, list[str]] = field(default_factory=dict)  # texto normalizado → exemplos
    footers: dict[str, list[str]] = field(default_factory=dict)
    page_number_pos: str = ""  # "top", "bottom" ou ""
    page_number_pattern: str = ""  # ex.: "Page # of 2245"
    printed_numbers: dict[str, int] = field(default_factory=dict)  # uid → número impresso
    changed: int = 0
    notes: list[str] = field(default_factory=list)


def _xh(b: Block) -> float:
    vals = [ln.xheight for ln in b.lines if ln.xheight]
    return statistics.median(vals) if vals else 0.0


def collect(pages: list[Page]) -> list[Candidate]:
    cands: list[Candidate] = []
    for i, p in enumerate(pages):
        if not p.height:
            continue
        for b in p.blocks:
            if b.role in (Role.FIGURE, Role.TABLE) or b.role_locked:
                continue
            for f in ("margem_deslocada", "margem_reclassificada", "verificar_margem"):
                if f in b.flags:
                    b.flags.remove(f)
                    if f == "margem_reclassificada" and b.kind in ("header", "footer", "number"):
                        b.role = {"header": Role.HEADER, "footer": Role.FOOTER, "number": Role.PAGE_NUMBER}[b.kind]
            cy = (b.bbox[1] + b.bbox[3]) / 2 / p.height
            if cy <= BAND:
                pos = "top"
            elif cy >= 1 - BAND:
                pos = "bottom"
            else:
                continue
            if len(b.lines) > 2:
                continue  # parágrafo inteiro perto da margem: conteúdo
            text = " ".join(ln.text for ln in b.lines)
            if len(text) > 110:
                continue  # cabeçalhos e rodapés correntes são curtos; texto longo repetido é conteúdo
            cands.append(Candidate(i, b, pos, cy, (b.bbox[0] + b.bbox[2]) / 2 / max(1, p.width),
                                   normalize(text), _xh(b), page_number_value(text)))
    return cands


def _clusters(cands: list[Candidate]) -> list[list[Candidate]]:
    clusters: list[list[Candidate]] = []
    reps: list[Candidate] = []
    for c in cands:
        if not c.norm:
            continue
        placed = False
        for k, r in enumerate(reps):
            if r.pos != c.pos or abs(r.rel_y - c.rel_y) > 0.035:
                continue
            if fuzz.ratio(r.norm, c.norm) >= 85:
                clusters[k].append(c)
                placed = True
                break
        if not placed:
            reps.append(c)
            clusters.append([c])
    return clusters


def _dense(cl: list[Candidate]) -> bool:
    pages = sorted({c.page_idx for c in cl})
    if len(pages) < 3:
        return False
    span = pages[-1] - pages[0] + 1
    return len(pages) / span >= 0.3


def _sequential(cl: list[Candidate]) -> float:
    """Fração de pares consecutivos (em ordem de página) em que o número
    cresce de forma compatível com a distância entre as páginas."""
    pts = sorted((c.page_idx, c.number) for c in cl if c.number is not None)
    if len(pts) < 3:
        return 0.0
    good = 0
    for (p1, n1), (p2, n2) in zip(pts, pts[1:]):
        d = p2 - p1
        if 0 < n2 - n1 <= max(3, d * 4):
            good += 1
    return good / (len(pts) - 1)


def classify(pages: list[Page]) -> RunningResult:
    res = RunningResult()
    cands = collect(pages)
    body_xh = _body_xheight(pages)

    # 1) Barra de status do celular/tablet no topo extremo.
    for c in cands:
        if c.pos == "top" and c.rel_y < 0.03 and STATUS_BAR_RE.search(" ".join(ln.text for ln in c.block.lines)):
            _set(c.block, Role.ARTIFACT, res)
    cands = [c for c in cands if c.block.role != Role.ARTIFACT]

    # Textos que também aparecem no meio das páginas (subtítulos recorrentes
    # como "Tentativa", "Objeto jurídico") são conteúdo, não margem corrente.
    mid_counts: Counter = Counter()
    for p in pages:
        for b in p.blocks:
            if not p.height or len(b.lines) > 2:
                continue
            cy = (b.bbox[1] + b.bbox[3]) / 2 / p.height
            if BAND < cy < 1 - BAND:
                mid_counts[normalize(" ".join(ln.text for ln in b.lines))] += 1

    for cl in _clusters(cands):
        pages_in = {c.page_idx for c in cl}
        if not _dense(cl):
            continue
        if mid_counts.get(cl[0].norm, 0) >= max(2, len(cl) * 0.5):
            continue
        med_xh = statistics.median([c.xheight for c in cl if c.xheight] or [0])
        seq = _sequential(cl)
        varying_numbers = len({c.number for c in cl if c.number is not None}) > 1
        is_percent = all(PERCENT_RE.match(" ".join(ln.text for ln in c.block.lines).strip()) for c in cl)
        if seq >= 0.6 and varying_numbers and not is_percent:
            role = Role.PAGE_NUMBER
            res.page_number_pos = cl[0].pos
            res.page_number_pattern = _pattern(cl)
            for c in cl:
                if c.number is not None:
                    res.printed_numbers[pages[c.page_idx].uid] = c.number
        else:
            role = Role.HEADER if cl[0].pos == "top" else Role.FOOTER
        for c in cl:
            # Mesmo texto em corpo bem maior que o normal: é título, não cabeçalho.
            if med_xh and c.xheight > med_xh * 1.3 and c.block.kind in ("doc_title", "paragraph_title"):
                continue
            _set(c.block, role, res)
        bucket = res.headers if role == Role.HEADER else res.footers
        bucket.setdefault(cl[0].norm, []).append(" ".join(ln.text for ln in cl[0].block.lines))
        res.notes.append(f"{role.label}: “{' '.join(ln.text for ln in cl[0].block.lines)[:60]}” em {len(pages_in)} páginas")

    # 1b) Mesmo texto de um cabeçalho/rodapé confirmado, na mesma faixa, mas em
    # outra altura (página com deslocamento): mesma função.
    clusters = _clusters(cands)
    dense = [cl for cl in clusters if _dense(cl)]
    for cl in clusters:
        if cl in dense:
            continue
        for c in cl:
            for d in dense:
                ref = d[0]
                if ref.pos == c.pos and ref.block.role in (Role.HEADER, Role.FOOTER, Role.PAGE_NUMBER)                         and fuzz.ratio(ref.norm, c.norm) >= 92 and c.xheight <= (ref.xheight or c.xheight) * 1.2:
                    _set(c.block, ref.block.role, res)
                    if c.block.role == Role.PAGE_NUMBER and c.number is not None:
                        res.printed_numbers[pages[c.page_idx].uid] = c.number
                    if "margem_deslocada" not in c.block.flags:
                        c.block.flags.append("margem_deslocada")
                    break

    # 2) Rótulos do detector sem repetição: confirmar ou devolver ao conteúdo.
    repeated = {id(c.block) for cl in dense for c in cl} | {
        id(c.block) for cl in clusters for c in cl if "margem_deslocada" in c.block.flags}
    for p in pages:
        for b in p.blocks:
            if b.role_locked or id(b) in repeated or b.role not in (Role.HEADER, Role.FOOTER, Role.PAGE_NUMBER):
                continue
            text = " ".join(ln.text for ln in b.lines)
            if b.role == Role.PAGE_NUMBER and page_number_value(text) is not None:
                continue  # número isolado na margem: mantém
            if PERCENT_RE.match(text.strip()):
                _set(b, Role.FOOTER, res)  # progresso de leitura do aplicativo
                continue
            # Isolado: só continua como margem se for curto e pequeno.
            xh = _xh(b)
            if len(text.split()) <= 12 and (not body_xh or xh <= body_xh * 1.05):
                if "verificar_margem" not in b.flags:
                    b.flags.append("verificar_margem")
                continue
            _set(b, Role.TITLE if b.kind in ("doc_title", "paragraph_title") else Role.BODY, res)
            b.flags.append("margem_reclassificada")

    for p in pages:
        if p.uid in res.printed_numbers:
            p.printed_number = str(res.printed_numbers[p.uid])
    return res


def _pattern(cl: list[Candidate]) -> str:
    text = " ".join(ln.text for ln in cl[0].block.lines)
    return re.sub(r"\d+", "#", text, count=1)


def _set(b: Block, role: Role, res: RunningResult) -> None:
    if b.role != role:
        b.role = role
        res.changed += 1


def _body_xheight(pages: list[Page]) -> float:
    vals = [ln.xheight for p in pages[:200] for b in p.blocks if b.role == Role.BODY for ln in b.lines if ln.xheight]
    return statistics.median(vals) if vals else 0.0
