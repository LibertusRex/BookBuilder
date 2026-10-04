"""Operações de revisão, independentes da interface.

* Mudar a função de um bloco (corpo, título, cabeçalho, rodapé, número de
  página, nota...) neste bloco, nesta página, num intervalo de páginas ou no
  documento inteiro. Fora do bloco escolhido, a mudança alcança os blocos
  *equivalentes*: mesma faixa da página (alto/pé/meio) e mesmo texto com os
  números mascarados, ou o mesmo estilo tipográfico.
* Editar o texto de um bloco.
* Ajustar um estilo (fonte, tamanho, negrito, itálico) — vale para todos os
  blocos daquele estilo de uma vez.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from rapidfuzz import fuzz

from .layout.running import normalize
from .model import Block, Page, Project, Role, Run, Style, merge_runs

SCOPES = ("bloco", "pagina", "intervalo", "documento")
SCOPE_LABELS = {
    "bloco": "Só este bloco",
    "pagina": "Blocos equivalentes nesta página",
    "intervalo": "Blocos equivalentes no intervalo de páginas",
    "documento": "Blocos equivalentes no documento inteiro",
}


def _band(p: Page, b: Block) -> str:
    cy = (b.bbox[1] + b.bbox[3]) / 2 / max(1, p.height)
    return "top" if cy < 0.15 else ("bottom" if cy > 0.85 else "mid")


@dataclass
class Match:
    page: Page
    block: Block


def equivalents(project: Project, page: Page, block: Block, pages: Iterable[Page]) -> list[Match]:
    """Blocos parecidos com ``block`` nas páginas dadas."""
    ref_band = _band(page, block)
    ref_text = normalize(block.text or " ".join(ln.text for ln in block.lines))
    out: list[Match] = []
    for p in pages:
        for b in p.blocks:
            if b is block:
                out.append(Match(p, b))
                continue
            if _band(p, b) != ref_band:
                continue
            t = normalize(b.text or " ".join(ln.text for ln in b.lines))
            same_text = bool(ref_text) and fuzz.ratio(ref_text, t) >= 85
            same_style = ref_band == "mid" and b.style_id == block.style_id and b.role == block.role
            if same_text or same_style:
                out.append(Match(p, b))
    return out


def set_role(project: Project, page: Page, block: Block, role: Role, scope: str = "bloco",
             page_range: tuple[int, int] | None = None) -> int:
    """Aplica a função e trava os blocos alterados contra a reanálise.
    ``page_range`` (índices 0-based, inclusivos, na ordem do projeto) para o
    escopo "intervalo". Retorna quantos blocos mudaram."""
    if scope == "bloco":
        targets = [Match(page, block)]
    elif scope == "pagina":
        targets = equivalents(project, page, block, [page])
    elif scope == "intervalo":
        a, b = page_range or (0, len(project.pages) - 1)
        sel = [p for i, p in enumerate(project.pages) if a <= i <= b and not p.excluded]
        targets = equivalents(project, page, block, sel)
    else:
        targets = equivalents(project, page, block, [p for p in project.pages if not p.excluded])
    n = 0
    for m in targets:
        if m.block.role != role or not m.block.role_locked:
            m.block.role = role
            m.block.role_locked = True
            if role == Role.TITLE and not m.block.heading_level:
                m.block.heading_level = 2
            if role != Role.TITLE:
                m.block.heading_level = 0
            n += 1
    return n


def set_heading_level(block: Block, level: int) -> None:
    block.role = Role.TITLE
    block.heading_level = max(1, min(6, level))
    block.role_locked = True


def set_text(block: Block, runs: list[Run]) -> None:
    block.runs = runs
    block.user_edited = True
    if "baixa_confianca" in block.flags:
        block.flags.remove("baixa_confianca")
    if "ilegivel" in block.flags:
        block.flags.remove("ilegivel")


def update_style(style: Style, family: str | None = None, size: float | None = None,
                 bold: bool | None = None, italic: bool | None = None) -> None:
    if family is not None:
        style.family_override = family if family != style.detected_family else ""
    if size is not None:
        style.size_override = size if abs(size - style.size_pt) > 0.01 else 0.0
    if bold is not None:
        style.bold = bold
    if italic is not None:
        style.italic = italic


def assign_style(project: Project, block: Block, style_id: str) -> None:
    if style_id in project.styles:
        block.style_id = style_id


def blocks_with_style(project: Project, style_id: str) -> list[tuple[Page, Block]]:
    return [(p, b) for p in project.pages for b in p.blocks if b.style_id == style_id]


def page_filter(project: Project, kind: str) -> list[int]:
    """Índices das páginas que atendem ao filtro da revisão."""
    out = []
    for i, p in enumerate(project.pages):
        flags = set(p.flags)
        for b in p.blocks:
            flags.update(b.flags)
        ok = {
            "todas": True,
            "avisos": bool(flags) or p.status.value == "erro" or bool(p.duplicate_of),
            "baixa": "baixa_confianca" in flags or "ilegivel" in flags,
            "encoberto": "encoberto" in flags or "sobreposicao" in flags,
            "margem": "verificar_margem" in flags or "margem_reclassificada" in flags,
            "notas": any(b.role == Role.FOOTNOTE for b in p.blocks) or "nota_sem_chamada" in flags,
            "duplicadas": bool(p.duplicate_of),
            "divergencias": any(b.disputes for b in p.blocks),
            "erro": p.status.value == "erro",
            "excluidas": p.excluded,
        }.get(kind, True)
        if ok:
            out.append(i)
    return out


FILTERS = {
    "todas": "Todas as páginas",
    "avisos": "Com algum aviso",
    "baixa": "Baixa confiança / ilegível",
    "encoberto": "Texto encoberto",
    "margem": "Cabeçalho/rodapé a conferir",
    "notas": "Com notas de rodapé",
    "divergencias": "Divergências Tesseract × Surya",
    "duplicadas": "Capturas repetidas",
    "erro": "Com falha",
    "excluidas": "Excluídas",
}


# ---------------------------------------------------------------------------
# Divergências Tesseract × Surya


def open_disputes(project: Project) -> list[tuple[int, Block, int]]:
    """(índice da página, bloco, índice da divergência) de todas as
    divergências abertas, na ordem do documento."""
    out = []
    for i, p in enumerate(project.pages):
        if p.excluded:
            continue
        for b in p.ordered_blocks():
            for k in range(len(b.disputes)):
                out.append((i, b, k))
    return out


def _replace_current(block: Block, current: str, new_text: str) -> bool:
    """Troca, nos trechos destacados do bloco, o texto ``current`` por
    ``new_text`` (sem destaque). Retorna False se não encontrou."""
    runs = block.runs
    target = " ".join(current.split())
    for i, r in enumerate(runs):
        if not r.low_conf or not r.text.strip():
            continue
        k = i
        acc = ""
        while k < len(runs) and (runs[k].low_conf or not runs[k].text.strip()):
            acc += runs[k].text
            if " ".join(acc.split()) == target:
                lead = runs[i].text[: len(runs[i].text) - len(runs[i].text.lstrip())]
                trail = runs[k].text[len(runs[k].text.rstrip()):]
                base = runs[i]
                repl = []
                if new_text:
                    repl = [Run(lead + new_text + trail, base.bold, base.italic, base.underline,
                                False, False, False, None)]
                elif lead or trail:
                    repl = [Run(" ")]
                block.runs = merge_runs(runs[:i] + repl + runs[k + 1:])
                # Espaço duplo deixado por uma remoção
                for rr in block.runs:
                    rr.text = re.sub(r"  +", " ", rr.text)
                return True
            k += 1
    return False


def apply_dispute(project: Project, block: Block, index: int, choice: str, everywhere: bool = False) -> list[str]:
    """Resolve a divergência com o texto do Tesseract ou do Surya. Com
    ``everywhere``, resolve igualmente todas as divergências iguais do livro.
    Retorna os uids das páginas alteradas."""
    from .layout.fusion import SEP_KEY

    d = block.disputes[index]
    k = d["tess"] + SEP_KEY + d["surya"]
    targets: list[tuple[Page | None, Block, dict]] = []
    if everywhere:
        for p in project.pages:
            for b in p.blocks:
                for dd in b.disputes:
                    if dd["tess"] == d["tess"] and dd["surya"] == d["surya"]:
                        targets.append((p, b, dd))
    else:
        page = next((p for p in project.pages if block in p.blocks), None)
        targets.append((page, block, d))
    changed = []
    for p, b, dd in targets:
        new_text = dd["tess"] if choice == "tess" else dd["surya"]
        if _replace_current(b, dd["current"], new_text) or dd["current"] == new_text:
            b.choices[k] = choice
            b.choices["_fonte:" + k] = "usuario"
            b.disputes = [x for x in b.disputes if x is not dd]
            if not any(r.low_conf for r in b.runs) and "baixa_confianca" in b.flags:
                b.flags.remove("baixa_confianca")
            if p is not None:
                changed.append(p.uid)
    return changed
