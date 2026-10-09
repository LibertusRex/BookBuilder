"""Exportação para EPUB 3 de texto fluido.

* HTML semântico: h1–h6, p, blockquote, figure/figcaption, table, listas como
  parágrafos com o marcador original.
* Um arquivo XHTML por capítulo (títulos de nível 1).
* Sumário navegável (nav.xhtml, e toc.ncx para leitores antigos).
* Notas de rodapé ligadas ao texto, com links de ida e volta
  (``epub:type="noteref"`` / ``epub:type="footnote"``).
* Cabeçalhos correntes, rodapés correntes e números impressos de página são
  omitidos: o EPUB é fluido e o leitor faz a própria paginação.
* Fontes e tamanhos estimados declarados no CSS (o leitor pode substituí-los).
"""

from __future__ import annotations

import datetime as _dt
import html
import mimetypes
import re
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from ..document import flow_pages
from ..layout.cleanup import TOC
from ..layout.noise import COVER
from ..layout.structure import FLOW_ROLES, box_group
from ..model import Block, Project, Role, Run, Style

XHTML_HEAD = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="{lang}" xml:lang="{lang}">
<head>
<meta charset="utf-8"/>
<title>{title}</title>
<link rel="stylesheet" type="text/css" href="estilo.css"/>
</head>
<body{body_attr}>
"""


@dataclass
class EpubResult:
    path: str
    chapters: int = 0
    footnotes: int = 0
    images: int = 0
    toc_entries: int = 0
    notes: list[str] = field(default_factory=list)


def esc(s: str) -> str:
    return html.escape(s, quote=True)


class _Chapter:
    def __init__(self, n: int, title: str):
        self.n = n
        self.title = title
        self.file = f"cap{n:03d}.xhtml"
        self.parts: list[str] = []
        self.notes: list[str] = []  # <aside> das notas, no fim do capítulo


@dataclass
class _Toc:
    level: int
    title: str
    href: str
    children: list["_Toc"] = field(default_factory=list)


from .docx_writer import URL_RE, url_href  # noqa: E402


def _runs_html(runs: list[Run], notes_map: dict[str, tuple[int, str]], mark_low: bool, ref_ids: set[str]) -> str:
    out = []
    for r in runs:
        if r.note_ref and r.note_ref in notes_map:
            n, label = notes_map[r.note_ref]
            rid = f"ref{n}"
            if rid in ref_ids:
                rid = f"ref{n}-{len(ref_ids)}"
            ref_ids.add(rid)
            out.append(f'<a epub:type="noteref" class="chamada" href="#nota{n}" id="{rid}">{esc(label)}</a>')
            continue
        t = esc(r.text).replace("\t", " ").replace("\n", "<br/>")
        if not t:
            continue
        if r.link:
            t = f'<a href="{esc(r.link)}">{t}</a>'
        else:
            t = URL_RE.sub(lambda m: f'<a href="{esc(url_href(m.group(1)))}">{m.group(1)}</a>', t)
        if r.superscript:
            t = f"<sup>{t}</sup>"
        if r.subscript:
            t = f"<sub>{t}</sub>"
        if r.underline:
            t = f'<span class="sublinhado">{t}</span>'
        if r.italic:
            t = f"<em>{t}</em>"
        if r.bold:
            t = f"<strong>{t}</strong>"
        if r.low_conf and mark_low:
            t = f'<span class="incerto">{t}</span>'
        out.append(t)
    return "".join(out)


def _css(project: Project, styles: dict[str, Style]) -> str:
    body = next((s for s in styles.values() if s.name == "Corpo do texto"), None)
    base = body.size if body else 11.0

    def fam(s: Style | None) -> str:
        if s is None:
            return "serif"
        generic = {"serif": "serif", "sans": "sans-serif", "mono": "monospace"}[s.category]
        names = [s.family]
        if s.detected_family and s.detected_family not in names:
            names.append(s.detected_family)
        return ", ".join(f'"{n}"' for n in names) + f", {generic}"

    lines = [
        "/* Fontes e tamanhos estimados a partir das imagens originais.",
        "   Leitores de EPUB podem substituir a fonte e o tamanho escolhidos. */",
        "body { font-family: %s; line-height: 1.4; margin: 0 5%%; }" % fam(body),
        "p { margin: 0; text-indent: 1.5em; text-align: justify; hyphens: auto; -epub-hyphens: auto; }",
        "p.sem-recuo, h1 + p, h2 + p, h3 + p, h4 + p, h5 + p, h6 + p { text-indent: 0; }",
        "p.esquerda { text-align: left; } p.centro { text-align: center; text-indent: 0; } p.direita { text-align: right; text-indent: 0; }",
        "p.item { text-indent: -1.2em; margin-left: 1.2em; }",
        "p.espaco { margin-top: 0.8em; }",
        "p.sumario { text-indent: 0; text-align: left; margin: 0.25em 0; }",
        "blockquote { margin: 0.6em 1.5em; font-size: 0.95em; } blockquote p { text-indent: 0; }",
        "div.quadro { border: 1px solid #888; border-radius: 4px; padding: 0.5em 0.8em; margin: 0.8em 0; }",
        "div.quadro p { text-indent: 0; }",
        "figure { margin: 1em 0; text-align: center; } figure img { max-width: 100%; height: auto; }",
        "figcaption, p.legenda { font-size: 0.9em; text-align: center; text-indent: 0; margin: 0.3em 0 0.8em; }",
        "table { border-collapse: collapse; margin: 0.8em auto; } td, th { border: 1px solid #888; padding: 0.2em 0.4em; vertical-align: top; }",
        "a.chamada { vertical-align: super; font-size: 0.75em; line-height: 0; text-decoration: none; }",
        "section.notas { margin-top: 2em; border-top: 1px solid #888; font-size: 0.85em; }",
        "aside.nota p { text-indent: 0; margin: 0.4em 0; }",
        "span.sublinhado { text-decoration: underline; }",
        "span.incerto { background: #fff3a0; }",
        "h1, h2, h3, h4, h5, h6 { text-indent: 0; text-align: left; line-height: 1.2; margin: 1.2em 0 0.6em; page-break-after: avoid; }",
    ]
    # Títulos: estilo dominante de cada nível.
    for lvl, st in project.analysis.get("_heading_styles", {}).items():
        s = styles.get(st)
        if s is None:
            continue
        rel = s.size / base if base else 1.0
        rules = [f"font-family: {fam(s)}", f"font-size: {rel:.2f}em", "font-weight: bold"]  # títulos sempre em negrito
        if s.italic:
            rules.append("font-style: italic")
        lines.append(f"h{lvl} {{ {'; '.join(rules)}; }}")
    # Demais estilos detectados como classes.
    for s in styles.values():
        if s.role in (Role.TITLE,) or s is body:
            continue
        rel = s.size / base if base else 1.0
        rules = [f"font-family: {fam(s)}", f"font-size: {rel:.2f}em"]
        if s.bold:
            rules.append("font-weight: bold")
        if s.italic:
            rules.append("font-style: italic")
        lines.append(f".{s.id} {{ {'; '.join(rules)}; }}")
    return "\n".join(lines) + "\n"


def write(project: Project, store, out_path: str) -> EpubResult:
    pages = flow_pages(project)
    if not pages:
        raise ValueError("Nenhuma página processada para exportar.")
    opts = project.export
    res = EpubResult(path=out_path)
    lang = opts.language or "pt-BR"
    title = opts.title or opts.base_name or "Documento"
    styles = project.styles
    figures_dir = Path(store.figures_dir) if store is not None else None

    # Estilo dominante por nível de título (para o CSS).
    from collections import Counter

    hs: dict[int, Counter] = {}
    for p in pages:
        for b in p.blocks:
            if b.role == Role.TITLE and b.heading_level and b.style_id:
                hs.setdefault(b.heading_level, Counter())[b.style_id] += 1
    project.analysis["_heading_styles"] = {lvl: c.most_common(1)[0][0] for lvl, c in hs.items()}

    # Notas: número sequencial por nota.
    note_runs: dict[str, list[Run]] = {}
    note_page: dict[str, int] = {}
    for pi, p in enumerate(pages):
        for b in p.ordered_blocks():
            if b.role == Role.FOOTNOTE and b.note_label:
                if b.continues_prev and b.note_label in note_runs:
                    note_runs[b.note_label] += [Run(" ")] + b.runs
                else:
                    note_runs[b.note_label] = list(b.runs)
                    note_page[b.note_label] = pi
    notes_map: dict[str, tuple[int, str]] = {}
    for i, key in enumerate(note_runs, start=1):
        notes_map[key] = (i, key.split(":", 1)[-1])
    referenced: set[str] = set()
    for p in pages:
        for b in p.blocks:
            for r in b.runs:
                if r.note_ref:
                    referenced.add(r.note_ref)

    chapters: list[_Chapter] = [_Chapter(1, title)]
    toc: list[_Toc] = []
    images: dict[str, str] = {}  # nome no EPUB → caminho de origem
    ref_ids: set[str] = set()
    hid = 0
    cover_src = ""
    heads: list[tuple[str, str]] = []  # (texto do título, destino), para o sumário do livro
    toc_pars: list[tuple[_Chapter, int, str, str]] = []
    open_par: dict | None = None  # parágrafo em construção (para continuações)
    open_box: str | None = None

    def ch() -> _Chapter:
        return chapters[-1]

    def flush_par():
        nonlocal open_par
        if open_par is not None:
            ch().parts.append(f'<p{open_par["attr"]}>{open_par["html"]}</p>')
            open_par = None

    def close_box():
        nonlocal open_box
        flush_par()
        if open_box is not None:
            ch().parts.append("</div>")
            open_box = None

    def add_note_aside(key: str):
        n, label = notes_map[key]
        body = _runs_html(note_runs[key], notes_map, opts.epub_mark_low_conf, ref_ids)
        back = f'<a href="#ref{n}">{esc(label)}</a>' if key in referenced else esc(label)
        ch().notes.append(f'<aside epub:type="footnote" class="nota" id="nota{n}"><p>{back} {body}</p></aside>')

    for pi, p in enumerate(pages):
        page_notes = [b for b in p.ordered_blocks() if b.role == Role.FOOTNOTE and b.note_label and not b.continues_prev]
        last_par_holder = None
        for b in p.ordered_blocks():
            if b.role.is_running or b.role == Role.FOOTNOTE:
                continue
            group = box_group(b)
            if b.continues_prev and open_par is not None and b.role in FLOW_ROLES:
                sep = "" if "juntar_sem_espaco" in b.flags else " "
                open_par["html"] += sep + _runs_html(b.runs, notes_map, opts.epub_mark_low_conf, ref_ids)
                last_par_holder = open_par
                continue
            flush_par()
            if group != open_box:
                close_box()
                if group is not None:
                    ch().parts.append('<div class="quadro">')
                    open_box = group
            if b.role == Role.TITLE and b.heading_level:
                lvl = min(6, b.heading_level)
                text_html = _runs_html(b.runs, notes_map, False, ref_ids)
                plain = re.sub(r"\s+", " ", b.text.replace("\n", " ")).strip()
                if lvl == 1 and (ch().parts or pi > 0) and open_box is None:
                    close_box()
                    chapters.append(_Chapter(len(chapters) + 1, plain or title))
                hid += 1
                anchor = f"t{hid}"
                ch().parts.append(f'<h{lvl} id="{anchor}">{text_html}</h{lvl}>')
                _toc_add(toc, _Toc(lvl, plain or f"Seção {hid}", f"{ch().file}#{anchor}"))
                heads.append((plain, f"{ch().file}#{anchor}"))
                continue
            if b.role == Role.FIGURE:
                src = figures_dir / b.image_file if (figures_dir and b.image_file) else None
                if src is None or not src.is_file():
                    continue
                if COVER in b.flags:  # capa fotografada: vira a capa do EPUB
                    cover_src = cover_src or str(src)
                    continue
                name = f"imagens/{b.image_file}"
                images[name] = str(src)
                alt = esc(b.text or "Imagem")
                ch().parts.append(f'<figure><img src="{name}" alt="{alt}"/></figure>')
                res.images += 1
                continue
            if b.role == Role.TABLE:
                ch().parts.append(_table_html(b, figures_dir, images, res))
                continue
            if TOC in b.flags and "\t" in b.text:
                # Sumário do livro impresso: sem o número da página (no EPUB o
                # leitor pagina conforme a letra); a entrada leva ao capítulo.
                cut = next(i for i, r in enumerate(b.runs) if "\t" in r.text)
                entry = b.runs[:cut] + [Run(b.runs[cut].text.split("\t", 1)[0])]
                toc_pars.append((ch(), len(ch().parts), _runs_html(entry, notes_map, opts.epub_mark_low_conf, ref_ids),
                                 "".join(r.text for r in entry)))
                ch().parts.append("")
                continue
            text_html = _runs_html(b.runs, notes_map, opts.epub_mark_low_conf, ref_ids)
            if not text_html.strip():
                continue
            if b.role == Role.CAPTION:
                ch().parts.append(f'<p class="legenda">{text_html}</p>')
                continue
            if b.role == Role.QUOTE:
                ch().parts.append(f"<blockquote><p>{text_html}</p></blockquote>")
                continue
            classes = []
            if b.role == Role.LIST_ITEM:
                classes.append("item")
            elif abs(b.indent_first) < 5:
                classes.append("sem-recuo")
            if b.align == "center":
                classes.append("centro")
            elif b.align == "right":
                classes.append("direita")
            elif b.align == "left" and len(b.lines) > 1:
                classes.append("esquerda")
            st = styles.get(b.style_id or "")
            if st is not None and st.name != "Corpo do texto" and st.role != Role.TITLE and not group:
                classes.append(st.id)
            attr = f' class="{" ".join(classes)}"' if classes else ""
            open_par = {"attr": attr, "html": text_html}
            last_par_holder = open_par
        # Notas da página: as sem chamada recebem uma no fim do último parágrafo.
        for nb in page_notes:
            key = nb.note_label
            if key not in notes_map:
                continue
            if key not in referenced:
                n, label = notes_map[key]
                anchor = f'<a epub:type="noteref" class="chamada" href="#nota{n}" id="ref{n}">{esc(label)}</a>'
                if last_par_holder is not None:
                    last_par_holder["html"] += anchor
                else:
                    flush_par()
                    ch().parts.append(f"<p>{anchor}</p>")
                referenced.add(key)
                res.notes.append(f"Nota sem chamada localizada em {p.source.display_name}: chamada inserida no fim do parágrafo.")
            add_note_aside(key)
            res.footnotes += 1
    close_box()
    for c, k, entry_html, entry in toc_pars:
        href = _toc_target(entry, heads)
        c.parts[k] = (f'<p class="sumario"><a href="{href}">{entry_html}</a></p>' if href
                      else f'<p class="sumario">{entry_html}</p>')

    res.chapters = len(chapters)
    res.toc_entries = _count(toc)
    _package(out_path, project, chapters, toc, images, _css(project, styles), lang, title, cover_src)
    res.notes.append("Leitores de EPUB podem substituir a fonte e o tamanho definidos; a paginação é a do leitor.")
    return res


def _table_html(b: Block, figures_dir, images: dict[str, str], res: EpubResult) -> str:
    if not b.table:
        out = []
        if b.image_file and figures_dir is not None and (figures_dir / b.image_file).is_file():
            name = f"imagens/{b.image_file}"
            images[name] = str(figures_dir / b.image_file)
            out.append(f'<figure><img src="{name}" alt="{esc(" ".join(ln.text for ln in b.lines)[:300] or "Tabela")}"/></figure>')
            res.images += 1
        return "".join(out)
    rows = max(c.row for c in b.table) + 1
    cols = max(c.col for c in b.table) + 1
    grid = [["" for _ in range(cols)] for _ in range(rows)]
    for c in b.table:
        grid[c.row][c.col] = esc(c.text)
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in grid)
    return f"<table>{body}</table>"


def _toc_target(entry: str, heads: list[tuple[str, str]]) -> str | None:
    """Título do livro a que leva a entrada do sumário: todas as palavras da
    entrada (sem o número de ordem) estão no título, e ele não tem muito mais."""
    def words(t: str) -> list[str]:
        return [w for w in re.sub(r"[^\w-]+", " ", t).casefold().split() if w]

    ew = words(re.sub(r"^\s*\d{1,3}\.\s+", "", entry))
    if not ew:
        return None
    for head, href in heads:
        hw = words(head)
        if all(w in hw for w in ew) and len(hw) <= len(ew) + 3:
            return href
    return None


def _toc_add(toc: list[_Toc], item: _Toc) -> None:
    level_list = toc
    while level_list and level_list[-1].level < item.level:
        level_list = level_list[-1].children
    level_list.append(item)


def _count(toc: list[_Toc]) -> int:
    return sum(1 + _count(t.children) for t in toc)


def _nav_ol(items: list[_Toc]) -> str:
    if not items:
        return ""
    lis = []
    for t in items:
        lis.append(f'<li><a href="{t.href}">{esc(t.title)}</a>{_nav_ol(t.children)}</li>')
    return "<ol>" + "".join(lis) + "</ol>"


def _ncx_points(items: list[_Toc], counter: list[int]) -> str:
    out = []
    for t in items:
        counter[0] += 1
        n = counter[0]
        out.append(f'<navPoint id="np{n}" playOrder="{n}"><navLabel><text>{esc(t.title)}</text></navLabel>'
                   f'<content src="{t.href}"/>{_ncx_points(t.children, counter)}</navPoint>')
    return "".join(out)


def _package(out_path: str, project: Project, chapters: list[_Chapter], toc: list[_Toc], images: dict[str, str],
             css: str, lang: str, title: str, cover_src: str = "") -> None:
    opts = project.export
    ident = project.analysis.get("epub_id") or f"urn:uuid:{uuid.uuid4()}"
    project.analysis["epub_id"] = ident
    modified = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest = ['<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
                '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
                '<item id="css" href="estilo.css" media-type="text/css"/>']
    spine = []
    cover_xhtml = None
    cover_name = None
    cover_file = opts.cover_image if opts.cover_image and Path(opts.cover_image).is_file() else cover_src
    if cover_file:
        ext = Path(cover_file).suffix.lower()
        cover_name = f"imagens/capa{ext}"
        mt = mimetypes.guess_type(cover_name)[0] or "image/jpeg"
        manifest.append(f'<item id="capa-img" href="{cover_name}" media-type="{mt}" properties="cover-image"/>')
        manifest.append('<item id="capa" href="capa.xhtml" media-type="application/xhtml+xml"/>')
        spine.append('<itemref idref="capa"/>')
        cover_xhtml = (XHTML_HEAD.format(lang=lang, title=esc(title), body_attr=' epub:type="cover"')
                       + f'<figure><img src="{cover_name}" alt="Capa"/></figure>\n</body>\n</html>\n')
    for c in chapters:
        manifest.append(f'<item id="c{c.n}" href="{c.file}" media-type="application/xhtml+xml"/>')
        spine.append(f'<itemref idref="c{c.n}"/>')
    for i, name in enumerate(sorted(images)):
        mt = mimetypes.guess_type(name)[0] or "image/png"
        manifest.append(f'<item id="img{i}" href="{name}" media-type="{mt}"/>')
    meta = [
        f'<dc:identifier id="bookid">{esc(ident)}</dc:identifier>',
        f"<dc:title>{esc(title)}</dc:title>",
        f"<dc:language>{esc(lang)}</dc:language>",
        f'<meta property="dcterms:modified">{modified}</meta>',
    ]
    if opts.author:
        meta.append(f"<dc:creator>{esc(opts.author)}</dc:creator>")
    if opts.publisher:
        meta.append(f"<dc:publisher>{esc(opts.publisher)}</dc:publisher>")
    if project.analysis.get("origem") == "docx":
        meta.append("<dc:description>Convertido de DOCX pelo BookBuilder.</dc:description>")
    else:
        meta.append("<dc:description>Convertido de imagens de páginas pelo BookBuilder (OCR local).</dc:description>")
    if cover_name:
        meta.append('<meta name="cover" content="capa-img"/>')
    opf = ('<?xml version="1.0" encoding="utf-8"?>\n'
           '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid" xml:lang="' + esc(lang) + '">\n'
           '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">' + "".join(meta) + "</metadata>\n"
           "<manifest>" + "".join(manifest) + "</manifest>\n"
           '<spine toc="ncx">' + "".join(spine) + "</spine>\n</package>\n")
    nav = (XHTML_HEAD.format(lang=lang, title="Sumário", body_attr="")
           + '<nav epub:type="toc" id="toc"><h1>Sumário</h1>'
           + (_nav_ol(toc) or f'<ol><li><a href="{chapters[0].file}">{esc(title)}</a></li></ol>')
           + "</nav>\n</body>\n</html>\n")
    first_href = chapters[0].file
    points = _ncx_points(toc, [0]) or (f'<navPoint id="np1" playOrder="1"><navLabel><text>{esc(title)}</text>'
                                        f'</navLabel><content src="{first_href}"/></navPoint>')
    ncx = ('<?xml version="1.0" encoding="utf-8"?>\n<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
           f'<head><meta name="dtb:uid" content="{esc(ident)}"/></head><docTitle><text>{esc(title)}</text></docTitle>'
           f"<navMap>{points}</navMap></ncx>\n")
    container = ('<?xml version="1.0" encoding="UTF-8"?>\n<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                 '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>\n')
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", container, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/content.opf", opf, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/nav.xhtml", nav, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/toc.ncx", ncx, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/estilo.css", css, compress_type=zipfile.ZIP_DEFLATED)
        if cover_xhtml and cover_name:
            z.writestr("OEBPS/capa.xhtml", cover_xhtml, compress_type=zipfile.ZIP_DEFLATED)
            z.write(cover_file, f"OEBPS/{cover_name}", compress_type=zipfile.ZIP_DEFLATED)
        for c in chapters:
            body = "\n".join(c.parts)
            if c.notes:
                body += '\n<section class="notas" epub:type="footnotes">\n' + "\n".join(c.notes) + "\n</section>"
            if not body.strip():
                body = "<p></p>"
            doc = XHTML_HEAD.format(lang=lang, title=esc(c.title), body_attr="") + body + "\n</body>\n</html>\n"
            z.writestr(f"OEBPS/{c.file}", doc, compress_type=zipfile.ZIP_DEFLATED)
        for name, src in images.items():
            z.write(src, f"OEBPS/{name}", compress_type=zipfile.ZIP_DEFLATED)
