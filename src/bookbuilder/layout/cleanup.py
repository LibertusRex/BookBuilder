"""Acertos finais no texto corrido de cada bloco (depois da fusão)."""

from __future__ import annotations

import re
from collections import Counter

from ..model import Block, Page, Role, Run, merge_runs
from . import fixes, fusion
from .structure import FLOW_ROLES, MARGIN_ROLES

LEADER = "." * 8
PAGE_REF = re.compile(r"^[0-9]{1,4}$|^[ivxlcdm]{1,6}$", re.I)
TOC = "entrada_sumario"


def dot_leaders(pages: list[Page]) -> int:
    """Sumários: o pontilhado entre o título e o número da página é lido
    pelo OCR como "palavras" de letras miúdas e confiança quase nula
    ("aaaaaeaeeee", "CESSA E CPI NC") ou grudado no fim do título
    ("FRENTE.....-0enssra"). Da esquerda para a direita ficam as palavras
    do título; o resto, até o número no fim da linha, vira um pontilhado
    só. Retorna as linhas corrigidas."""
    n = 0
    for p in pages:
        for b in p.blocks:
            if b.user_edited or b.role not in FLOW_ROLES + (Role.TITLE,):
                continue
            for ln in b.lines:
                if _leader_line(ln):
                    n += 1
        _split_toc(p)
        for b in p.blocks:
            ws = b.lines[-1].words if b.lines else []
            if (len(ws) >= 2 and ws[-2].text == LEADER and PAGE_REF.match(ws[-1].text)
                    and b.role in FLOW_ROLES + (Role.TITLE,) and TOC not in b.flags):
                b.flags.append(TOC)
    return n


def _leader_line(ln) -> bool:
    ws = ln.words
    if len(ws) < 2 or not PAGE_REF.match(ws[-1].text) or any(w.text == LEADER for w in ws):
        return False
    tall = ws[-1].bbox[3] - ws[-1].bbox[1]  # algarismos: altura das maiúsculas
    keep: list = []
    zone: list = []
    prefix = ""
    for k, w in enumerate(ws[:-1]):
        if zone:
            zone.append(w)
            continue
        m = re.search(r"\.{2,}", w.text)
        if m:
            prefix = w.text[:m.start()]
            zone.append(w)
        elif k == 0 or (w.conf >= 50 and w.bbox[3] - w.bbox[1] >= tall * 0.6):
            keep.append(w)
        else:
            zone.append(w)
    if not zone:
        return False
    junky = sum(1 for w in zone if w.bbox[3] - w.bbox[1] < tall * 0.45 or ".." in w.text)  # pontos miúdos
    span = zone[-1].bbox[2] - zone[0].bbox[0]
    if (junky < max(1, len(zone) * 0.6) and ".." not in zone[0].text) or span < 3 * tall:
        return False  # palavras duvidosas antes de um número, mas não um pontilhado
    first = zone[0]
    if re.search(r"[A-Za-zÀ-ÿ]", prefix):
        keep.append(type(first)(text=prefix, bbox=first.bbox, conf=first.conf))
    lead = type(first)(text=LEADER, bbox=(first.bbox[0], min(w.bbox[1] for w in zone), zone[-1].bbox[2],
                                         max(w.bbox[3] for w in zone)), conf=100.0)
    ln.words = keep + [lead, ws[-1]]
    return True


def _split_toc(p: Page) -> None:
    """Sumário: cada entrada (até a linha que termina no número da página,
    com pontilhado) vira um parágrafo, em vez de um bloco corrido."""
    out: list[Block] = []
    for b in p.blocks:
        ends = [bool(ln.words) and PAGE_REF.match(ln.words[-1].text) is not None for ln in b.lines]
        dotted = sum(any(w.text == LEADER for w in ln.words) for ln in b.lines)
        if b.user_edited or len(b.lines) < 2 or sum(ends) < 2 or not dotted:
            out.append(b)
            continue
        group: list = []
        k = 0
        for ln, end in zip(b.lines, ends):
            group.append(ln)
            if end or ln is b.lines[-1]:
                x0 = min(g.bbox[0] for g in group)
                y0 = min(g.bbox[1] for g in group)
                x1 = max(g.bbox[2] for g in group)
                y1 = max(g.bbox[3] for g in group)
                out.append(Block(id=f"{b.id}-{k}", role=b.role, bbox=(x0, y0, x1, y1), kind=b.kind, lines=group,
                                 conf=b.conf, order=b.order, style_id=b.style_id, flags=list(b.flags)))
                group = []
                k += 1
    p.blocks = out


