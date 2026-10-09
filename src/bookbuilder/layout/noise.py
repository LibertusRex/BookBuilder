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


def _confident_words(b: Block) -> bool:
    """Nomes próprios e palavras estrangeiras ("Tal Ben-Shahar",
    "principium") não estão no dicionário, mas lidos com alta confiança e
    bem-formados não são lixo de figura."""
    ws = [w for ln in b.lines for w in ln.words]
    return bool(ws) and all(w.conf >= 85 and re.fullmatch(r"[A-Za-zÀ-ÿ][a-zà-ÿ]+(?:-[A-Za-zÀ-ÿ][a-zà-ÿ]+)*[.,]?", w.text)
                            and re.search(r"[aeiouáéíóúâêôãõ]", w.text.lower()) for w in ws)


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


def photo_edge(p: Page, b: Block) -> bool:
    """Páginas fotografadas/digitalizadas: dedos segurando o livro, a borda
    da página vizinha, a mesa. Encostam na borda do recorte e não têm texto
    legível; figuras e texto do livro ficam dentro das margens."""
    if not p.light or not p.width or not p.height:
        return False
    x0, y0, x1, y1 = b.bbox
    mx, my = p.width * 0.012, p.height * 0.012
    touches = x0 <= mx or y0 <= my or x1 >= p.width - mx or y1 >= p.height - my
    if not touches:
        return False
    if b.role == Role.FIGURE:
        area = (x1 - x0) * (y1 - y0) / (p.width * p.height)
        return area < 0.08 or (area < 0.25 and "ilegivel" in b.flags)
    if b.role in (Role.BODY, Role.LIST_ITEM, Role.QUOTE, Role.CAPTION):
        confs = [w.conf for ln in b.lines for w in ln.words]
        return len(b.lines) <= 2 and bool(confs) and sum(confs) / len(confs) < 60
    return False


INITIAL = "letra_inicial"


def attach_initials(p: Page) -> None:
    """Capitular: a primeira letra do parágrafo, maior que o texto, sai como
    um bloco de uma letra só ("M") colado ao início da primeira linha do
    parágrafo ("as o que…"). A letra volta para a palavra e o bloco vira
    artefato (sem texto próprio)."""
    for b in p.blocks:
        if b.role_locked or b.user_edited or len(b.lines) != 1 or len(b.lines[0].words) != 1:
            continue
        if b.role not in (Role.BODY, Role.ARTIFACT) or (b.role == Role.ARTIFACT and FLAG not in b.flags):
            continue
        letter = b.lines[0].words[0].text
        if len(letter) != 1 or not letter.isupper():
            continue
        x0, y0, x1, y1 = b.bbox
        h = y1 - y0
        for o in p.blocks:
            if o is b or o.role not in (Role.BODY, Role.LIST_ITEM, Role.QUOTE) or not o.lines or not o.lines[0].words:
                continue
            w = o.lines[0].words[0]
            if not w.text[:1].islower():
                continue
            gap = w.bbox[0] - x1
            overlap = min(y1, w.bbox[3]) - max(y0, w.bbox[1])
            if -h * 0.2 <= gap <= h * 0.6 and overlap >= (w.bbox[3] - w.bbox[1]) * 0.5:
                w.text = letter + w.text
                w.superscript = False  # a palavra fica no alto, ao lado da capitular
                w.bbox = (min(x0, w.bbox[0]), min(y0, w.bbox[1]), w.bbox[2], max(y1, w.bbox[3]))
                o.bbox = (min(x0, o.bbox[0]), min(y0, o.bbox[1]), o.bbox[2], o.bbox[3])
                if o.surya_html:
                    o.surya_html = re.sub(r"(<p>\s*)" + re.escape(w.text[1:]), lambda m: m.group(1) + w.text,
                                          o.surya_html, count=1)
                b.lines = []
                b.role = Role.ARTIFACT
                b.flags = [f for f in b.flags if f != FLAG] + [INITIAL]
                break


def mark(pages: list[Page], lex, figure_text=None) -> int:
    """Marca o ruído como artefato. ``figure_text(page, block)`` lê o texto
    de uma figura fina (para achar a barra "Go to page")."""
    n = 0
    for p in pages:
        attach_initials(p)
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
                hit = sparse_text(b, t) or ui_bar_text(t) or (garbage_text(t, lex) and not _surya_rescues(b, t, lex)
                                                              and not _confident_words(b))
            elif b.role in (Role.HEADER, Role.FOOTER, Role.PAGE_NUMBER):
                hit = ui_bar_text(" ".join(ln.text for ln in b.lines))
            hit = hit or photo_edge(p, b)
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


