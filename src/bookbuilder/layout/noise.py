"""Ruído gráfico lido como conteúdo, marcado como artefato (fica fora da
exportação, mas o usuário pode devolvê-lo na revisão):

* texto que é a leitura de ícones, molduras e QR codes: poucas letras
  espalhadas numa largura enorme ("Lone —" em 1.100 px), ou sem palavra
  válida nenhuma ("o 5222 2A9 CC“ C“CTTTITOOOOA", "À o o o o o");
* figuras que são ícones do livro digital (fone, documento, seta de
  ampliar, alfinete, "X"), a interface do leitor no alto da tela e a barra
  "Go to page …" de uma captura feita durante a navegação.
"""

from __future__ import annotations

import re

from ..model import Block, Page, Role
from .fusion import html_to_text

FLAG = "ruido_grafico"
SHORT_WORDS = {"a", "e", "o", "é", "à", "as", "os", "em", "de", "da", "do", "no", "na", "se", "ao", "ou", "um", "que"}
ROMAN = re.compile(r"^[IVXLCDM]+$")


def _valid(w: str, lex) -> bool:
    if w.lower() in SHORT_WORDS or (ROMAN.match(w) and len(w) <= 4):
        return True
    if len(w) < 3:
        return False
    return bool(lex.valid(w) or lex.valid(w.lower()) or lex.valid(w.capitalize()))


def _junk_word(w: str) -> bool:
    """"CTTTITOOOOA", "Losseeeeehe", "AMeneenininesat": letra repetida três
    vezes, maiúscula no meio de minúsculas, ou sem vogal."""
    return bool(re.search(r"(.)\1\1", w) or re.search(r"[a-zà-ÿ][A-ZÀ-Ü]", w)
                or (len(w) >= 4 and not re.search(r"[aeiouáéíóúâêôãõàAEIOUÁÉÍÓÚÂÊÔÃÕÀ]", w)))


def garbage_text(t: str, lex) -> bool:
    """Texto sem relação com o livro (leitura de figura/ícone): quase nenhuma
    letra pertence a palavras válidas de três letras ou mais."""
    t = t.strip()
    if not t or len(t) > 120:
        return False
    if re.search(r"http|www\.|§|[ºª]", t):
        return False
    words = re.findall(r"[A-Za-zÀ-ÿ]+", t)
    if not words:
        return not re.search(r"\d", t)  # só símbolos: "|", "—)", ")"
    junk = any(_junk_word(w) for w in words)
    if re.search(r"\d{3,}", t) and not junk:
        return False  # "arts. 181 a 183-A"
    if re.fullmatch(r"\(?([a-z]|\d{1,3})[).]", t):
        return False  # marcador ou número isolado: pode ser item de lista
    letters = sum(len(w) for w in words)
    good = sum(len(w) for w in words if len(w) >= 3 and _valid(w, lex) and not _junk_word(w))
    return good / letters < 0.3


def sparse_text(b: Block, t: str) -> bool:
    """Poucas letras numa largura desproporcional: borda de quadro, ícone."""
    t = t.strip()
    if not t or len(t) > 40 or not b.lines:
        return False
    w = b.bbox[2] - b.bbox[0]
    line_h = (b.bbox[3] - b.bbox[1]) / max(1, len(b.lines))
    return w / len(t) > 1.5 * max(10.0, line_h)


def icon_figure(p: Page, b: Block) -> bool:
    w, h = b.bbox[2] - b.bbox[0], b.bbox[3] - b.bbox[1]
    if h <= 0:
        return False
    if max(w, h) <= 110 and 0.45 <= w / h <= 2.0:
        return True  # ícone pequeno e quase quadrado
    return b.bbox[3] < p.height * 0.045  # interface do leitor no alto da tela


def _repeats_text(p: Page, fig: Block, txt: str) -> bool:
    """Recorte de figura cujo texto já está num bloco de texto vizinho (a
    palavra "Rixa" do título, a linha do link do QR code)."""
    from rapidfuzz import fuzz

    t = " ".join(txt.split())
    if len(re.sub(r"\W", "", t)) < 4:
        return False
    url_like = re.compile(r"ht+[pt]s?:?/|u[qg]r\.?to|www\.", re.I)  # o OCR erra: "htto://ugrto"
    has_url = bool(url_like.search(t))
    for b in p.blocks:
        if b is fig or b.role in (Role.FIGURE, Role.ARTIFACT) or not b.lines:
            continue
        if abs((b.bbox[1] + b.bbox[3]) / 2 - (fig.bbox[1] + fig.bbox[3]) / 2) > 400:
            continue
        other = " ".join(" ".join(ln.text for ln in b.lines).split())
        if fuzz.partial_ratio(t.casefold(), other.casefold()) >= 85:
            return True
        if has_url and url_like.search(other) and abs(b.bbox[1] - fig.bbox[1]) < 250:
            return True  # imagem da linha do link, que também está como texto
    return False


