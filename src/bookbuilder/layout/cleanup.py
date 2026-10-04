"""Acertos finais no texto corrido de cada bloco (depois da fusão)."""

from __future__ import annotations

import re
from collections import Counter

from ..model import Block, Page, Role
from . import fixes, fusion
from .structure import FLOW_ROLES, MARGIN_ROLES

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
