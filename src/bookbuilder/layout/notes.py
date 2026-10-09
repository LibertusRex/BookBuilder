"""Notas de rodapé: detecção no pé da página e ligação às chamadas."""

from __future__ import annotations

import re
import statistics

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..document import DocReport

from ..model import Block, Page, Role, Run, merge_runs
from .structure import FLOW_ROLES

NOTE_MARK_RE = re.compile(r"^\s*(\d{1,3}|[*†‡§]{1,3})(?:[.)]|\s|(?=[A-ZÀ-Üa-zà-ü“\"(]))")


def body_xh(pages: list[Page]) -> float:
    vals = [ln.xheight for p in pages for b in p.blocks if b.role == Role.BODY for ln in b.lines if ln.xheight]
    return statistics.median(vals) if vals else 0.0


def detect_footnotes(pages: list[Page], body_xh: float) -> None:
    """Blocos no pé da página, em corpo menor, começando com uma chamada
    ("1", "*"), abaixo de todo o texto corrido: notas de rodapé."""
    if not body_xh:
        return
    for p in pages:
        flow = [b for b in p.blocks if b.role in FLOW_ROLES and not b.box_id]
        if not flow:
            continue
        for b in p.blocks:
            if "entrada_sumario" in b.flags and not b.role_locked:
                if b.role == Role.FOOTNOTE:
                    b.role = Role.BODY  # análise anterior
                continue  # "5. Bem-estar emocional …… 153" no pé do sumário não é nota
            if b.role_locked or b.role not in (Role.BODY, Role.FOOTER):
                continue
            if b.role == Role.FOOTER and "verificar_margem" not in b.flags:
                continue  # rodapé repetido ("12 Tal Ben-Shahar"): o número é o da página
            xh = statistics.median([ln.xheight for ln in b.lines if ln.xheight] or [0])
            if not xh or xh > body_xh * 0.88:
                continue
            first = b.lines[0].text if b.lines else ""
            if not NOTE_MARK_RE.match(first):
                continue
            bigger = [o for o in flow if o is not b and statistics.median(
                [ln.xheight for ln in o.lines if ln.xheight] or [0]) > body_xh * 0.88]
            below_all = all(o.bbox[3] <= b.bbox[1] + 5 for o in bigger)
            if below_all and (b.bbox[1] + b.bbox[3]) / 2 > p.height * 0.5:
                b.role = Role.FOOTNOTE


def link_footnotes(pages: list[Page], rep: DocReport) -> None:
    prev_note: Block | None = None
    for p in pages:
        for b in p.blocks:  # aviso de uma análise anterior (o bloco pode ter deixado de ser nota)
            if "nota_sem_chamada" in b.flags:
                b.flags.remove("nota_sem_chamada")
        notes = [b for b in p.ordered_blocks() if b.role == Role.FOOTNOTE]
        refs: dict[str, list[Run]] = {}
        for b in p.ordered_blocks():
            if b.role in FLOW_ROLES or b.role in (Role.TITLE, Role.CAPTION, Role.QUOTE):
                for r in b.runs:
                    if r.superscript and r.text.strip():
                        refs.setdefault(r.text.strip(), []).append(r)
        for nb in notes:
            rep.footnotes += 1
            first = nb.runs[0] if nb.runs else None
            m = NOTE_MARK_RE.match(nb.text)
            if m and first is not None:
                label = m.group(1)
                key = f"{p.uid}:{label}"
                nb.note_label = key
                # Remove a chamada do início do texto da nota.
                cut = m.end(1)
                _strip_prefix(nb, cut)
                targets = refs.get(label)
                if targets:
                    for r in targets:
                        r.note_ref = key
                else:
                    rep.footnotes_unlinked += 1
                    if "nota_sem_chamada" not in nb.flags:
                        nb.flags.append("nota_sem_chamada")
                prev_note = nb
            elif prev_note is not None and nb is notes[0]:
                # Sem chamada no topo da área de notas: continua a nota anterior.
                nb.continues_prev = True
                nb.note_label = prev_note.note_label
            else:
                rep.footnotes_unlinked += 1
                if "nota_sem_chamada" not in nb.flags:
                    nb.flags.append("nota_sem_chamada")


def _strip_prefix(b: Block, n: int) -> None:
    out: list[Run] = []
    remaining = n
    for r in b.runs:
        if remaining <= 0:
            out.append(r)
            continue
        if len(r.text) <= remaining:
            remaining -= len(r.text)
            continue
        out.append(Run(r.text[remaining:], r.bold, r.italic, r.underline, r.superscript, r.subscript, r.low_conf, r.note_ref))
        remaining = 0
    if out:
        out[0].text = out[0].text.lstrip(" .)\t")
    b.runs = merge_runs(out)