def toc_entries(pages: list[Page]) -> None:
    """Entradas de sumário: título, tabulação e número da página, todas no
    mesmo formato (o parágrafo leva a tabulação com pontilhado à direita).
    O pontilhado lido pelo OCR sai do texto; palavras do título que só o
    Surya leu antes do pontilhado voltam; o número de ordem lido errado
    ("l.", "4.º") segue a sequência das entradas vizinhas."""
    for p in pages:
        entries = []
        for b in p.ordered_blocks():
            if TOC not in b.flags or b.user_edited or not b.runs:
                continue
            parsed = _toc_split("".join(r.text for r in b.runs if not r.note_ref))
            if parsed is None:
                continue
            title, page = parsed
            s_parsed = _toc_split(fusion.html_to_text(b.surya_html)) if b.surya_html else None
            if s_parsed and s_parsed[1] == page:
                tw, sw = _toc_words(title), _toc_words(s_parsed[0])
                if tw and sw[:len(tw)] == tw and 0 < len(sw) - len(tw) <= 3:
                    title = re.sub(r"\s+", " ", s_parsed[0]).strip()
            low = {t.strip() for r in b.runs if r.low_conf for t in r.text.split()}
            entries.append((b, title, page, low))
        nums = [_toc_number(t) for _, t, _, _ in entries]
        for k, (b, title, page, low) in enumerate(entries):
            first = title.split(" ", 1)
            if nums[k] is not None and len(first) == 2:
                prev = next((nums[j] for j in range(k - 1, -1, -1) if nums[j] and nums[j][0] is not None), None)
                nxt = next((nums[j] for j in range(k + 1, len(nums)) if nums[j] and nums[j][0] is not None), None)
                want = prev[0] + 1 if prev else (nxt[0] - 1 if nxt else None)
                if want and want > 0 and first[0] != f"{want}." and (nums[k][0] is None or nums[k][1]):
                    low.discard(first[0])
                    title = f"{want}. {first[1]}"
            runs = [Run(t + " ", low_conf=t in low) for t in title.split(" ")]
            runs[-1].text = runs[-1].text.rstrip()
            b.runs = merge_runs(runs + [Run("\t"), Run(page)])
            b.disputes = [d for d in b.disputes if d.get("current", "").strip() in title]
            b.continues_prev = False


def _toc_split(t: str) -> tuple[str, str] | None:
    """Texto de uma entrada → (título sem o pontilhado, número da página)."""
    t = re.sub(r"\s+", " ", t).strip()
    m = re.match(r"^(.*?)[\s.…·_]*(?<=[\s.…·_])([0-9]{1,4}|[ivxlcdm]{1,6})$", t, re.I)
    if not m:
        return None
    title = m.group(1).rstrip(" .…·_") if re.search(r"\.{2,}\s*$", m.group(1)) else m.group(1).strip()
    return (title, m.group(2)) if title else None


def _toc_words(t: str) -> list[str]:
    return [w for w in (re.sub(r"\W+", "", x).casefold() for x in t.split()) if w]


def _toc_number(title: str) -> tuple[int | None, bool] | None:
    """Número de ordem no começo da entrada ("3.") → (valor, lido errado?).
    "l." e "I." (algarismo 1 lido como letra) e "4.º" contam como lidos
    errado; o valor certo vem da sequência."""
    tok = title.split(" ", 1)[0]
    m = re.fullmatch(r"(\d{1,3})\.", tok)
    if m:
        return int(m.group(1)), False
    m = re.fullmatch(r"(\d{1,3})\.?[º°ª]", tok)
    if m:
        return int(m.group(1)), True
    if re.fullmatch(r"[lI|]\.", tok):
        return None, True
    return None


def drop_repeated_tails(pages: list[Page]) -> None:
    """O recorte do Surya às vezes avança sobre o bloco seguinte e a primeira
    linha dele aparece também no fim deste (destacada, só o Surya leu)."""
    def norm(t: str) -> str:
        return re.sub(r"\W+", " ", t).strip().casefold()

    for p in pages:
        blocks = [b for b in p.ordered_blocks() if b.runs]
        for a, b in zip(blocks, blocks[1:]):
            # Bloco que é só o começo de outro bloco sobreposto a ele (a mesma
            # linha lida duas vezes por regiões do layout que se cruzam).
            whole = norm("".join(r.text for r in a.runs))
            overlap = min(a.bbox[3], b.bbox[3]) - max(a.bbox[1], b.bbox[1])
            if (a.role in FLOW_ROLES and not a.user_edited and len(whole) >= 20 and len(whole.split()) >= 4
                    and overlap > 0 and norm("".join(r.text for r in b.runs)).startswith(whole)):
                a.role = Role.ARTIFACT
                a.flags.append("repetido")
                continue
            if not a.surya_html:
                continue
            k = len(a.runs)
            while k > 0 and ((a.runs[k - 1].low_conf and a.runs[k - 1].text.strip() not in fusion.PLACEHOLDERS)
                             or not a.runs[k - 1].text.strip()):
                k -= 1
            tail = norm("".join(r.text for r in a.runs[k:]))
            if len(tail) < 20 or len(tail.split()) < 4:
                continue
            if norm("".join(r.text for r in b.runs)).startswith(tail):
                a.runs = a.runs[:k]
                if a.runs:
                    a.runs[-1].text = a.runs[-1].text.rstrip()
                a.disputes = [d for d in a.disputes if norm(d.get("current", "")) not in tail]


