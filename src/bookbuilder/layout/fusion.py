"""Fusão das leituras do Tesseract e do Surya para um bloco.

Regras (definidas pelo usuário):

* As duas leituras concordam → texto aceito; o destaque de baixa confiança
  some (dois motores independentes leram o mesmo).
* Diferença de uma única letra → vence o Tesseract, sem destaque.
  Não contam como "letra": §, travessões, aspas e símbolos (ver abaixo).
* Uma palavra longa no Tesseract × várias curtas no Surya ("Sistemada" ×
  "Sistema da") → vence o Surya, sem destaque.
* Romanos, numeração, acentos e itálico/negrito → do Surya. Acento só vale
  se a forma acentuada existir no livro ou no dicionário pt-BR; senão fica o
  Tesseract (evita "títulação", "específicamente"), sem destaque.
* § e travessões → do Surya. Aspas curvas → do Tesseract (o Surya as troca
  por retas). Diferença só de hífen → a forma que o dicionário reconhece.
* Marcadores de lista ("a)", "b)") que o Surya omite → do Tesseract.
* URLs: a bem formada do Surya vence a quebrada do Tesseract.
* Números com estrutura verificável (processo CNJ, lei N/AAAA): vence a
  leitura válida.
* Qualquer outra diferença → vence o Surya, com destaque amarelo, e vira uma
  *divergência* registrada (para o árbitro por recorte e para a revisão).
* Trechos que só o Tesseract leu: lixo/baixa confiança é descartado;
  marcas "[…]" são mantidas; o resto fica, com destaque.
"""

from __future__ import annotations

import difflib
import html as htmlmod
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field

from lxml import html as lhtml

from ..model import Run, merge_runs

ROMAN_RE = re.compile(r"^\(?([IVXLCDM]+|[ivxlcdm]{1,5})[).,;:]*$")
NUMBERING_RE = re.compile(r"^\(?\d+(\.\d+)+\.?[).,;:]*$|^\(?\d+[).]?$")
LIST_MARKER_RE = re.compile(r"^\(?([a-zA-Z]{1,2}(\.\d+)?|\d{1,3}|[ivxlcdmIVXLCDM]{1,5})[).]$|^[•●▪■◦○·–—-]$")
URL_RE = re.compile(r"^\(?(https?://|www\.)[\w.-]+\.[a-z]{2,}(/\S*)?$", re.I)
CNJ_RE = re.compile(r"(\d{7})-(\d{2})\.(\d{4})\.(\d)\.(\d{2})\.(\d{4})")
LAW_RE = re.compile(r"^\(?\d{1,2}\.?\d{3}/(\d{2}|\d{4})[),.;:]*$")
PLACEHOLDERS = {"[…]", "[trecho ilegível]"}
# Leituras trocadas típicas do Tesseract para "I" (romano), "O" e "S"/"§".
CONFUSABLE = str.maketrans({"1": "i", "l": "i", "|": "i", "!": "i", "0": "o", "5": "s"})
SEP_KEY = " ⟷ "


@dataclass
class Seg:
    text: str
    bold: bool = False
    italic: bool = False
    underline: bool = False
    superscript: bool = False
    subscript: bool = False
    low_conf: bool = False
    note_ref: str | None = None


@dataclass
class Tok:
    segs: list[Seg]
    sep: str = " "  # separador depois do token
    ti: tuple = ()  # índices dos tokens do Tesseract que formam este token
    sj: tuple = ()  # índices dos tokens do Surya

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.segs)

    @property
    def flagged(self) -> bool:
        return any(g.low_conf for g in self.segs)


@dataclass
class Stats:
    agree: int = 0
    tess: int = 0
    surya: int = 0
    flagged: int = 0
    dropped: int = 0
    rejected: int = 0  # blocos em que a releitura do Surya foi descartada
    arbiter: int = 0  # divergências resolvidas pelo árbitro por recorte
    user: int = 0  # divergências resolvidas na revisão

    def add(self, other: "Stats") -> None:
        for k in ("agree", "tess", "surya", "flagged", "dropped", "rejected", "arbiter", "user"):
            setattr(self, k, getattr(self, k) + getattr(other, k))


@dataclass
class Dispute:
    """Trecho em que os motores divergiram e nenhuma regra decidiu."""

    tess: str
    surya: str
    ti: tuple  # tokens do Tesseract envolvidos (ou vizinhos, quando só o Surya leu)
    current: str  # texto que ficou no documento (destacado)
    chosen: str = "surya"

    @property
    def key(self) -> str:
        return self.tess + SEP_KEY + self.surya

    def to_dict(self, bbox) -> dict:
        return {"tess": self.tess, "surya": self.surya, "current": self.current, "chosen": self.chosen,
                "bbox": list(bbox) if bbox else None}


@dataclass
class FuseResult:
    runs: list[Run]
    disputes: list[Dispute] = field(default_factory=list)
    tess_tokens: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Tokenização


