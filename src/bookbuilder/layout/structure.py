"""Estrutura do texto corrido: parágrafos partidos pelo layout, marcadores
de lista, alinhamento, parágrafos que continuam na página seguinte e
quadros que atravessam páginas."""

from __future__ import annotations

import dataclasses
import re
import statistics
from collections import Counter

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..document import DocReport

from ..model import Block, Page, Role
from . import text

FLOW_ROLES = (Role.BODY, Role.LIST_ITEM, Role.QUOTE)


MARGIN_ROLES = (Role.HEADER, Role.FOOTER, Role.PAGE_NUMBER)


def box_group(b: Block) -> str | None:
    """Identificador do quadro para a exportação (une continuações)."""
    if not b.box_id:
        return None
    return b.box_id.split("=")[-1]


def line_gap(b: Block) -> float:
    gaps = [n.bbox[1] - a.bbox[3] for a, n in zip(b.lines, b.lines[1:])]
    return statistics.median(gaps) if gaps else 0.0


MARKER_ONLY = re.compile(r"^\(?([a-zA-Z]{1,2}(\.\d+)*|\d{1,3}(\.\d+)*\.?[ºª°]?|[ivxlcIVXLC]{1,5})[).]$")


FUNCTION_WORDS = {"a", "o", "as", "os", "e", "ou", "de", "do", "da", "dos", "das", "em", "no", "na", "nos", "nas",
                  "por", "pelo", "pela", "pelos", "pelas", "com", "sem", "para", "ao", "aos", "à", "às", "um", "uma",
                  "que", "se", "sob", "sobre", "entre", "art.", "arts.", "§", "n.", "v.", "p."}


def _join_blocks(p: Page, a: Block, b: Block, same_line: bool = False) -> None:
    """Junta o bloco ``b`` ao fim de ``a`` (``same_line``: ``a`` é um marcador
    isolado à esquerda da primeira linha de ``b``)."""
    if same_line:
        first = b.lines[0]
        mk = a.lines[0]
        first.words = [w for ln in a.lines for w in ln.words] + first.words
        first.bbox = (min(mk.bbox[0], first.bbox[0]), min(mk.bbox[1], first.bbox[1]),
                      max(mk.bbox[2], first.bbox[2]), max(mk.bbox[3], first.bbox[3]))
        a.lines = b.lines
        a.role = Role.LIST_ITEM
    else:
        a.lines = a.lines + b.lines
    a.bbox = (min(a.bbox[0], b.bbox[0]), min(a.bbox[1], b.bbox[1]), max(a.bbox[2], b.bbox[2]), max(a.bbox[3], b.bbox[3]))
    a.flags = sorted(set(a.flags) | set(b.flags))
    a.occluded = a.occluded or b.occluded
    # Releituras do Surya dos dois pedaços também se juntam.
    a.surya_html = (a.surya_html + " " + b.surya_html) if (a.surya_html and b.surya_html) else None
    a.disputes, a.choices = [], {**a.choices, **b.choices}
    p.blocks.remove(b)


def _merge_same_row_lines(b: Block) -> bool:
    """Linhas do bloco na mesma altura viram uma só, da esquerda para a
    direita (o marcador "d)" separado do texto do item ficava depois dele)."""
    changed = False
    lines = list(b.lines)
    i = 0
    while i < len(lines):
        j = i + 1
        while j < len(lines):
            a, c = lines[i], lines[j]
            h = min(a.bbox[3] - a.bbox[1], c.bbox[3] - c.bbox[1])
            v = min(a.bbox[3], c.bbox[3]) - max(a.bbox[1], c.bbox[1])
            hx = min(a.bbox[2], c.bbox[2]) - max(a.bbox[0], c.bbox[0])
            if h > 0 and v > h * 0.4 and hx <= h * 0.25:  # encostadas ou quase (pontilhado de sumário)
                left, right = (a, c) if a.bbox[0] <= c.bbox[0] else (c, a)
                a.words = left.words + right.words
                a.bbox = (min(a.bbox[0], c.bbox[0]), min(a.bbox[1], c.bbox[1]),
                          max(a.bbox[2], c.bbox[2]), max(a.bbox[3], c.bbox[3]))
                a.baseline = max(a.baseline, c.baseline)
                a.xheight = a.xheight or c.xheight
                a.capheight = a.capheight or c.capheight
                lines.pop(j)
                changed = True
                continue
            j += 1
        i += 1
    if changed:
        b.lines = sorted(lines, key=lambda ln: (ln.bbox[1], ln.bbox[0]))
    return changed


