"""Títulos: o que o detector deixou como parágrafo, coerência no livro
inteiro, estilos trocados e níveis de título."""

from __future__ import annotations

import re
import statistics
from collections import Counter

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..document import DocReport

from ..model import Block, Page, Project, Role, Run, merge_runs
from . import text
from .structure import FLOW_ROLES, box_group

CHAPTER_RE = re.compile(
    r"^(CAP[IÍ]TULO|T[IÍ]TULO|PARTE|LIVRO|CHAPTER|PART|BOOK)\s+([IVXLCDM]+|\d+|[A-ZÀ-Ü]+)\.?$", re.I)


NUMBERED_RE = re.compile(r"^(\d+(?:\.\d+)*)\.?(?:\s|\t)")


ARTICLE_TITLE = re.compile(r"^Arts?\.\s*\d+[\w\-–]*(,.*)?$")


BOX_TOPIC = re.compile(r"PONTOS? RELEVANTES? PARA DEBATE", re.I)


def body_style(pages: list[Page]) -> str | None:
    c = Counter(b.style_id for p in pages for b in p.blocks if b.role == Role.BODY and b.style_id)
    return c.most_common(1)[0][0] if c else None


def fix_stray_styles(pages: list[Page], project: Project) -> None:
    """Parágrafo de várias linhas com letra do tamanho do corpo que caiu num
    grupo de estilo de título (fonte maior): volta ao estilo do corpo."""
    body_sid = body_style(pages)
    if body_sid is None:
        return
    bx = [ln.xheight for p in pages for b in p.blocks if b.style_id == body_sid for ln in b.lines if ln.xheight]
    if not bx:
        return
    body_xh = statistics.median(bx)
    body_size = project.styles[body_sid].size if body_sid in project.styles else 0
    for p in pages:
        for b in p.blocks:
            if b.role not in FLOW_ROLES or b.style_id in (None, body_sid) or len(b.lines) < 2:
                continue
            st = project.styles.get(b.style_id)
            if st is None or st.size <= body_size * 1.15:
                continue
            xs = [ln.xheight for ln in b.lines if ln.xheight]
            if xs and abs(statistics.median(xs) - body_xh) <= body_xh * 0.25 and text.ends_sentence(b.text.rstrip()):
                b.style_id = body_sid


def _short_heading_text(t: str) -> bool:
    return bool(t) and len(t) < 130 and not re.search(r"[.;:,]$", t) and "http" not in t


def promote_titles(pages: list[Page], project: Project) -> None:
    """Títulos que o detector deixou como parágrafo:

    * seção numerada de uma linha ("7.2.11 Atenuante inominada") numa fonte
      diferente da do corpo;
    * linha curta em fonte maior que a do corpo ("Calúnia", nomes de crime
      em até duas linhas);
    * artigo de lei sozinho na linha ("Art. 146", "Art. 161, § 1.º, I");
    * o tema logo abaixo de "PONTO RELEVANTE PARA DEBATE";
    * o mesmo texto curto que, na maior parte do livro, já é título
      ("SÍNTESE", "Sujeito ativo")."""
    body_sid = body_style(pages)
    body_size = project.styles[body_sid].size if body_sid in project.styles else 0

    def free(b: Block) -> bool:
        return b.role == Role.BODY and not b.role_locked and not b.user_edited and b.lines

    for p in pages:
        blocks = p.ordered_blocks()
        for i, b in enumerate(blocks):
            if not free(b):
                continue
            t = b.text.strip()
            if not _short_heading_text(t) or len(b.lines) > 2:
                continue
            st = project.styles.get(b.style_id or "")
            if len(b.lines) == 1 and NUMBERED_RE.match(t) and re.search(r"[A-Za-zÀ-ÿ]", t) and b.style_id != body_sid:
                b.role = Role.TITLE
            elif st is not None and body_size and st.size >= body_size * 1.2 and len(t) < 130 and re.match(r"[A-ZÀ-Ú]", t):
                b.role = Role.TITLE
            elif len(b.lines) == 1 and len(t) < 60 and ARTICLE_TITLE.match(t):
                b.role = Role.TITLE
            elif (len(b.lines) == 1 and len(t) < 100 and i > 0 and blocks[i - 1].role == Role.TITLE
                  and BOX_TOPIC.search(blocks[i - 1].text) and re.match(r"[A-ZÀ-Ú]", t)):
                b.role = Role.TITLE
    # Coerência: o mesmo texto curto tem o mesmo papel no livro inteiro.
    def tkey(b: Block) -> str:
        return re.sub(r"\d+", "#", " ".join(b.text.split())).casefold()

    roles: dict[str, Counter] = {}
    for p in pages:
        for b in p.blocks:
            if b.role in (Role.TITLE, Role.BODY) and len(b.lines) == 1 and _short_heading_text(b.text.strip()):
                roles.setdefault(tkey(b), Counter())[b.role] += 1
    for p in pages:
        for b in p.blocks:
            if free(b) and len(b.lines) == 1 and _short_heading_text(b.text.strip()):
                c = roles.get(tkey(b))
                if c and c[Role.TITLE] >= 3 and c[Role.TITLE] >= 0.6 * sum(c.values()):
                    b.role = Role.TITLE
    # Coerência de estilo: linha curta (começando em maiúscula, sem pontuação
    # final) num estilo em que a maioria das linhas curtas já é título.
    by_style: dict[str, Counter] = {}
    for p in pages:
        for b in p.blocks:
            if b.style_id and b.role in (Role.TITLE, Role.BODY) and len(b.lines) == 1 and _short_heading_text(b.text.strip()):
                by_style.setdefault(b.style_id, Counter())[b.role] += 1
    for p in pages:
        for b in p.blocks:
            t = b.text.strip()
            if (free(b) and len(b.lines) == 1 and _short_heading_text(t) and len(t) < 100 and re.match(r"[A-ZÀ-Ú]", t)
                    and b.style_id != body_sid):
                c = by_style.get(b.style_id or "")
                if c and c[Role.TITLE] >= 5 and c[Role.TITLE] >= 0.6 * sum(c.values()):
                    b.role = Role.TITLE
    # Dentro da caixa de SÍNTESE, só "SÍNTESE" é título: os itens (mesmo em
    # negrito) são parágrafos.
    synth_boxes = {box_group(b) for p in pages for b in p.blocks
                   if b.role == Role.TITLE and b.box_id and b.text.strip().upper() == "SÍNTESE"}
    for p in pages:
        for b in p.blocks:
            if (b.role == Role.TITLE and b.box_id and box_group(b) in synth_boxes and not b.role_locked
                    and b.text.strip().upper() != "SÍNTESE"):
                b.role = Role.BODY
                b.heading_level = 0