def runs_to_tokens(runs: list[Run]) -> list[Tok]:
    toks: list[Tok] = []
    cur: list[Seg] = []
    for r in runs:
        for part in re.split(r"(\s+)", r.text):
            if not part:
                continue
            if part.isspace():
                if cur:
                    toks.append(Tok(cur, sep="\t" if "\t" in part else ("\n" if "\n" in part else " ")))
                    cur = []
                elif toks:
                    toks[-1].sep = "\t" if "\t" in part else toks[-1].sep
                continue
            cur.append(Seg(part, r.bold, r.italic, r.underline, r.superscript, r.subscript, r.low_conf, r.note_ref))
    if cur:
        toks.append(Tok(cur, sep=""))
    if toks:
        toks[-1].sep = ""
    return toks


BLOCK_TAGS = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "td", "th", "table", "ul", "ol", "br", "caption"}

LATEX_FIXES = [
    (r"\$?\s*(\d+)\s*\.\s*\^\{\s*\\circ\s*\}\s*\$?", r"\1.º"),  # $3.^{\circ}$ → 3.º
    (r"\^\{\s*\\circ\s*\}", "º"),
    (r"\\\$", "§"),  # \$ lido no lugar de §
    (r"\\S\b", "§"),
    (r"\$\s*§\s*\$", "§"),
]


def normalize_latex(html: str) -> str:
    r"""O Surya às vezes escreve símbolos em LaTeX ("$3.^{\circ}$", "\$").
    Converte os casos conhecidos para os caracteres comuns."""
    out = re.sub(r"</?math[^>]*>", "", html)
    for pat, rep in LATEX_FIXES:
        out = re.sub(pat, rep, out)
    return out


def html_to_text(html: str) -> str:
    return " ".join(t.text for t in html_to_tokens(html))


def html_to_tokens(html: str) -> list[Tok]:
    """Tokens do HTML do Surya, com a formatação de cada trecho."""
    if not html or not html.strip():
        return []
    html = normalize_latex(html)
    try:
        root = lhtml.fragment_fromstring(html, create_parent="div")
    except Exception:  # noqa: BLE001
        text = htmlmod.unescape(re.sub(r"<[^>]+>", " ", html))
        return runs_to_tokens([Run(text)])
    runs: list[Run] = []

    def walk(el, fmt):
        tag = el.tag if isinstance(el.tag, str) else ""
        f = dict(fmt)
        if tag in ("b", "strong"):
            f["bold"] = True
        elif tag in ("i", "em"):
            f["italic"] = True
        elif tag == "u":
            f["underline"] = True
        elif tag == "sup":
            f["superscript"] = True
        elif tag == "sub":
            f["subscript"] = True
        if tag in BLOCK_TAGS:
            runs.append(Run(" "))
        if el.text:
            runs.append(Run(el.text, **f))
        for ch in el:
            walk(ch, f)
            if ch.tail:
                runs.append(Run(ch.tail, **fmt))
        if tag in BLOCK_TAGS:
            runs.append(Run(" "))

    walk(root, {})
    return runs_to_tokens(runs)


# ---------------------------------------------------------------------------
# Comparação