def _attach_floating_markers(p: Page) -> int:
    """Um marcador isolado ("b)") na altura de uma linha do meio de outro
    bloco (a ordem de leitura separou a coluna dos marcadores da coluna do
    texto): divide o bloco nessa linha e o marcador abre o novo item."""
    import copy

    n = 0
    for m in [b for b in p.blocks if b.role in FLOW_ROLES and len(b.lines) == 1 and not b.user_edited]:
        mt = m.lines[0].text.strip()
        if not MARKER_ONLY.match(mt):
            continue
        ml = m.lines[0]
        xh = ml.xheight or (ml.bbox[3] - ml.bbox[1]) * 0.5
        host, k = None, -1
        for t in p.blocks:
            if t is m or t.role not in FLOW_ROLES or t.user_edited or t.box_id != m.box_id:
                continue
            for i, ln in enumerate(t.lines):
                if abs(ln.bbox[1] - ml.bbox[1]) < xh and 0 <= ln.bbox[0] - ml.bbox[2] <= xh * 6:
                    host, k = t, i
                    break
            if host is not None:
                break
        if host is None or k <= 0:
            continue  # k == 0: marcador antes da primeira linha, tratado na junção por pares
        new = copy.deepcopy(host)
        new.id = host.id + "-i" + str(k)
        new.lines = host.lines[k:]
        host.lines = host.lines[:k]
        for b in (host, new):
            b.bbox = (min(ln.bbox[0] for ln in b.lines), min(ln.bbox[1] for ln in b.lines),
                      max(ln.bbox[2] for ln in b.lines), max(ln.bbox[3] for ln in b.lines))
            b.surya_html = None  # a releitura era do bloco inteiro: não dá para dividir
            b.disputes, b.choices = [], {}
        new.continues_prev = False
        first = new.lines[0]
        first.words = list(ml.words) + first.words
        first.bbox = (min(ml.bbox[0], first.bbox[0]), min(ml.bbox[1], first.bbox[1]), first.bbox[2],
                      max(ml.bbox[3], first.bbox[3]))
        new.bbox = (min(new.bbox[0], ml.bbox[0]),) + tuple(new.bbox[1:])
        new.role = Role.LIST_ITEM
        p.blocks.remove(m)
        # Ordem: o novo item logo depois do bloco dividido.
        ordered = [b for b in p.ordered_blocks() if b is not new]
        idx = ordered.index(host) + 1
        ordered.insert(idx, new)
        p.blocks.append(new)
        for i, b in enumerate(ordered):
            b.order = i
        n += 1
    return n


def _column_right(p: Page, col) -> float:
    rights = sorted(ln.bbox[2] for b in p.blocks if b.role in FLOW_ROLES and b.column == col for ln in b.lines)
    return rights[int(len(rights) * 0.9)] if rights else 0.0


