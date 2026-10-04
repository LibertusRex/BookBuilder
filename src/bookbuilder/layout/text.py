"""Reconstrução do texto de um bloco: linhas visuais → parágrafo contínuo.

O ponto delicado é o hífen no fim da linha. Em português ele pode ser:

* divisão silábica do tipógrafo ("crimi-/nalidade") → unir sem hífen;
* hífen da própria palavra ("contra-/ataque", "acreditava-/se") → manter.

A decisão usa, nesta ordem: pronomes oblíquos (ênclise/mesóclise), prefixos
que exigem hífen, o vocabulário do próprio documento (a palavra aparece
inteira em outro lugar? aparece com hífen no meio da linha?) e, por fim, a
regra geral: divisão silábica.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

from ..model import Block, Line, Run, Word, merge_runs

HYPHENS = "-‐‑­¬"
CLITICS = {
    "se", "me", "te", "lhe", "lhes", "o", "a", "os", "as", "lo", "la", "los", "las",
    "no", "na", "nos", "nas", "vos", "mo", "ma", "mos", "mas", "to", "ta", "tos", "tas",
    "lho", "lha", "lhos", "lhas", "ei", "ás", "á", "emos", "eis", "ão", "ia", "ias", "íamos", "íeis", "iam",
}
HYPHEN_PREFIXES = {
    "ex", "vice", "pós", "pré", "pró", "recém", "além", "aquém", "sem", "bem", "mal", "grão", "grã",
    "contra", "anti", "auto", "micro", "semi", "sobre", "supra", "ultra", "infra", "intra", "extra",
    "neo", "proto", "pseudo", "arqui", "mini", "multi", "pluri", "inter", "super", "hiper", "sub",
    "co", "macro", "maxi", "mega", "tele", "pan", "circum", "ab", "ob", "sob", "ad",
}
WORD_RE = re.compile(r"[^\W\d_]+(?:[-‐‑][^\W\d_]+)*", re.UNICODE)
# Romanos só na forma válida ("civil." não é marcador); ordinais "2.º)" também.
BULLET_START = re.compile(r"^([•●▪■◦○\-–—*·]|\(?[a-zA-Z]{1,2}(\.\d+)?\)|\(?\d{1,3}[.)]|\(?\d{1,3}\.?[ºª°]\)"
                          r"|(?=[ivxlcIVXLC])(?i:c{0,3}(?:xc|xl|l?x{0,3})(?:ix|iv|v?i{0,3}))[.)])\s")
SENTENCE_END = (".", "!", "?", ":", "…", "”", '"', "»", ")")


def vocabulary(lines: list[Line]) -> Counter:
    """Palavras inteiras (minúsculas) vistas no meio das linhas do documento."""
    c: Counter = Counter()
    for ln in lines:
        ws = ln.words
        for i, w in enumerate(ws):
            if i == len(ws) - 1 and w.text.endswith(tuple(HYPHENS)):
                continue
            for m in WORD_RE.findall(w.text):
                c[m.casefold()] += 1
    return c


def _strip_letters(s: str) -> str:
    return "".join(ch for ch in s if ch.isalpha())


def keep_hyphen(left: str, right: str, vocab: Counter) -> bool:
    """``left`` sem o hífen final; ``right`` = primeira palavra da linha seguinte."""
    lw = _strip_letters(left.split("-")[-1]).casefold()
    rw_full = right
    rw = re.match(r"[^\W\d_]+", right)
    rws = rw.group(0).casefold() if rw else ""
    if not lw or not rws:
        return True
    if right.startswith(tuple(HYPHENS)):
        return True  # convenção de repetir o hífen no início da linha
    if rw_full[:1].isupper() and left[:1].isupper():
        return True  # nomes compostos: Jean-Paul, Rio-Grandense
    joined = (lw + rws)
    hyph = f"{lw}-{rws}"
    if rws in CLITICS and len(lw) >= 3:
        # "acreditava-se", "dar-lhe", "fá-lo"; mas não "a-" + "o" improvável
        return vocab.get(joined, 0) == 0 or vocab.get(hyph, 0) > 0
    if vocab.get(hyph, 0) > vocab.get(joined, 0):
        return True
    if vocab.get(joined, 0) > 0:
        return False
    if lw in HYPHEN_PREFIXES and (rws[:1] == "h" or rws[:1] == lw[-1:] or lw in {"ex", "vice", "pós", "pré", "pró", "recém", "além", "aquém", "sem", "grão", "grã"}):
        return True
    return False


def _norm(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def word_runs(w: Word, line: Line, bold: bool, italic: bool, low_conf: float) -> list[Run]:
    lc = w.conf < low_conf
    if w.tail_sup and w.text.endswith(w.tail_sup) and len(w.text) > len(w.tail_sup):
        main = w.text[: -len(w.tail_sup)]
        return [
            Run(_norm(main), bold=bold, italic=italic, underline=w.underline, low_conf=lc),
            Run(_norm(w.tail_sup), superscript=True, low_conf=lc),
        ]
    return [Run(_norm(w.text), bold=bold, italic=italic, underline=w.underline,
                superscript=w.superscript, subscript=w.subscript, low_conf=lc)]


def build_runs(block: Block, vocab: Counter, is_bold, is_italic, low_conf: float = 60.0) -> list[Run]:
    """Texto contínuo do bloco com formatação por palavra.

    ``is_bold(word, line)`` e ``is_italic(word, line)`` vêm da análise de
    estilos (comparam com o corpo do texto). Palavras curtas ("de", "e")
    entre duas palavras em itálico/negrito herdam a formatação, para não
    picotar expressões como *Lições de direito penal*."""
    lines = block.lines
    flat = []
    for li, ln in enumerate(lines):
        for wi, w in enumerate(ln.words):
            # Marcas de texto encoberto vizinhas viram uma só.
            if w.text == "[…]" and flat and flat[-1][2].text == "[…]":
                continue
            flat.append((li, wi, w, ln))
    bold = [bool(is_bold(w, ln)) for _, _, w, ln in flat]
    ital = [bool(is_italic(w, ln)) for _, _, w, ln in flat]
    for arr in (bold, ital):
        for i in range(1, len(arr) - 1):
            if not arr[i] and arr[i - 1] and arr[i + 1] and len(flat[i][2].text) <= 3:
                arr[i] = True
        # Pontuação colada herda do vizinho.
        for i in range(len(arr)):
            if not arr[i] and not any(ch.isalnum() for ch in flat[i][2].text) and i > 0 and arr[i - 1]:
                arr[i] = True
    runs: list[Run] = []
    for k, (li, wi, w, ln) in enumerate(flat):
        last_in_line = wi == len(ln.words) - 1
        wr = word_runs(w, ln, bold[k], ital[k], low_conf)
        sep = " "
        if not last_in_line:
            nxt_w = ln.words[wi + 1]
            gap = nxt_w.bbox[0] - w.bbox[2]
            if nxt_w.superscript and gap < (ln.xheight or 10) * 0.8:
                sep = ""  # chamada sobrescrita colada à palavra
            elif block.align != "justify" and ln.xheight and gap > ln.xheight * 3.0:
                sep = "	"  # espaço largo (tabulação) entre número e título, colunas
        else:
            if li == len(lines) - 1:
                sep = ""
            else:
                nxt = lines[li + 1].words[0].text if lines[li + 1].words else ""
                base = [r for r in wr if not r.superscript]
                txt = base[-1].text if base else ""
                if txt and txt[-1] in HYPHENS and len(txt) > 1 and nxt[:1].isalpha() and not w.tail_sup:
                    if keep_hyphen(txt[:-1], nxt, vocab):
                        if txt[-1] != "-":
                            _replace_last_char(wr, "-")
                    else:
                        _replace_last_char(wr, "")
                    sep = ""
                elif txt.endswith("/"):
                    sep = ""  # URLs e barras quebradas
        runs.extend(wr)
        if sep:
            runs.append(Run(sep))
    return merge_runs(_attach_spaces(runs))


def _replace_last_char(wr: list[Run], repl: str) -> None:
    for r in reversed(wr):
        if r.superscript:
            continue
        r.text = r.text[:-1] + repl
        return


def _attach_spaces(runs: list[Run]) -> list[Run]:
    """Espaços simples herdam a formatação do trecho anterior quando ele e o
    seguinte têm a mesma formatação (evita quebrar itálico em vários trechos)."""
    out: list[Run] = []
    for i, r in enumerate(runs):
        if r.text == " " and out and i + 1 < len(runs):
            prev, nxt = out[-1], runs[i + 1]
            if (prev.bold, prev.italic, prev.underline) == (nxt.bold, nxt.italic, nxt.underline) and not prev.superscript:
                out.append(Run(" ", bold=prev.bold, italic=prev.italic, underline=prev.underline))
                continue
        out.append(r)
    return out


def ends_sentence(text: str) -> bool:
    t = text.rstrip()
    return bool(t) and t.endswith(SENTENCE_END)


def ends_with_hyphen(text: str) -> bool:
    t = text.rstrip()
    return bool(t) and t[-1] in HYPHENS