def _strip_accents(s: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", s) if not unicodedata.combining(ch))


def key(s: str) -> str:
    """Chave de alinhamento: sem acentos, minúsculas, aspas/traços unificados;
    "§" equivale a "S"/"$"/"&" (as leituras erradas típicas do sinal)."""
    s = s.replace("§", "s").replace("$", "s").replace("&", "s")
    s = _strip_accents(s).casefold()
    s = re.sub(r"[“”«»\"]", '"', s)
    s = re.sub(r"[‘’']", "'", s)
    s = re.sub(r"[–—‐]", "-", s)
    return s


def letters(s: str) -> str:
    return "".join(ch for ch in s if ch.isalnum())


def shape(s: str) -> str:
    """Chave em que 1/l/|/! = I, 0 = O, 5 = S (confusões de forma)."""
    return letters(key(s).translate(CONFUSABLE))


def confusable_join(t: str, parts: list[str]) -> bool:
    """Uma palavra do Tesseract × várias do Surya, iguais a menos de uma
    confusão de forma, com um romano ou "§" na leitura do Surya: "22,1," ×
    "22, I,"; "|–" × "I –"; "122,11," × "122, II,"."""
    if not (re.search(r"[1l|!5]", t) and any(ROMAN_RE.match(x) or "§" in x for x in parts)):
        return False
    a, b = shape(t), shape("".join(parts))
    # "61,1," × "61, II,": o romano inteiro lido como um só "1".
    return a == b or re.sub(r"i+", "i", a) == re.sub(r"i+", "i", b)


def _ordinal(x: str) -> str:
    x = re.sub(r"(\d)\.?[ºª°]", r"\1#", x)
    return re.sub(r"(\d)\.9(?!\d)", r"\1#", x)


def lev(a: str, b: str, limit: int = 3) -> int:
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


class Lexicon:
    """Palavras válidas: vocabulário do próprio livro e dicionário pt-BR."""

    def __init__(self, vocab: Counter, dictionary=None):
        self.vocab = vocab
        self.dic = dictionary
        self._cache: dict[str, bool] = {}

    def valid(self, word: str) -> bool:
        core = re.sub(r"^[^\wÀ-ÿ]+|[^\wÀ-ÿ]+$", "", word)
        if not core:
            return True
        if core in self._cache:
            return self._cache[core]
        ok = self.vocab.get(core.casefold(), 0) >= 2
        if not ok and self.dic is not None:
            try:
                ok = bool(self.dic.lookup(core)) or bool(self.dic.lookup(core.lower()))
            except Exception:  # noqa: BLE001
                ok = False
        self._cache[core] = ok
        return ok


# --- números verificáveis -----------------------------------------------------


def cnj_valid(text: str) -> bool | None:
    """Número de processo no padrão CNJ: confere o dígito verificador
    (módulo 97, Resolução CNJ 65/2008). None se não tem o formato."""
    m = CNJ_RE.search(text)
    if not m:
        return None
    n, dd, a, j, tr, o = m.groups()
    try:
        return int(n + a + j + tr + o + dd) % 97 == 1
    except ValueError:
        return False


def law_plausible(text: str) -> bool | None:
    """Lei no formato N/AAAA com ano plausível."""
    m = LAW_RE.match(text)
    if not m:
        return None
    y = m.group(1)
    year = int(y) if len(y) == 4 else (1900 + int(y) if int(y) > 30 else 2000 + int(y))
    return 1800 <= year <= 2035


def number_choice(a: str, b: str) -> str | None:
    """'tess' / 'surya' quando só uma das leituras é um número válido."""
    for check in (cnj_valid, law_plausible):
        va, vb = check(a), check(b)
        if va is None and vb is None:
            continue
        if va and not vb:
            return "tess"
        if vb and not va:
            return "surya"
    return None


# --- construção dos tokens de saída ----------------------------------------------


def _fmt_from_surya(t: Tok, s: Tok) -> Tok:
    """Texto do token do Tesseract com itálico/negrito/sublinhado do Surya
    (sobrescrito: de qualquer um dos dois)."""
    s_it = any(x.italic for x in s.segs)
    s_b = any(x.bold for x in s.segs)
    s_u = any(x.underline for x in s.segs)
    out = [Seg(g.text, bold=s_b, italic=s_it, underline=s_u, superscript=g.superscript,
               subscript=g.subscript, low_conf=False, note_ref=g.note_ref) for g in t.segs]
    return Tok(out, t.sep, t.ti, s.sj)


def _tess_tok(t: Tok, flag: bool = False) -> Tok:
    return Tok([Seg(g.text, g.bold, g.italic, g.underline, g.superscript, g.subscript, flag, g.note_ref)
                for g in t.segs], t.sep, t.ti, ())


def _surya_tok(s: Tok, sep: str, flag: bool, ti: tuple = ()) -> Tok:
    return Tok([Seg(g.text, g.bold, g.italic, g.underline, g.superscript, g.subscript, flag) for g in s.segs],
               sep, ti, s.sj)


def _with_text(t: Tok, s: Tok, text: str, flag: bool = False) -> Tok:
    """Token com o texto escolhido e a formatação do Surya."""
    base = _fmt_from_surya(t, s)
    g0 = base.segs[0]
    sup_tail = [g for g in base.segs if g.superscript and g is not g0]
    segs = [Seg(text if not sup_tail else text[: len(text) - sum(len(g.text) for g in sup_tail)],
                g0.bold, g0.italic, g0.underline, False, g0.subscript, flag)] + sup_tail
    return Tok([g for g in segs if g.text], t.sep, t.ti, s.sj)


def _symbol_diff(a: str, b: str) -> bool:
    """As palavras só diferem em §, $, travessões ou aspas (o hífen comum é
    tratado à parte, pelo dicionário)?"""
    def strip(x: str) -> str:
        return re.sub(r"[§$–—“”\"‘’'«»]", "", x)

    return a != b and strip(a) == strip(b)


def _looks_like_url(a: str) -> bool:
    al = a.lower()
    return "http" in al or "www" in al or al.count("/") >= 2 or ".to/" in al


def decide_pair(t: Tok, s: Tok, lex: Lexicon, st: Stats) -> Tok:
    a, b = t.text, s.text
    if a == b:
        st.agree += 1
        return _fmt_from_surya(t, s)
    if a in PLACEHOLDERS:
        st.tess += 1
        return t
    nc_tok = _note_call(t, s)
    if nc_tok is not None:
        st.surya += 1
        return nc_tok
    if re.sub(r"(?<=\w)[–—](?=\w)", "-", b) == a:
        st.tess += 1  # hífen entre letras/números ("121-A") não vira traço
        return _fmt_from_surya(t, s)
    da = [ch for ch in a if ch in "–—"]
    if da and any(ch in "–—" for ch in b):
        # O Surya acerta que é traço, mas não o tamanho: fica o medido na imagem.
        b = re.sub(r"[–—]", da[0], b)
        if a == b:
            st.agree += 1
            return _fmt_from_surya(t, s)
    if "\\" in b or "^{" in b:
        # Restos de LaTeX na leitura do Surya: fica o Tesseract.
        st.tess += 1
        return _fmt_from_surya(t, s)
    ka, kb = key(a), key(b)
    if ka == kb:
        # Só acento, maiúscula, aspas ou tipo de traço.
        if re.sub(r"[“”«»\"‘’']", "", a) == re.sub(r"[“”«»\"‘’']", "", b):
            st.tess += 1  # aspas curvas do Tesseract
            return _fmt_from_surya(t, s)
        if any(ch in "–—" for ch in b) and re.sub(r"[-–—‐]", "", a) == re.sub(r"[-–—‐]", "", b):
            st.surya += 1  # travessões do Surya
            return _with_text(t, s, b)
        if lex.valid(b) or not lex.valid(a):
            st.surya += 1  # acento/maiúscula do Surya, se a forma existe
            return _with_text(t, s, _keep_curly(a, b))
        st.tess += 1
        return _fmt_from_surya(t, s)
    # URL bem formada do Surya × URL quebrada do Tesseract
    if URL_RE.match(b) and not URL_RE.match(a) and _looks_like_url(a):
        st.surya += 1
        return _with_text(t, s, b)
    # Números verificáveis (processo CNJ, lei N/AAAA)
    nc = number_choice(a, b)
    if nc == "tess":
        st.tess += 1
        return _fmt_from_surya(t, s)
    if nc == "surya":
        st.surya += 1
        return _with_text(t, s, b)
    # § e símbolos
    if "§" in b and re.search(r"(^|[^\w])(S\$|\$S|SS|88|S|\$|5|8|&)", a) or _symbol_diff(a, b):
        st.surya += 1
        return _with_text(t, s, b)
    # Diferença só de hífen
    if ka.replace("-", "") == kb.replace("-", ""):
        va, vb = lex.valid(a), lex.valid(b)
        if vb and not va:
            st.surya += 1
            return _with_text(t, s, b)
        st.tess += 1
        return _fmt_from_surya(t, s)
    # Ordinais: "22º" × "22ª", "1.9" × "1.º"
    if any(ch in b for ch in "ºª") and _ordinal(a) == _ordinal(b):
        st.surya += 1
        return _with_text(t, s, b)
    # Marcador de lista lido errado pelo Tesseract: "£)" × "f)"
    if LIST_MARKER_RE.match(b) and not LIST_MARKER_RE.match(a) and len(a) <= 3 and a.endswith(")"):
        st.surya += 1
        return _with_text(t, s, b)
    # Romanos e numeração
    if ROMAN_RE.match(b) or NUMBERING_RE.match(b):
        st.surya += 1
        return _with_text(t, s, b)
    # Algarismo no lugar de letra ("2 única" × "a única"): a palavra do Surya.
    if (any(ch.isdigit() for ch in a) and not any(ch.isdigit() for ch in b) and b.isalpha()
            and lex.valid(b) and len(a) == len(b)):
        st.surya += 1
        return _with_text(t, s, b)
    # Uma letra de diferença (fora acentos/símbolos): Tesseract
    if lev(letters(ka), letters(kb), 1) <= 1 and letters(ka) and letters(kb):
        st.tess += 1
        return _fmt_from_surya(t, s)
    # Outra diferença: Surya com destaque (divergência)
    st.surya += 1
    st.flagged += 1
    return _surya_tok(s, t.sep, True, t.ti)


# Chamada de nota sobrescrita que o Tesseract lê como "?º", "º", "!º".
NOTE_JUNK = re.compile(r"[?!º°ª*'’]{1,3}")
NOTE_CALL = re.compile(r"^(.*[\w)\]”\"»’])([.!?…]*[.!?…”»)])(\d{1,3})$")


def _note_call(t: Tok, s: Tok) -> Tok | None:
    """"(TEPT).?º" × "(TEPT).2", "loteria.?" × "loteria.4": o Surya leu o
    número da chamada de nota que o Tesseract trocou por sinais (ou não viu,
    quando o Surya o marca como sobrescrito). Fica o texto do Surya, com o
    número sobrescrito."""
    a, b = t.text, s.text
    m = NOTE_CALL.match(b)
    if not m:
        return None
    word, punct, num = m.groups()
    tail = a[len(a.rstrip("?!º°ª*'’.,")):] if a else ""
    head = a[: len(a) - len(tail)] if tail else a
    if letters(key(head)) != letters(key(word)):
        return None
    sup = bool(s.segs) and s.segs[-1].superscript and s.segs[-1].text == num
    if not (tail and NOTE_JUNK.search(tail)) and not sup:
        return None  # sem sinal do Tesseract, só com o número sobrescrito pelo Surya
    g0 = s.segs[0]
    segs = [Seg(word + punct, g0.bold, g0.italic, g0.underline), Seg(num, superscript=True)]
    return Tok(segs, t.sep, t.ti, s.sj)


def _join_hyphenated(S: list[Tok], lex: Lexicon) -> list[Tok]:
    """O Surya às vezes mantém a divisão silábica do fim da linha
    ("racio-" "nais"): junta as partes ("racionais"); mantém o hífen quando
    só a forma com hífen existe ("bem-" "estar")."""
    out: list[Tok] = []
    k = 0
    while k < len(S):
        s = S[k]
        nxt = S[k + 1] if k + 1 < len(S) else None
        txt = s.text
        if (nxt is not None and len(txt) > 2 and txt.endswith("-") and txt[-2].isalpha()
                and nxt.text[:1].islower()):
            plain = txt[:-1] + nxt.text
            keep = not lex.valid(plain) and lex.valid(txt + nxt.text)
            segs = [Seg(g.text, g.bold, g.italic, g.underline, g.superscript, g.subscript, g.low_conf, g.note_ref)
                    for g in s.segs]
            if not keep:
                segs[-1].text = segs[-1].text[:-1]
            out.append(Tok([g for g in segs if g.text] + list(nxt.segs), nxt.sep, s.ti, s.sj))
            k += 2
            continue
        out.append(s)
        k += 1
    return out


def _drop_stray_periods(out: list[Tok], S: list[Tok]) -> None:
    """Ponto que só o Tesseract viu, colado à palavra e seguido de minúscula
    ("como. animais"): sujeira da imagem."""
    for k, tok in enumerate(out[:-1]):
        txt = tok.text
        if (txt.endswith(".") and not txt.endswith("..") and len(txt) > 2 and txt[-2].isalpha()
                and out[k + 1].text[:1].islower() and tok.sj and S[tok.sj[0]].text == txt[:-1]):
            tok.segs[-1].text = tok.segs[-1].text[:-1]


def _keep_curly(a: str, b: str) -> str:
    """Texto do Surya, mas com as aspas curvas que o Tesseract leu."""
    if any(ch in a for ch in "“”‘’") and not any(ch in b for ch in "“”‘’"):
        out = list(b)
        qa = [ch for ch in a if ch in "“”‘’\"'"]
        k = 0
        for i, ch in enumerate(out):
            if ch in "\"'" and k < len(qa):
                out[i] = qa[k]
                k += 1
        return "".join(out)
    return b


# ---------------------------------------------------------------------------
# Fusão


def fuse(tess_runs: list[Run], surya_html: str, lex: Lexicon, st: Stats | None = None) -> list[Run]:
    return fuse_block(tess_runs, surya_html, lex, st).runs


def fuse_block(tess_runs: list[Run], surya_html: str, lex: Lexicon, st: Stats | None = None,
               choices: dict[str, str] | None = None) -> FuseResult:
    """Funde as leituras do bloco. ``choices`` (chave da divergência →
    "tess"/"surya") aplica decisões já tomadas pelo árbitro ou pelo usuário."""
    st = st if st is not None else Stats()
    T = runs_to_tokens(tess_runs)
    for i, t in enumerate(T):
        t.ti = (i,)
    S = _join_hyphenated(html_to_tokens(surya_html), lex)
    for j, s in enumerate(S):
        s.sj = (j,)
    tess_texts = [t.text for t in T]
    if not S:
        return FuseResult(tess_runs, [], tess_texts)  # sem leitura do Surya: fica o Tesseract
    if T and (_block_similarity(T, S) < 0.6 or _invented_numbers(T, S)):
        st.rejected += 1
        return FuseResult(tess_runs, [], tess_texts)  # releitura sem relação com o bloco (alucinação)
    if not T:
        st.surya += len(S)
        return FuseResult(_tokens_to_runs([_surya_tok(s, " ", False) for s in S]), [], tess_texts)
    sm = difflib.SequenceMatcher(None, [key(t.text) for t in T], [key(s.text) for s in S], autojunk=False)
    out: list[Tok] = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        ts, ss = T[i1:i2], S[j1:j2]
        if op == "equal":
            for t, s in zip(ts, ss):
                out.append(decide_pair(t, s, lex, st))
        elif op == "replace":
            jt = "".join(letters(key(t.text)) for t in ts)
            js = "".join(letters(key(s.text)) for s in ss)
            if len(ss) > len(ts) and len(ts) == 1 and jt == js:
                out += _split_joined(ts[0], ss, st)  # palavra junta no Tesseract, separada no Surya
            elif len(ss) > len(ts) and jt == js:
                st.surya += len(ss)
                sep = ts[-1].sep
                ti = tuple(i for t in ts for i in t.ti)
                for k, s in enumerate(ss):
                    out.append(_surya_tok(s, " " if k < len(ss) - 1 else sep, False, ti))
            elif len(ts) == len(ss) and all(_sim(t.text, s.text) >= 0.5 for t, s in zip(ts, ss)):
                for t, s in zip(ts, ss):
                    out.append(decide_pair(t, s, lex, st))
            elif jt == js:
                st.tess += len(ts)  # mesmo texto, divisão diferente (Surya juntou): Tesseract
                out += [_fmt_from_surya(t, ss[0]) for t in ts]
            else:
                out += _align_segment(ts, ss, lex, st, T, S)
        elif op == "delete":
            out += _deleted(ts, st, lex, T)
        elif op == "insert":
            prev_sep = out[-1].sep if out else " "
            if out and out[-1].sep == "":
                out[-1].sep = " "
            keep = [s for s in ss if not _repeats_neighbor(s, out[-1] if out else None, T[i1] if i1 < len(T) else None)]
            st.dropped += len(ss) - len(keep)
            ins = _inserted(keep, st, lex, S)
            if ins:
                _move_punct(out, ins, S, j1)
                ins[-1].sep = prev_sep if i1 >= len(T) else " "
            out += ins
    if out:
        out[-1].sep = ""
    _drop_stray_periods(out, S)
    _fix_sections(out)
    found = _collect_disputes(out, T, S)
    if choices:
        out = _apply_choices(out, found, T, S, choices, st)
        found = _collect_disputes(out, T, S)
    return FuseResult(_tokens_to_runs(out), [d for d, _, _ in found], tess_texts)


def _collect_disputes(out: list[Tok], T: list[Tok], S: list[Tok]) -> list[tuple[Dispute, int, int]]:
    """Agrupa tokens destacados vizinhos em divergências: (divergência,
    início, fim) na lista de saída."""
    res = []
    k = 0
    while k < len(out):
        if not out[k].flagged or out[k].text in PLACEHOLDERS:
            k += 1
            continue
        a = k
        while k < len(out) and out[k].flagged and out[k].text not in PLACEHOLDERS:
            k += 1
        span = out[a:k]
        ti = sorted({i for t in span for i in t.ti})
        sj = sorted({j for t in span for j in t.sj})
        tess = " ".join(T[i].text for i in range(ti[0], ti[-1] + 1)) if ti else ""
        surya = " ".join(S[j].text for j in range(sj[0], sj[-1] + 1)) if sj else ""
        # Âncora na imagem: tokens do Tesseract envolvidos ou, se só o Surya leu, os vizinhos.
        anchor = tuple(ti)
        if not anchor:
            nb = [i for t in (out[a - 1:a] + out[k:k + 1]) for i in t.ti]
            anchor = tuple(nb)
        current = " ".join(t.text for t in span)
        res.append((Dispute(tess, surya, anchor, current, "tess" if not sj else "surya"), a, k))
    return res


def _apply_choices(out, disputes, T, S, choices, st):
    """Substitui cada divergência decidida pelo texto escolhido, sem destaque."""
    for d, a, b in reversed(disputes):
        ch = choices.get(d.key)
        if ch not in ("tess", "surya"):
            continue
        sep = out[b - 1].sep
        if ch == "surya":
            sj = sorted({j for t in out[a:b] for j in t.sj})
            repl = [_surya_tok(S[j], " ", False) for j in range(sj[0], sj[-1] + 1)] if sj else []
        else:
            ti = sorted({i for t in out[a:b] for i in t.ti})
            repl = [_tess_tok(T[i]) for i in range(ti[0], ti[-1] + 1)] if ti else []
            for r in repl:
                r.sep = " "
        if repl:
            repl[-1].sep = sep
        elif a > 0:
            out[a - 1].sep = sep if sep else out[a - 1].sep
        out[a:b] = repl
        if choices.get("_fonte:" + d.key) == "usuario":
            st.user += 1
        else:
            st.arbiter += 1
        st.flagged -= 1
    return out


def _sim(a: str, b: str) -> float:
    from rapidfuzz import fuzz

    ka, kb = letters(key(a)), letters(key(b))
    if not ka and not kb:
        return 1.0 if key(a) == key(b) else 0.5
    return fuzz.ratio(ka, kb) / 100.0


def _align_segment(ts: list[Tok], ss: list[Tok], lex: Lexicon, st: Stats, T: list[Tok] | None = None,
                   S: list[Tok] | None = None) -> list[Tok]:
    """Pareia palavra a palavra um trecho em que os dois motores leram
    quantidades diferentes de palavras. Junções ("doftipo" × "do tipo") são
    reconhecidas; pares parecidos passam pelas regras; o que sobra vira
    remoção (só no Tesseract) ou inserção (só no Surya)."""
    n, m = len(ts), len(ss)
    GAP = 0.55
    INF = 1e9
    cost = [[INF] * (m + 1) for _ in range(n + 1)]
    back: list[list] = [[None] * (m + 1) for _ in range(n + 1)]
    cost[0][0] = 0.0
    for i in range(n + 1):
        for j in range(m + 1):
            c = cost[i][j]
            if c >= INF:
                continue
            if i < n and c + GAP < cost[i + 1][j]:
                cost[i + 1][j], back[i + 1][j] = c + GAP, ("del", i, j)
            if j < m and c + GAP < cost[i][j + 1]:
                cost[i][j + 1], back[i][j + 1] = c + GAP, ("ins", i, j)
            if i < n and j < m:
                cc = c + (1.0 - _sim(ts[i].text, ss[j].text))
                if cc < cost[i + 1][j + 1]:
                    cost[i + 1][j + 1], back[i + 1][j + 1] = cc, ("pair", i, j)
                for k in (2, 3):
                    if j + k <= m:
                        joined = "".join(letters(key(x.text)) for x in ss[j:j + k])
                        tl = letters(key(ts[i].text))
                        if (joined and (joined == tl or (len(tl) >= 5 and lev(joined, tl, 1) <= 1
                                                         and all(lex.valid(x.text) and len(letters(x.text)) > 1
                                                                 or x.text.lower() in ("a", "e", "o", "é", "à")
                                                                 for x in ss[j:j + k])))
                                or confusable_join(ts[i].text, [x.text for x in ss[j:j + k]])):
                            cc = c + 0.05
                            if cc < cost[i + 1][j + k]:
                                cost[i + 1][j + k], back[i + 1][j + k] = cc, ("join", i, j, k)
    ops = []
    i, j = n, m
    while (i, j) != (0, 0):
        op = back[i][j]
        ops.append(op)
        if op[0] == "del":
            i -= 1
        elif op[0] == "ins":
            j -= 1
        elif op[0] == "pair":
            i, j = i - 1, j - 1
        else:
            i, j = i - 1, j - op[3]
    ops.reverse()
    out: list[Tok] = []
    for op in ops:
        if op[0] == "pair":
            t, s = ts[op[1]], ss[op[2]]
            # Mesma quantidade de palavras dos dois lados: troca simples (Surya com destaque).
            if (n != m and _sim(t.text, s.text) < 0.4 and not (ROMAN_RE.match(s.text) or NUMBERING_RE.match(s.text)
                                                    or "§" in s.text or URL_RE.match(s.text)
                                                    or LIST_MARKER_RE.match(s.text))):
                out += _deleted([t], st, lex, T)
                out += _inserted([s], st, lex, S)
            else:
                out.append(decide_pair(t, s, lex, st))
        elif op[0] == "join":
            t = ts[op[1]]
            parts = ss[op[2]:op[2] + op[3]]
            out += _split_joined(t, parts, st)
        elif op[0] == "del":
            out += _deleted([ts[op[1]]], st, lex, T)
        else:
            nxt = ts[op[1]] if op[1] < len(ts) else None
            if _repeats_neighbor(ss[op[2]], out[-1] if out else None, nxt):
                st.dropped += 1
            else:
                ins = _inserted([ss[op[2]]], st, lex, S)
                if ins and S is not None and ss[op[2]].sj:
                    _move_punct(out, ins, S, ss[op[2]].sj[0])
                out += ins
    return out


def _split_joined(t: Tok, parts: list[Tok], st: Stats) -> list[Tok]:
    """Divide a palavra junta do Tesseract nos pontos em que o Surya separou.
    Se as letras coincidem, usa os caracteres do Tesseract (preserva acentos
    e aspas); senão, usa as palavras do Surya."""
    st.surya += len(parts)
    src = t.text
    keys = [letters(key(x.text)) for x in parts]
    if "".join(keys) == letters(key(src)):
        out: list[Tok] = []
        pos = 0
        n = len(src)
        for k2, (x, kx) in enumerate(zip(parts, keys)):
            start = pos
            need = len(kx)
            while pos < n and need > 0:
                if src[pos].isalnum():
                    need -= 1
                pos += 1
            while pos < n and not src[pos].isalnum():
                pos += 1  # pontuação logo depois fica com este pedaço
            if k2 == len(parts) - 1:
                pos = n
            piece = src[start:pos]
            base = _fmt_from_surya(Tok([Seg(piece)], " ", t.ti), x)
            base.sep = " " if k2 < len(parts) - 1 else t.sep
            out.append(base)
        return out
    return [_surya_tok(x, " " if k2 < len(parts) - 1 else t.sep, False, t.ti) for k2, x in enumerate(parts)]


def _next_text(seq: list[Tok] | None, idx: tuple) -> str:
    if not seq or not idx or idx[-1] + 1 >= len(seq):
        return ""
    return seq[idx[-1] + 1].text


def icon_noise(text: str, first: bool, nxt: str, lex: Lexicon | None) -> bool:
    """Ruído de ícone lido como texto por um só dos motores: "L" antes de
    "PONTO RELEVANTE", "CN", ou uma letra solta ("a", "o") antes da
    palavra maiúscula que abre o bloco."""
    core = text.strip(".,;:")
    if not core:
        return False
    starts_cap = nxt[:1].isupper()
    if re.fullmatch(r"[A-ZÀ-Ü]{1,3}", core):
        if first and starts_cap:
            return True
        if ROMAN_RE.match(core) or len(core) == 1:
            return False
        return lex is not None and not lex.valid(core)
    return len(core) == 1 and core.isalpha() and first and starts_cap


def _deleted(ts: list[Tok], st: Stats, lex: Lexicon | None = None, T: list[Tok] | None = None) -> list[Tok]:
    out = []
    for t in ts:
        txt = t.text
        first = t.ti[:1] == (0,)
        if txt in PLACEHOLDERS:
            out.append(t)
        elif LIST_MARKER_RE.match(txt) or (first and NUMBERING_RE.match(txt)):
            st.tess += 1  # marcador de lista ou numeração de seção que o Surya omite
            out.append(_tess_tok(t))
        elif (not any(ch.isalnum() for ch in txt) and txt not in ("–", "—", "§")) or NOTE_JUNK.fullmatch(txt):
            st.dropped += 1  # lixo do OCR que o Surya não viu
        elif any(g.low_conf for g in t.segs):
            st.dropped += 1
        elif icon_noise(txt, first, _next_text(T, t.ti), lex):
            st.dropped += 1
        else:
            st.tess += 1
            st.flagged += 1
            out.append(_tess_tok(t, True))
    return out


def _repeats_neighbor(s: Tok, prev: Tok | None, nxt: Tok | None) -> bool:
    """Palavra que só o Surya leu repetindo a vizinha ("diferenciar ciar")."""
    sl = letters(key(s.text))
    if len(sl) < 3:
        return False
    pl = letters(key(prev.text)) if prev else ""
    nl = letters(key(nxt.text)) if nxt else ""
    return sl == pl or sl == nl or (len(pl) > len(sl) and pl.endswith(sl))


def _move_punct(out: list[Tok], ins: list[Tok], S: list[Tok], j1: int) -> None:
    """"último." (Tesseract) + "dia." (só o Surya) → "último dia.": a pontuação
    final vai para a palavra inserida, como o Surya leu."""
    if not out or j1 == 0:
        return
    prev, s_prev = out[-1], S[j1 - 1].text
    pt = prev.text
    if pt and pt[-1] in ".,;:" and not s_prev.endswith(pt[-1]) and ins[-1].text.endswith(pt[-1]):
        g = prev.segs[-1]
        g.text = g.text[:-1]


def _inserted(ss: list[Tok], st: Stats, lex: Lexicon | None = None, S: list[Tok] | None = None) -> list[Tok]:
    out = []
    for s in ss:
        if icon_noise(s.text, s.sj[:1] == (0,), _next_text(S, s.sj), lex):
            st.dropped += 1
            continue
        flag = any(ch.isalnum() for ch in s.text)
        st.surya += 1
        st.flagged += int(flag)
        out.append(_surya_tok(s, " ", flag))
    return out


SECTION_TOKEN = re.compile(r"^([(\[]?)(S\$|\$S|SS|\$\$|S|\$|&)$")


def _fix_sections(toks: list[Tok]) -> None:
    """§ lido como S/$ (inclusive com parêntese colado: "($ 4.º")."""
    for a, b in zip(toks, toks[1:]):
        m = SECTION_TOKEN.match(a.text)
        if m and b.text[:1].isdigit():
            a.segs = [Seg(m.group(1) + ("§§" if m.group(2) in ("SS", "$$") else "§"), a.segs[0].bold, a.segs[0].italic)]


NUMSYM = re.compile(r"^[^A-Za-zÀ-ÿ]+$")


def _invented_numbers(T: list[Tok], S: list[Tok]) -> bool:
    """Muitos tokens só de números/símbolos que o Tesseract não leu: a
    releitura inventou conteúdo (ex.: "– 0.5 ± 2.5°")."""
    tn = Counter(t.text for t in T if NUMSYM.match(t.text))
    extra = sum(1 for s in S if NUMSYM.match(s.text) and tn.get(s.text, 0) == 0 and s.text not in ("§", "–", "—"))
    return extra > max(3, len(S) * 0.08)


def _block_similarity(T: list[Tok], S: list[Tok]) -> float:
    from rapidfuzz import fuzz

    a = "".join(letters(key(t.text)) for t in T)
    b = "".join(letters(key(s.text)) for s in S)
    if not a or not b:
        return 0.0
    return fuzz.ratio(a, b) / 100.0


def _tokens_to_runs(toks: list[Tok]) -> list[Run]:
    runs: list[Run] = []
    for t in toks:
        for g in t.segs:
            runs.append(Run(g.text, g.bold, g.italic, g.underline, g.superscript, g.subscript, g.low_conf, g.note_ref))
        if t.sep:
            prev = runs[-1] if runs else None
            same = prev is not None and not prev.superscript
            runs.append(Run(t.sep, bold=prev.bold if same else False, italic=prev.italic if same else False))
    return merge_runs(runs)


# ---------------------------------------------------------------------------
# Posição dos tokens do Tesseract na imagem


def token_boxes(tess_texts: list[str], words) -> list[tuple | None]:
    """Caixa (na imagem) de cada token do Tesseract, casando os tokens com as
    palavras do OCR pela sequência de letras (a hifenização une duas
    palavras num token)."""
    boxes: list[tuple | None] = []
    wi = 0
    for tt in tess_texts:
        need = letters(key(tt))
        got = ""
        bx = None
        start = wi
        while wi < len(words) and len(got) < len(need):
            w = words[wi]
            got += letters(key(w.text))
            b = w.bbox
            bx = b if bx is None else (min(bx[0], b[0]), min(bx[1], b[1]), max(bx[2], b[2]), max(bx[3], b[3]))
            wi += 1
        if got != need and need:
            # descompasso (ex.: palavra descartada): recomeça sem avançar demais
            wi = start + 1 if start < len(words) else start
        boxes.append(bx)
    return boxes


_dictionary = None


def load_dictionary():
    """Dicionário VERO pt-BR (carregado uma vez; ~3 s)."""
    global _dictionary
    if _dictionary is None:
        from .. import paths

        base = paths.dictionary_base()
        if base.with_suffix(".dic").is_file():
            from spylls.hunspell import Dictionary

            _dictionary = Dictionary.from_files(str(base))
        else:
            _dictionary = False
    return _dictionary or None