def merge_split_paragraphs(pages: list[Page]) -> int:
    """O detector de layout às vezes parte um parágrafo em duas regiões. Junta:

    * blocos consecutivos da mesma coluna quando o primeiro não termina a
      frase e o segundo continua o texto (começa em minúscula, ou o primeiro
      termina em hífen);
    * linhas seguintes de um parágrafo ou item de lista ("a) … arts. 312 a" |
      "326, em …"): o primeiro bloco termina com a linha cheia até a margem,
      sem fim de frase, e o segundo vem logo abaixo, no espaçamento normal
      de linhas, alinhado ou com o recuo deslocado do item;
    * marcador de lista isolado ("c)") com o texto do item à direita, na
      mesma linha."""
    n = 0
    for p in pages:
        n += _attach_floating_markers(p)
        for b in p.blocks:
            if b.role in FLOW_ROLES and not b.user_edited and len(b.lines) > 1 and _merge_same_row_lines(b):
                if MARKER_ONLY.match(b.lines[0].words[0].text if b.lines[0].words else "") and b.role == Role.BODY:
                    b.role = Role.LIST_ITEM
        blocks = p.ordered_blocks()
        i = 0
        while i < len(blocks) - 1:
            a, b = blocks[i], blocks[i + 1]
            if (a.role in FLOW_ROLES and b.role in FLOW_ROLES and not a.user_edited and not b.user_edited
                    and a.column == b.column and a.box_id == b.box_id and a.lines and b.lines
                    and "entrada_sumario" not in a.flags + b.flags):  # sumário: uma entrada por parágrafo
                at = " ".join(ln.text for ln in a.lines).rstrip()
                bt = " ".join(ln.text for ln in b.lines).lstrip()
                xh = statistics.median([ln.xheight for ln in a.lines + b.lines if ln.xheight] or [10.0])
                # Marcador isolado + texto do item na mesma linha.
                if (len(a.lines) == 1 and MARKER_ONLY.match(at) and abs(b.lines[0].bbox[1] - a.lines[0].bbox[1]) < xh
                        and 0 <= b.lines[0].bbox[0] - a.bbox[2] <= xh * 6 and not text.BULLET_START.match(bt)):
                    _join_blocks(p, a, b, same_line=True)
                    blocks.pop(i + 1)
                    n += 1
                    continue
                gap = b.bbox[1] - a.bbox[3]
                normal = line_gap(a) or xh * 0.6
                overlap_x = min(a.bbox[2], b.bbox[2]) - max(a.bbox[0], b.bbox[0])
                continues = text.ends_with_hyphen(at) or (not text.ends_sentence(at) and bt[:1].islower())
                if continues and 0 <= gap <= normal + xh * 1.2 and overlap_x > 0 and not text.BULLET_START.match(bt):
                    _join_blocks(p, a, b)
                    blocks.pop(i + 1)
                    n += 1
                    continue
                # Linhas seguintes do mesmo parágrafo/item.
                lgap = b.lines[0].bbox[1] - a.lines[-1].bbox[3]
                col_right = _column_right(p, a.column)
                full = a.lines[-1].bbox[2] >= col_right - xh * 2
                a_left = min(ln.bbox[0] for ln in a.lines)
                aligned = a_left - xh <= b.lines[0].bbox[0] <= a_left + xh * 6
                open_end = not text.ends_sentence(at) and not at.endswith(";")
                dash = bt[:1] in "–—"
                if (open_end and full and aligned and -xh * 0.3 <= lgap <= normal + xh * 0.6 and overlap_x > 0
                        and (dash or not text.BULLET_START.match(bt))):
                    _join_blocks(p, a, b)
                    blocks.pop(i + 1)
                    n += 1
                    continue
            i += 1
    return n