def figures_from_garbage(pages: list[Page], save_crop) -> int:
    """Páginas fotografadas: uma ilustração com rótulos (escala "−5 … 5",
    "negativo / neutro / positivo") pode ser tomada por texto pelo detector
    de layout e sair como lixo. Um bloco largo descartado como lixo vira
    figura, junto com os rótulos descartados em volta.

    ``save_crop(page, bbox) -> nome do arquivo`` grava o recorte."""
    n = 0
    for p in pages:
        if not p.light or not p.width:
            continue
        ids = {b.id for b in p.blocks}
        junk = [b for b in p.blocks if b.role == Role.ARTIFACT and FLAG in b.flags and b.lines
                and not photo_edge(p, b) and not b.role_locked]
        for b in junk:
            if f"{b.id}-fig" in ids or "na_figura" in b.flags or b.bbox[2] - b.bbox[0] < p.width * 0.35:
                continue
            x0, y0, x1, y1 = b.bbox
            lh = max(20, (y1 - y0) / max(1, len(b.lines)))
            group = [b]
            for o in junk:
                if o is b or "na_figura" in o.flags:
                    continue
                if o.bbox[1] <= y1 + 1.5 * lh and o.bbox[3] >= y0 - 1.5 * lh and o.bbox[0] < x1 and o.bbox[2] > x0:
                    group.append(o)
                    x0, y0 = min(x0, o.bbox[0]), min(y0, o.bbox[1])
                    x1, y1 = max(x1, o.bbox[2]), max(y1, o.bbox[3])
            pad = int(lh * 0.5)
            box = (max(0, x0 - pad), max(0, y0 - pad), min(p.width, x1 + pad), min(p.height, y1 + pad))
            name = save_crop(p, box)
            if not name:
                continue
            fig = Block(id=f"{b.id}-fig", role=Role.FIGURE, bbox=box, kind="figura_recuperada", order=b.order,
                        image_file=name, flags=["figura_de_ruido"])
            p.blocks.append(fig)
            ids.add(fig.id)
            for g in group:
                g.flags.append("na_figura")
            n += 1
        # A moldura da ilustração tinha sido tomada por um quadro do livro.
        frames = {g.box_id for g in p.blocks if "na_figura" in g.flags and g.box_id}
        for o in p.blocks:
            if o.box_id in frames and "na_figura" not in o.flags and not o.role_locked:
                o.box_id = None
    return n


COVER = "capa"


def cover_page(pages: list[Page], save_image=None) -> Block | None:
    """Capa fotografada (primeira página de um PDF digitalizado ou foto, com
    figura grande ou letras bem maiores que as do livro): fica inteira como
    imagem, com as cores originais, e o texto lido nela sai do documento.
    ``save_image(page)`` grava a imagem e devolve o nome do arquivo."""
    import statistics

    if not pages or not pages[0].light:
        return None
    p = pages[0]
    cover = next((b for b in p.blocks if COVER in b.flags), None)
    if cover is None:
        if any(b.user_edited or b.role_locked for b in p.blocks):
            return None
        area = float(p.width * p.height) or 1.0
        big_fig = any(b.role == Role.FIGURE and (b.bbox[2] - b.bbox[0]) * (b.bbox[3] - b.bbox[1]) >= area * 0.05
                      for b in p.blocks)
        xs = [ln.xheight for b in p.blocks if b.role in (Role.BODY, Role.TITLE) for ln in b.lines if ln.xheight]
        rest = [ln.xheight for q in pages[1:] for b in q.blocks if b.role == Role.BODY for ln in b.lines if ln.xheight]
        big_text = bool(xs and rest and statistics.median(xs) >= statistics.median(rest) * 1.6)
        if not (big_fig or big_text):
            return None
        cover = Block(id=f"{p.uid[:8]}-capa", role=Role.FIGURE, bbox=(0, 0, p.width, p.height), kind="capa",
                      flags=[COVER])
        p.blocks.append(cover)
    for b in p.blocks:
        if b is not cover and b.role != Role.ARTIFACT:
            b.role = Role.ARTIFACT
            if "na_capa" not in b.flags:
                b.flags.append("na_capa")
    if save_image is not None:
        cover.image_file = save_image(p) or cover.image_file
    return cover