def final_text_fixes(pages: list[Page], vocab: Counter, lex) -> None:
    """Acertos finais no texto (fundido ou só do Tesseract):

    * traço entre espaços (ou antes de pontuação) é sempre travessão "—",
      também onde só o Surya leu o traço (ele às vezes devolve meia-risca);
    * inciso "I" lido como "l"/"1"/"|", "(inciso), I)" → "(inciso I)";
    * "§ 1.%" → "§ 1.º", "155,$ 2" → "155, § 2", vírgula colada;
    * acento que falta numa palavra que o livro escreve acentuada
      ("vitima" → "vítima", "impeto" → "ímpeto")."""
    amap = fixes.accent_map(vocab)

    def fix(t: str) -> str:
        t = fixes.fix_roman_l_text(t)
        t = fixes.fix_text(t)
        t = fixes.restore_accents(t, amap, lex)
        t = fixes.fix_icon_prefix(t, lex)
        return re.sub(r"(^|(?<=\s))–(?=\s|$|[,;.)])", "—", t)

    for p in pages:
        for b in p.blocks:
            if b.user_edited or b.role in MARGIN_ROLES or b.role in (Role.FIGURE, Role.ARTIFACT):
                continue
            apply_to_runs(b, fix)
            # Inciso romano sozinho ("I).", "II,") lido só pelo Surya: o contexto
            # confirma; sai o destaque e a divergência.
            roman = re.compile(r"\(?[IVX]{1,4}[).,;:]*")
            cleared = {r.text.strip() for r in b.runs if r.low_conf and roman.fullmatch(r.text.strip())}
            if cleared:
                for r in b.runs:
                    if r.low_conf and r.text.strip() in cleared:
                        r.low_conf = False
                b.disputes = [d for d in b.disputes if d.get("current", "").strip() not in cleared]
                if not any(r.low_conf for r in b.runs) and "baixa_confianca" in b.flags:
                    b.flags.remove("baixa_confianca")


def apply_to_runs(b: Block, fn) -> None:
    """Aplica ``fn`` ao texto inteiro do bloco (os acertos podem atravessar a
    divisa entre trechos destacados e não destacados) e devolve cada edição
    ao trecho em que ela começa. Chamadas de nota ficam intactas."""
    import difflib

    runs = b.runs
    if not runs:
        return
    owner: list[int] = []
    chars: list[str] = []
    for k, r in enumerate(runs):
        if r.note_ref:
            continue
        for ch in r.text:
            owner.append(k)
            chars.append(ch)
    old = "".join(chars)
    new = fn(old)
    if new == old:
        return
    pieces: list[list[str]] = [[] for _ in runs]
    sm = difflib.SequenceMatcher(None, old, new, autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            for i in range(i1, i2):
                pieces[owner[i]].append(old[i])
        else:
            k = owner[i1] if i1 < len(owner) else (owner[-1] if owner else 0)
            if op == "insert" and i1 > 0 and i1 <= len(owner) and (i1 == len(owner) or owner[i1 - 1] != owner[i1]):
                k = owner[i1 - 1]  # inserção na divisa: fica com o trecho anterior
            pieces[k].append(new[j1:j2])
    for k, r in enumerate(runs):
        if not r.note_ref:
            r.text = "".join(pieces[k])
    b.runs = [r for r in runs if r.note_ref or r.text]


def fix_words(pages: list[Page], vocab: Counter) -> tuple[int, int]:
    """Correções palavra a palavra antes de montar o texto: §, inciso "I",
    travessões e confusões de caracteres pelo vocabulário do próprio livro.
    Devolve (símbolos corrigidos, correções pelo vocabulário)."""
    symbol_fixes = corrections = 0
    for p in pages:
        for b in p.blocks:
            for ln in b.lines:
                for w in ln.words:
                    if w.superscript and not any(ch.isalnum() for ch in w.text):
                        w.superscript = False  # traço/pontuação marcados como sobrescrito em versões anteriores
            if b.user_edited or b.role in MARGIN_ROLES:
                continue  # margens: o texto se repete igual em todas as páginas; não "corrigir" traços etc.
            # § no fim de uma linha e o número no começo da seguinte também contam.
            symbol_fixes += fixes.fix_section_signs([w for ln in b.lines for w in ln.words])
            symbol_fixes += fixes.fix_roman_l([w for ln in b.lines for w in ln.words])
            for ln in b.lines:
                symbol_fixes += fixes.fix_dashes(ln.words, ln.xheight)
                for w in ln.words:
                    if " " in w.text and w.text not in ("[trecho ilegível]",):
                        # Palavras do OCR nunca têm espaço: desfaz separações de versões anteriores.
                        w.text = w.text.replace(" ", "")
                    if fixes.vocab_correct(w, vocab):
                        corrections += 1
    return symbol_fixes, corrections