def recompute_alignment(pages: list[Page]) -> None:
    """Alinhamento medido em relação à própria coluna: justificado quando as
    linhas (menos a última) chegam à margem direita da coluna."""
    for p in pages:
        flow = [b for b in p.blocks if b.role in FLOW_ROLES and b.lines]
        by_col: dict[tuple, list[Block]] = {}
        for b in flow:
            # Quadros têm margens próprias, mais estreitas que as do corpo.
            by_col.setdefault((b.column, box_group(b)), []).append(b)
        for blocks in by_col.values():
            rights = sorted(ln.bbox[2] for b in blocks for ln in (b.lines[:-1] or b.lines))
            if not rights:
                continue
            col_right = rights[int(len(rights) * 0.9)] if len(rights) > 3 else rights[-1]
            lefts = sorted(min(ln.bbox[0] for ln in b.lines) for b in blocks)
            col_left = lefts[len(lefts) // 10]
            # Recuo de primeira linha típico (para não confundir com centralização).
            indents = [b.lines[0].bbox[0] - min(ln.bbox[0] for ln in b.lines[1:]) for b in blocks if len(b.lines) >= 2]
            indents = [x for x in indents if x > 5]
            typ_indent = statistics.median(indents) if indents else 0.0
            for b in blocks:
                xh = statistics.median([ln.xheight for ln in b.lines if ln.xheight] or [10.0])
                tol = max(6.0, xh * 0.8)
                if len(b.lines) >= 2:
                    body = b.lines[:-1]
                    at_right = sum(abs(ln.bbox[2] - col_right) < tol for ln in body) / len(body)
                    if at_right >= 0.7:
                        b.align = "justify"
                        continue
                    centers = [(ln.bbox[0] + ln.bbox[2]) / 2 for ln in b.lines]
                    mid = (col_left + col_right) / 2
                    if all(abs(c - mid) < tol * 2 for c in centers) and min(ln.bbox[0] for ln in b.lines) > col_left + xh * 2:
                        b.align = "center"
                    else:
                        b.align = "left"
                else:
                    ln = b.lines[0]
                    lgap, rgap = ln.bbox[0] - col_left, col_right - ln.bbox[2]
                    is_indent = typ_indent and abs(lgap - typ_indent) < max(tol, typ_indent * 0.25)
                    if (not is_indent and lgap > xh * 3 and rgap > xh * 3
                            and abs(lgap - rgap) < max(tol * 1.5, (col_right - col_left) * 0.015)):
                        b.align = "center"
                    elif lgap > xh * 6 and rgap < tol:
                        b.align = "right"
                    else:
                        b.align = "left"


def style_alignment(pages: list[Page]) -> None:
    """Parágrafos de 1–2 linhas (e pedaços que continuam em outra página)
    seguem o alinhamento dominante do seu estilo: num texto justificado, a
    última linha fica naturalmente à esquerda, então "justificado" é o certo."""
    from collections import Counter

    counts: dict[str, Counter] = {}
    for p in pages:
        for b in p.blocks:
            if b.role in FLOW_ROLES and len(b.lines) >= 3 and b.style_id:
                counts.setdefault(b.style_id, Counter())[b.align] += 1
    dominant: dict[str, str] = {}
    for sid, c in counts.items():
        al, k = c.most_common(1)[0]
        if k >= 0.6 * sum(c.values()):
            dominant[sid] = al
    for p in pages:
        for b in p.blocks:
            if b.role in FLOW_ROLES and len(b.lines) < 3 and b.align == "left" and dominant.get(b.style_id or "") == "justify":
                b.align = "justify"


def continuations(pages: list[Page], vocab: Counter, rep: DocReport) -> None:
    for prev, cur in zip(pages, pages[1:]):
        a_list = [b for b in prev.ordered_blocks() if b.role in FLOW_ROLES or b.role == Role.TITLE]
        b_list = [b for b in cur.ordered_blocks() if b.role in FLOW_ROLES or b.role == Role.TITLE]
        if not a_list or not b_list:
            continue
        a, b = a_list[-1], b_list[0]
        b.continues_prev = False
        if "juntar_sem_espaco" in b.flags:
            b.flags.remove("juntar_sem_espaco")
        if a.role not in FLOW_ROLES or b.role not in FLOW_ROLES:
            continue
        at, bt = a.text.rstrip(), b.text.lstrip()
        if not at or not bt:
            continue
        hyphen = text.ends_with_hyphen(at)
        # Termina em artigo/preposição/conjunção: a frase continua com certeza.
        tail_word = at.split()[-1].casefold() if at.split() else ""
        strong = hyphen or bt[:1].islower() or bt[:1] in ",;)–—" or tail_word in FUNCTION_WORDS
        if bool(a.box_id) != bool(b.box_id):
            # Quadro aberto embaixo cujo texto segue na página seguinte sem a
            # moldura detectada: só com indício forte.
            if not (a.box_id and a.box_id.split("=")[0].endswith("v") and strong):
                continue
        xh = statistics.median([ln.xheight for ln in b.lines if ln.xheight] or [10.0])
        first_offset = b.indent_left + (b.indent_first if len(b.lines) > 1 else 0.0)
        indented = first_offset > xh * 1.2
        open_end = hyphen or not text.ends_sentence(at)
        broken_dash = hyphen and bt[:1] in "–—"  # "felicida-" | "— primeiro…": o fim da palavra se perdeu
        if not open_end or (indented and not strong) or (text.BULLET_START.match(bt) and not broken_dash):
            continue
        if not (hyphen or bt[:1].islower() or bt[:1] in ",;)–—" or not text.ends_sentence(at)):
            continue
        b.continues_prev = True
        rep.continuations += 1
        if broken_dash:
            # A palavra partida não termina na página seguinte (o resto ficou
            # fora da foto): fica como está, em destaque para a revisão.
            _flag_last_word(a)
            continue
        if hyphen:
            left = at.split()[-1][:-1]
            right = bt.split()[0]
            if not text.keep_hyphen(left, right, vocab):
                _drop_trailing_hyphen(a)
            b.flags.append("juntar_sem_espaco")


def _flag_last_word(b: Block) -> None:
    for k in range(len(b.runs) - 1, -1, -1):
        r = b.runs[k]
        t = r.text.rstrip()
        if not t:
            continue
        cut = t.rfind(" ") + 1
        if cut > 0:
            b.runs[k:k + 1] = [dataclasses.replace(r, text=t[:cut]), dataclasses.replace(r, text=t[cut:], low_conf=True)]
        else:
            r.low_conf = True
        if "palavra_incompleta" not in b.flags:
            b.flags.append("palavra_incompleta")
        return


def _drop_trailing_hyphen(b: Block) -> None:
    for r in reversed(b.runs):
        if r.text.strip():
            t = r.text.rstrip()
            if t and t[-1] in text.HYPHENS:
                r.text = t[:-1]
            return


def unify_boxes(pages: list[Page]) -> None:
    """Quadro aberto embaixo numa página seguido de quadro aberto em cima na
    página seguinte = o mesmo quadro (um só quadro na exportação)."""
    prev_raw: str | None = None
    prev_id: str | None = None
    for p in pages:
        for b in p.ordered_blocks():
            if b.role not in FLOW_ROLES and b.role != Role.TITLE:
                continue
            raw = b.box_id
            if raw is None:
                prev_raw = prev_id = None
                continue
            raw = raw.split("=")[0]
            if prev_raw and prev_raw != raw and prev_raw.endswith("v") and "^" in raw:
                b.box_id = f"{raw}={prev_id}"
            else:
                b.box_id = raw
            prev_raw = raw
            prev_id = b.box_id.split("=")[-1]


def even_body_paragraphs(pages: list[Page], body_style: str | None = None) -> int:
    """Páginas fotografadas: a curvatura e a perspectiva da foto deixam o
    recuo e as margens de cada parágrafo um pouco diferentes (e alguns
    parecem alinhados à esquerda). Os parágrafos do corpo do texto ficam
    todos justificados, sem recuo lateral, com o mesmo recuo de primeira
    linha (o do livro, ou nenhum) e no estilo do corpo do texto (``body_style``:
    mesma fonte e tamanho; a foto faz alguns parecerem de outro corpo). Blocos realmente recuados (epígrafe,
    citação), centralizados ou curtos ficam como estão. Retorna os ajustados."""
    # Páginas de texto corrido (folha de rosto, sumário e créditos ficam de fora).
    scanned = [p for p in pages if p.light and any(b.role == Role.BODY and len(b.lines) >= 3 for b in p.blocks)]
    for p in scanned:
        for b in p.blocks:
            # Parágrafo (ou continuação) que começa com travessão não é item de lista.
            if (b.role == Role.LIST_ITEM and not b.role_locked and len(b.lines) >= 2 and b.lines[0].words
                    and b.lines[0].words[0].text[:1] in "—–"):
                b.role = Role.BODY
    cands = [b for p in scanned for b in p.blocks
             if b.role == Role.BODY and b.lines and not b.user_edited and not b.box_id
             and "entrada_sumario" not in b.flags and b.align not in ("center", "right")]
    xhs = [ln.xheight for b in cands for ln in b.lines if ln.xheight]
    if not cands or not xhs:
        return 0
    xh = statistics.median(xhs)
    firsts = [b.indent_first for b in cands if b.indent_first > xh * 2]
    common = statistics.median(firsts) if firsts else 0.0
    n = 0
    for b in cands:
        if b.indent_left > max(common, xh * 3) * 1.5:
            continue  # bloco recuado de verdade
        b.align = "justify"
        if body_style:
            b.style_id = body_style
        b.indent_left = 0.0
        b.indent_right = 0.0
        b.indent_first = common if (common and b.indent_first > common * 0.5) else 0.0
        n += 1
    return n