def ui_bar_text(t: str) -> bool:
    """Janela do leitor capturada junto com a página."""
    return bool(re.search(r"go\s*to\s*page|ir\s+para\s+a\s+p[aá]gina|pick\s+up\s+where|kindle\s+cloud\s+reader"
                          r"|updated\s+\d+\s+\w+\s+ago", t, re.I))


def _surya_rescues(b: Block, t: str, lex) -> bool:
    """Palavras juntadas pelo Tesseract ("existênciadedoisoumais") parecem
    lixo; a releitura do Surya salva o bloco quando lê as mesmas letras com
    sentido. Se leu outra coisa (uma legenda vizinha), não vale."""
    if not b.surya_html:
        return False
    from rapidfuzz import fuzz

    st = html_to_text(b.surya_html)
    la = re.sub(r"[^A-Za-zÀ-ÿ]", "", t).lower()
    lb = re.sub(r"[^A-Za-zÀ-ÿ]", "", st).lower()
    return bool(lb) and not garbage_text(st, lex) and fuzz.ratio(la, lb) >= 60


def mark(pages: list[Page], lex, figure_text=None) -> int:
    """Marca o ruído como artefato. ``figure_text(page, block)`` lê o texto
    de uma figura fina (para achar a barra "Go to page")."""
    n = 0
    for p in pages:
        for b in p.blocks:
            if b.role_locked or b.user_edited or FLAG in b.flags:
                continue
            hit = False
            if b.role == Role.FIGURE:
                txt = b.text or ""
                w, h = b.bbox[2] - b.bbox[0], b.bbox[3] - b.bbox[1]
                read = False
                if figure_text is not None and h <= 130 and "lida_ui4" not in b.flags:
                    try:
                        ocr, _mean = figure_text(p, b)
                    except Exception:  # noqa: BLE001
                        ocr = ""
                    txt = (txt + " " + ocr).strip()
                    read = True
                    b.flags.append("lida_ui4")
                # Faixa larga e fina sem texto legível: pedaço de moldura de quadro.
                frame = read and w >= 700 and h <= 130 and "ilegivel" in b.flags and not re.search(r"[A-Za-zÀ-ÿ]{3,}", txt)
                hit = (icon_figure(p, b) or ui_bar_text(txt) or frame or (h <= 130 and _repeats_text(p, b, txt)))
            elif b.role in (Role.BODY, Role.LIST_ITEM, Role.QUOTE, Role.CAPTION):
                t = " ".join(ln.text for ln in b.lines)
                # Palavras juntadas pelo Tesseract parecem lixo: só conta se a
                # releitura do Surya (quando houver) também não fizer sentido.
                hit = sparse_text(b, t) or ui_bar_text(t) or (garbage_text(t, lex) and not _surya_rescues(b, t, lex))
            elif b.role in (Role.HEADER, Role.FOOTER, Role.PAGE_NUMBER):
                hit = ui_bar_text(" ".join(ln.text for ln in b.lines))
            if hit:
                b.role = Role.ARTIFACT
                b.flags.append(FLAG)
                n += 1
        # Pedaço curto na mesma faixa de outros pedaços de moldura ("AOS" entre
        # "SESSIONS ESTAS" e "—)") também é moldura.
        noisy = [b for b in p.blocks if FLAG in b.flags and b.role == Role.ARTIFACT and b.lines]
        for b in p.blocks:
            if b.role not in (Role.BODY, Role.LIST_ITEM) or b.role_locked or b.user_edited or len(b.lines) != 1:
                continue
            t = b.lines[0].text.strip()
            if len(t) > 12 or len(t.split()) > 2:
                continue
            h = b.bbox[3] - b.bbox[1]
            if any(min(b.bbox[3], x.bbox[3]) - max(b.bbox[1], x.bbox[1]) > h * 0.5 for x in noisy):
                b.role = Role.ARTIFACT
                b.flags.append(FLAG)
                n += 1
    return n


def figure_text(p: Page, b: Block) -> tuple[str, float]:
    """Texto e tom médio de uma figura fina (barra "Go to page" do leitor,
    faixa escura de moldura, recorte que repete um texto vizinho)."""
    import cv2

    from ..ocr import tesseract
    from ..pipeline import work_gray

    gray = work_gray(p)[0]
    x0, y0, x1, y1 = (int(v) for v in b.bbox)
    crop = gray[max(0, y0):y1, max(0, x0):x1]
    if crop.size == 0:
        return "", 255.0
    big = cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    text = " ".join(w.text for ln in tesseract.ocr_hocr(big, lang="por", psm=6) for w in ln.words)
    return text, float(crop.mean())
