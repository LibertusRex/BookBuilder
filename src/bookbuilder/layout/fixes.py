"""Correções pontuais e verificáveis de erros típicos do OCR.

Nenhuma correção inventa conteúdo: elas usam evidência da própria imagem
(pontos da numeração detectados nos pixels) ou do próprio documento (a forma
corrigida aparece várias vezes em outros lugares). Palavras corrigidas ficam
marcadas como de baixa confiança, para revisão.
"""

from __future__ import annotations

import re
from collections import Counter

import cv2
import numpy as np

from ..model import Line, Word

NUMBERING_RE = re.compile(r"^[\d.]+$")
ROMAN_CONTEXT = re.compile(r"^(CAP[IÍ]TULO|T[IÍ]TULO|PARTE|LIVRO|SE[CÇ][AÃ]O|CHAPTER|PART|BOOK|SECTION|ANEXO|AP[EÊ]NDICE)$", re.I)
ROMAN_GLYPHS = str.maketrans({"|": "I", "!": "I", "l": "I", "1": "I", "]": "I", "[": "I", "í": "I", "Í": "I"})


def fix_numbering(word: Word, gray: np.ndarray, xh: float, baseline: int) -> bool:
    """Reconstrói numerações como "2.1.2.1" quando o OCR perde ou desloca os
    pontos (comum em fontes de título finas). Usa só os pixels: componentes
    altos são os algarismos (na ordem lida pelo OCR) e componentes pequenos
    na linha de base são os pontos. Só altera se a quantidade de algarismos
    bater com a leitura do OCR."""
    if not NUMBERING_RE.match(word.text):
        return False
    digits = [c for c in word.text if c.isdigit()]
    if len(digits) < 2:
        return False
    x0, y0, x1, y1 = word.bbox
    pad = 2
    gx, gy = max(0, x0 - pad), max(0, y0 - pad)
    crop = gray[gy:y1 + pad, gx:x1 + pad]
    if crop.size == 0:
        return False
    _, ink = cv2.threshold(crop, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    n, _, st, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    tall, dots = [], []
    for i in range(1, n):
        cx, cy, cw, chh, area = st[i]
        if area < 2:
            continue
        if chh >= xh * 0.8:
            tall.append(gx + cx + cw / 2)
        elif chh <= max(3, xh * 0.4) and cw <= max(3, xh * 0.5) and gy + cy + chh >= baseline - xh * 0.35:
            dots.append(gx + cx + cw / 2)
    if len(tall) != len(digits):
        return False
    items = sorted([(x, d) for x, d in zip(sorted(tall), digits)] + [(x, ".") for x in dots])
    text = "".join(c for _, c in items)
    text = re.sub(r"\.{2,}", ".", text).lstrip(".")
    if text != word.text:
        word.text = text
        return True
    return False


def fix_roman_after_label(words: list[Word]) -> int:
    n = 0
    for a, b in zip(words, words[1:]):
        if (ROMAN_CONTEXT.match(a.text) and b.text and not b.text.isdigit()
                and all(ch in "IVXLCDM|!l1][íÍ" for ch in b.text) and any(ch in "|!l][íÍ" for ch in b.text)):
            t = b.text.translate(ROMAN_GLYPHS)
            if t != b.text and re.fullmatch(r"[IVXLCDM]+", t):
                b.text = t
                n += 1
    return n


# Pares de confusão comuns em fontes de título e de corpo.
CONFUSIONS = [("G", "C"), ("C", "G"), ("rn", "m"), ("m", "rn"), ("cl", "d"), ("li", "h"), ("0", "O"), ("O", "0"),
              ("1", "l"), ("l", "1"), ("I", "l"), ("l", "I"), ("e", "c"), ("c", "e"), ("ó", "o"), ("Ó", "O")]
_letters = re.compile(r"^([^\w]*)([\w\-]+)([^\w]*)$", re.UNICODE)


def vocab_correct(word: Word, vocab: Counter, min_count: int = 3, max_conf: float = 90.0) -> bool:
    """Troca uma confusão de caracteres quando a forma corrigida aparece pelo
    menos ``min_count`` vezes no documento e a forma lida não aparece em
    nenhum outro lugar."""
    if word.conf >= max_conf:
        return False
    m = _letters.match(word.text)
    if not m:
        return False
    pre, core, post = m.groups()
    key = core.casefold()
    if vocab.get(key, 0) > 1 or len(core) < 4:
        return False
    best, best_n = None, 0
    for a, b in CONFUSIONS:
        start = 0
        while True:
            i = core.find(a, start)
            if i < 0:
                break
            cand = core[:i] + b + core[i + len(a):]
            cnt = vocab.get(cand.casefold(), 0)
            if cnt > best_n:
                best, best_n = cand, cnt
            start = i + 1
    if best and best_n >= min_count:
        word.text = pre + best + post
        word.conf = min(word.conf, 59.0)  # fica sinalizada para revisão
        return True
    return False


def fix_title_lines(lines: list[Line], gray: np.ndarray) -> None:
    for ln in lines:
        for w in ln.words:
            fix_numbering(w, gray, ln.xheight or 10.0, ln.baseline)
        fix_roman_after_label(ln.words)



SECTION_RE = re.compile(r"^([(\[]?)([S$5&]{1,2})$")
NUM_START = re.compile(r"^\d")
DASH_ONLY = re.compile(r"^[-–—‐]+([,;.)]*)$")


def fix_section_signs(words: list[Word]) -> int:
    """O OCR lê o sinal de parágrafo "§" como "S", "$", "S$" ou "SS". Só é
    trocado quando o sinal está sozinho e seguido de um número ("S 2.º" →
    "§ 2.º"): em português, "S" ou "$" soltos antes de número não ocorrem.
    "SS"/"$$" viram "§§" (plural)."""
    n = 0
    for a, b in zip(words, words[1:]):
        if a.text.lstrip("([") in ("§", "§§") and NUM_START.match(b.text):
            a.conf = max(a.conf, 85.0)
            continue
        m = SECTION_RE.match(a.text)
        if m and NUM_START.match(b.text) and m.group(2) not in ("5", "55"):
            a.text = m.group(1) + ("§§" if m.group(2) in ("SS", "$$") else "§")
            a.conf = max(a.conf, 85.0)  # regra segura: não precisa de destaque
            n += 1
    return n


ROMAN_L = re.compile(r"^[l1|!]([),.;:]*)$")
# Mesmo caso no texto corrido (já fundido com o Surya).
INCISO_TEXT = re.compile(r"((?:\bincisos?|\bcaput,?|\d\.?[ºª],)\s+)[l1|!](?=[),.;:])")


def fix_roman_l_text(text: str) -> str:
    return INCISO_TEXT.sub(r"\1I", text)


TEXT_FIXES = [
    # "(inciso), I)", "(inciso);I);", "(inciso]).I)." → "(inciso I)"
    (re.compile(r"\((incisos?)[\])\.,;\s]*([IVX]+)\)"), r"(\1 \2)"),
    # "I Te II" → "I e II"
    (re.compile(r"\b([IVX]+) [Tt]e ([IVX]+)\b"), r"\1 e \2"),
    # "155,$  2." → "155, § 2."
    (re.compile(r"(\d),\s*\$\s+(?=\d)"), r"\1, § "),
    # "§ 1.%", "§ 2.9" → "§ 1.º", "§ 2.º"
    (re.compile(r"(§\s*\d+)\.[%9](?!\d)"), r"\1.º"),
    # Vírgula colada na palavra seguinte: "247,I e II", "3.3,“b”" (números como 1,5 ficam)
    (re.compile(r"(?<=[\w)\]”»])(,)(?=[A-Za-zÀ-ÿ“«(])"), r"\1 "),
    # Espaço logo depois da aspa de abertura ou antes da de fechamento: "“ a”" → "“a”"
    (re.compile(r"(?<=\w)([“«])\s+(?=\w)"), r" \1"),  # "ou“ nomen" → "ou “nomen"
    (re.compile(r"([“«])\s+(?=\w)"), r"\1"),
    (re.compile(r"(?<=\w)\s+([”»])"), r"\1"),
]


def fix_text(text: str) -> str:
    for rx, repl in TEXT_FIXES:
        text = rx.sub(repl, text)
    return text


def _plain(s: str) -> str:
    import unicodedata

    return "".join(ch for ch in unicodedata.normalize("NFKD", s) if not unicodedata.combining(ch))


def accent_map(vocab: Counter, min_count: int = 5) -> dict[str, str]:
    """Forma sem acento → forma acentuada do próprio livro ("vitima" →
    "vítima"), quando a acentuada é frequente e a sem acento quase não
    aparece na leitura do Tesseract (que acerta bem os acentos). Não depende
    do dicionário: "vitima" e "impeto" existem como verbos, mas o livro
    escreve "vítima" centenas de vezes."""
    best: dict[str, tuple[int, str]] = {}
    for w, c in vocab.items():
        p = _plain(w)
        keys = {p}
        if "ñ" in w:
            keys.add(_plain(w.replace("ñ", "nh")))  # "Munhoz" → "Muñoz"
        for k in keys:
            if k != w and c >= min_count and (k not in best or c > best[k][0]):
                best[k] = (c, w)
    # Só palavras de 5+ letras que o livro nunca escreve sem acento ("peca",
    # "so" e citações em espanhol/italiano ficam como estão).
    return {p: w for p, (c, w) in best.items() if vocab.get(p, 0) == 0 and len(p) >= 5}


WORD_TOKEN = re.compile(r"[A-Za-zÀ-ÿ]+")


def restore_accents(text: str, amap: dict[str, str], lex=None) -> str:
    def repl(m):
        w = m.group(0)
        low = w.casefold()
        if low not in amap:
            return w
        acc = amap[low]
        if w.isupper():
            return acc.upper()
        if w[0].isupper():
            return acc[0].upper() + acc[1:]
        return acc

    # Endereços web (e pedaços de endereço quebrado na linha: "a-b-c-d") ficam intactos.
    return " ".join(tok if ("/" in tok or "www" in tok or tok.count("-") >= 3) else WORD_TOKEN.sub(repl, tok)
                    for tok in text.split(" "))
INCISO_CONTEXT = re.compile(r"^(incisos?|caput,?|\d+\.?[ºª],?|[IVX]+,)$")


def fix_roman_l(words: list[Word]) -> int:
    """Inciso "I" lido como "l": "inciso l)", "§ 1.º, l)" → "I"."""
    n = 0
    for prev, w in zip(words, words[1:]):
        m = ROMAN_L.match(w.text)
        if m and INCISO_CONTEXT.match(prev.text):
            w.text = "I" + m.group(1)
            n += 1
    return n


def fix_dashes(words: list[Word], xh: float) -> int:
    """Traço sozinho entre espaços nunca é hífen nem meia-risca: é travessão
    "—" (a largura medida pelo OCR varia demais para distinguir os dois)."""
    n = 0
    for w in words:
        m = DASH_ONLY.match(w.text)
        if not m:
            continue
        new = "—" + m.group(1)
        w.conf = max(w.conf, 85.0)
        if new != w.text:
            w.text = new
            n += 1
    return n


ICON_PREFIX = re.compile(r"\b([A-Z])([A-ZÀ-Ú][a-zà-ÿ]{3,})\b")


def fix_icon_prefix(text: str, lex) -> str:
    """Letra de ícone grudada no começo da palavra: "NAtenuantes" → "Atenuantes"."""
    def repl(m):
        w = m.group(0)
        return m.group(2) if not lex.valid(w) and lex.valid(m.group(2)) else w

    return ICON_PREFIX.sub(repl, text)