def assign_headings(pages: list[Page], project: Project, rep: DocReport) -> None:
    title_styles = sorted({b.style_id for p in pages for b in p.blocks if b.role == Role.TITLE and b.style_id},
                          # desempate pelo nome: a ordem de um conjunto muda de uma execução para outra
                          key=lambda sid: (-(project.styles[sid].size if sid in project.styles else 0), sid))
    rank = {sid: i for i, sid in enumerate(title_styles)}
    # Reanálise: desfaz a união "CAPÍTULO X" + título feita da vez anterior
    # (o texto dos blocos acabou de ser reconstruído).
    for p in pages:
        for b in p.blocks:
            if "incorporado_ao_titulo" in b.flags:
                b.flags.remove("incorporado_ao_titulo")
                if not b.role_locked:
                    b.role = Role.TITLE
            if "rotulo_capitulo" in b.flags:
                b.flags.remove("rotulo_capitulo")
    promote_titles(pages, project)
    has_doc_title = any(b.kind == "doc_title" for p in pages for b in p.blocks if b.role == Role.TITLE)
    for p in pages:
        blocks = p.ordered_blocks()
        # "CAPÍTULO II" + título do capítulo → um só título de nível 1.
        for i, b in enumerate(blocks):
            if b.role not in (Role.TITLE, Role.BODY) or b.user_edited:
                continue
            t = b.text.strip()
            if CHAPTER_RE.match(t):
                nxt = next((x for x in blocks[i + 1:i + 3] if x.role == Role.TITLE and x.kind == "doc_title"), None)
                prv = next((x for x in reversed(blocks[max(0, i - 2):i]) if x.role == Role.TITLE and x.kind == "doc_title"), None)
                target = nxt or prv
                b.role = Role.TITLE
                b.heading_level = 1
                if target is not None and "rotulo_capitulo" not in target.flags:
                    target.runs = merge_runs([Run(t, bold=any(r.bold for r in b.runs)), Run("\n")] + target.runs)
                    target.flags.append("rotulo_capitulo")
                    b.role = Role.ARTIFACT  # conteúdo já incorporado ao título
                    b.flags.append("incorporado_ao_titulo")
        for b in blocks:
            if b.role != Role.TITLE:
                continue
            rep.headings += 1
            t = b.text.strip()
            m = NUMBERED_RE.match(t)
            if b.kind == "doc_title" or "rotulo_capitulo" in b.flags or CHAPTER_RE.match(t):
                lvl = 1
            elif m:
                lvl = 1 + len(m.group(1).split("."))
            else:
                lvl = (2 if has_doc_title else 1) + rank.get(b.style_id or "", 0)
            b.heading_level = max(1, min(6, lvl))
    # Os níveis começam em 1 (um documento sem títulos de capítulo não fica só com "Título 2").
    used = [b.heading_level for p in pages for b in p.blocks if b.role == Role.TITLE and not b.role_locked]
    if used and min(used) > 1:
        shift = min(used) - 1
        for p in pages:
            for b in p.blocks:
                if b.role == Role.TITLE and not b.role_locked:
                    b.heading_level = max(1, b.heading_level - shift)
