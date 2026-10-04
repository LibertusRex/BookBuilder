"""Leitura de um documento DOCX (por exemplo, o livro já revisado no Word)
para o mesmo modelo usado na conversão de imagens, de modo que o EPUB saia
pelo mesmo caminho: títulos e sumário, quadros, listas, citações, legendas,
notas de rodapé com links de ida e volta, imagens, tabelas e hiperlinks.

* Estilos "Título 1–6" / "Heading 1–6" / "Title" → títulos (nível do estilo);
  "Citação"/"Quote" → citação; "Legenda"/"Caption" → legenda; parágrafos
  com numeração ou estilo de lista → item de lista (o número/marcador
  automático do Word vira texto); parágrafos com borda (os "Quadros" do
  BookBuilder) → quadro.
* Negrito, itálico, sublinhado, sobrescrito e subscrito vêm do trecho ou,
  quando não marcados nele, do estilo do parágrafo.
* Cabeçalhos, rodapés e números de página do Word não vão para o EPUB.
* Nada é lido de fora do arquivo: imagens vinculadas (não incorporadas)
  são ignoradas.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.hyperlink import Hyperlink
from docx.text.paragraph import Paragraph
from docx.text.run import Run as DocxRun

from .model import Block, Page, PageStatus, Project, Role, Run, SourceRef, TableCell, merge_runs

HEADING_RE = re.compile(r"^(heading|título|titulo)\s*(\d)$", re.I)


@dataclass
class ImportResult:
    project: Project
    paragraphs: int = 0
    headings: int = 0
    footnotes: int = 0
    images: int = 0
    tables: int = 0
    links: int = 0
    notes: list[str] = field(default_factory=list)


class _Store:
    """O mínimo que os exportadores usam de um projeto salvo: a pasta das figuras."""

    def __init__(self, figures_dir: Path):
        self.figures_dir = figures_dir


def _style_chain(style):
    while style is not None:
        yield style
        style = style.base_style


def _style_flag(style, attr: str) -> bool:
    for st in _style_chain(style):
        v = getattr(st.font, attr, None)
        if v is not None:
            return bool(v)
    return False


def _has_border(par: Paragraph) -> bool:
    ppr = par._p.pPr
    if ppr is not None and ppr.find(qn("w:pBdr")) is not None:
        return True
    for st in _style_chain(par.style):
        sp = st.element.pPr
        if sp is not None and sp.find(qn("w:pBdr")) is not None:
            return True
    return False


def _first_indent(par: Paragraph) -> float:
    v = par.paragraph_format.first_line_indent
    if v is None:
        for st in _style_chain(par.style):
            v = st.paragraph_format.first_line_indent
            if v is not None:
                break
    return float(v.pt) if v is not None else 0.0


def _alignment(par: Paragraph) -> str:
    al = par.paragraph_format.alignment
    if al is None:
        for st in _style_chain(par.style):
            al = st.paragraph_format.alignment
            if al is not None:
                break
    name = str(al).lower() if al is not None else ""
    if "center" in name:
        return "center"
    if "right" in name:
        return "right"
    if "justify" in name or "both" in name:
        return "justify"
    return "left"


class _Numbering:
    """Números e marcadores automáticos do Word, convertidos em texto."""

    def __init__(self, doc):
        self.fmt: dict[tuple[str, int], tuple[str, str, int]] = {}
        self.count: dict[tuple[str, int], int] = {}
        try:
            numbering = doc.part.numbering_part.element
        except Exception:  # noqa: BLE001 - documento sem numeração
            return
        abstract = {}
        for an in numbering.findall(qn("w:abstractNum")):
            levels = {}
            for lvl in an.findall(qn("w:lvl")):
                ilvl = int(lvl.get(qn("w:ilvl")))
                nf = lvl.find(qn("w:numFmt"))
                lt = lvl.find(qn("w:lvlText"))
                st = lvl.find(qn("w:start"))
                levels[ilvl] = (nf.get(qn("w:val")) if nf is not None else "decimal",
                                lt.get(qn("w:val")) if lt is not None else "%1.",
                                int(st.get(qn("w:val"))) if st is not None else 1)
            abstract[an.get(qn("w:abstractNumId"))] = levels
        for num in numbering.findall(qn("w:num")):
            aid = num.find(qn("w:abstractNumId"))
            if aid is None:
                continue
            for ilvl, v in abstract.get(aid.get(qn("w:val")), {}).items():
                self.fmt[(num.get(qn("w:numId")), ilvl)] = v

    def label(self, par: Paragraph) -> str | None:
        ppr = par._p.pPr
        numpr = ppr.find(qn("w:numPr")) if ppr is not None else None
        if numpr is None:  # numeração herdada do estilo ("List Number", "List Bullet")
            for st in _style_chain(par.style):
                sp = st.element.pPr
                numpr = sp.find(qn("w:numPr")) if sp is not None else None
                if numpr is not None:
                    break
        if numpr is None:
            return None
        nid = numpr.find(qn("w:numId"))
        il = numpr.find(qn("w:ilvl"))
        if nid is None or nid.get(qn("w:val")) == "0":
            return None
        key = (nid.get(qn("w:val")), int(il.get(qn("w:val"))) if il is not None else 0)
        fmt, text, start = self.fmt.get(key, ("bullet", "•", 1))
        if fmt == "bullet":
            return "•" if not text or ord(text[0]) > 0xF000 else text
        n = self.count.get(key, start - 1) + 1
        self.count[key] = n
        for (nid2, il2) in list(self.count):  # reinicia os subníveis
            if nid2 == key[0] and il2 > key[1]:
                del self.count[(nid2, il2)]
        val = _fmt_number(n, fmt)
        return re.sub(r"%\d", val, text)


def _fmt_number(n: int, fmt: str) -> str:
    if fmt in ("lowerLetter", "upperLetter"):
        s = ""
        while n > 0:
            n, r = divmod(n - 1, 26)
            s = chr(97 + r) + s
        return s.upper() if fmt == "upperLetter" else s
    if fmt in ("lowerRoman", "upperRoman"):
        vals = [(1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"), (50, "l"), (40, "xl"),
                (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")]
        s = ""
        for v, sym in vals:
            while n >= v:
                s += sym
                n -= v
        return s.upper() if fmt == "upperRoman" else s
    return str(n)


class _Reader:
    def __init__(self, path: str, figures_dir: Path):
        self.path = path
        self.doc = Document(path)
        self.figures_dir = figures_dir
        self.numbering = _Numbering(self.doc)
        self.notes: dict[str, str] = {}  # id da nota no Word → chave da nota
        self.note_texts: dict[str, list[Run]] = {}
        self.res: ImportResult | None = None
        self._img_n = 0
        self._load_footnotes()

    # --- notas de rodapé ------------------------------------------------------------
    def _load_footnotes(self) -> None:
        part = None
        for rel in self.doc.part.rels.values():
            if rel.reltype.endswith("/footnotes"):
                part = rel.target_part
        if part is None:
            return
        from docx.oxml import parse_xml

        root = parse_xml(part.blob)
        for fn in root.findall(qn("w:footnote")):
            fid = fn.get(qn("w:id"))
            if fn.get(qn("w:type")) in ("separator", "continuationSeparator") or fid in ("-1", "0"):
                continue
            runs: list[Run] = []
            for k, p in enumerate(fn.findall(qn("w:p"))):
                if k:
                    runs.append(Run(" "))
                runs += self._xml_runs(p, part)
            # Tira o número da própria nota (w:footnoteRef) e o espaço inicial.
            while runs and not runs[0].text.strip():
                runs.pop(0)
            if runs:
                runs[0].text = runs[0].text.lstrip()
            self.note_texts[fid] = merge_runs(runs)

    def _xml_runs(self, p, part) -> list[Run]:
        """Trechos de um parágrafo lido direto do XML (notas de rodapé)."""
        par = Paragraph(p, self.doc.part)
        out: list[Run] = []
        for item in par.iter_inner_content():
            if isinstance(item, Hyperlink):
                target = self._link_target(item, part)
                for r in item.runs:
                    out += self._run(r, par, target)
            else:
                if item._r.find(qn("w:footnoteRef")) is not None:
                    continue
                out += self._run(item, par, None)
        return out

    # --- trechos ------------------------------------------------------------------------
    def _link_target(self, h: Hyperlink, part) -> str | None:
        rid = h._hyperlink.get(qn("r:id"))
        if rid and rid in part.rels:
            rel = part.rels[rid]
            if rel.is_external:
                return rel.target_ref
        return None

    def _run(self, r: DocxRun, par: Paragraph, link: str | None) -> list[Run]:
        ref = r._r.find(qn("w:footnoteReference"))
        if ref is not None:
            fid = ref.get(qn("w:id"))
            if fid in self.note_texts:
                key = self.notes.setdefault(fid, f"docx:{len(self.notes) + 1}")
                return [Run(key.split(":", 1)[1], note_ref=key)]
            return []
        text = r.text
        if not text:
            return []
        f = r.font
        st = par.style

        def flag(v, attr):
            if v is not None:
                return bool(v)
            if r.style is not None and r.style.name != "Default Paragraph Font" and _style_flag(r.style, attr):
                return True
            return _style_flag(st, attr)

        out = Run(text, bold=flag(f.bold, "bold"), italic=flag(f.italic, "italic"),
                  underline=bool(f.underline) if f.underline is not None else False,
                  superscript=bool(f.superscript), subscript=bool(f.subscript), link=link)
        if link and self.res is not None:
            self.res.links += 1
        return [out]

    def _images(self, el) -> list[Block]:
        out = []
        for blip in el.iter(qn("a:blip")):
            rid = blip.get(qn("r:embed"))
            if not rid or rid not in self.doc.part.related_parts:
                continue
            part = self.doc.part.related_parts[rid]
            ext = Path(str(part.partname)).suffix.lower() or ".png"
            if ext not in (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"):
                continue  # formatos que os leitores de EPUB não mostram (emf, wmf…)
            self._img_n += 1
            name = f"docx_img{self._img_n:04d}{ext}"
            (self.figures_dir / name).write_bytes(part.blob)
            alt = ""
            for dp in el.iter(qn("wp:docPr")):
                alt = dp.get("descr") or dp.get("title") or ""
                break
            out.append(Block(id=f"img{self._img_n}", role=Role.FIGURE, bbox=(0, 0, 0, 0), image_file=name, kind="figure",
                             runs=[Run(alt)] if alt else []))
        return out

    # --- documento ----------------------------------------------------------------------
    def read(self) -> ImportResult:
        doc = self.doc
        proj = Project(source_folder=str(Path(self.path).parent))
        self.res = res = ImportResult(project=proj)
        cp = doc.core_properties
        stem = Path(self.path).stem
        proj.export.base_name = stem
        proj.export.title = (cp.title or "").strip() or stem
        proj.export.author = (cp.author or "").strip() if (cp.author or "").strip() not in ("python-docx",) else ""
        proj.export.output_dir = str(Path(self.path).parent)
        proj.analysis["origem"] = "docx"

        pages: list[Page] = []
        page: Page | None = None
        order = 0
        box_n = 0
        in_box = False

        def new_page() -> Page:
            nonlocal order
            pg = Page(source=SourceRef(path=self.path, frame=len(pages)), status=PageStatus.DONE)
            pages.append(pg)
            order = 0
            return pg

        def add(b: Block) -> None:
            nonlocal order, page
            if page is None:
                page = new_page()
            b.order = order
            order += 1
            page.blocks.append(b)

        for el in doc.element.body.iterchildren():
            if el.tag == qn("w:tbl"):
                in_box = False
                tbl = Table(el, doc)
                cells = []
                for ri, row in enumerate(tbl.rows):
                    for ci, cell in enumerate(row.cells):
                        cells.append(TableCell(ri, ci, cell.text.strip()))
                res.tables += 1
                add(Block(id=f"t{res.tables}", role=Role.TABLE, bbox=(0, 0, 0, 0), table=cells, kind="table"))
                continue
            if el.tag != qn("w:p"):
                continue
            par = Paragraph(el, doc)
            imgs = self._images(el)
            runs: list[Run] = []
            for item in par.iter_inner_content():
                if isinstance(item, Hyperlink):
                    target = self._link_target(item, doc.part)
                    for r in item.runs:
                        runs += self._run(r, par, target)
                else:
                    runs += self._run(item, par, None)
            runs = merge_runs(runs)
            text = "".join(r.text for r in runs).strip()
            for im in imgs:
                in_box = False
                res.images += 1
                add(im)
            if not text:
                continue
            sname = par.style.name if par.style is not None else ""
            m = HEADING_RE.match(sname)
            role, level = Role.BODY, 0
            if m:
                role, level = Role.TITLE, int(m.group(2))
            elif sname.lower() in ("title", "título do documento"):
                role, level = Role.TITLE, 1
            elif sname.lower() in ("quote", "intense quote", "citação", "citação intensa"):
                role = Role.QUOTE
            elif sname.lower() in ("caption", "legenda"):
                role = Role.CAPTION
            label = self.numbering.label(par)
            if label is not None or "list" in sname.lower() or "lista" in sname.lower():
                if role == Role.BODY:
                    role = Role.LIST_ITEM
                if label:
                    runs = [Run(label + " ")] + runs
            if role == Role.TITLE:
                for r in runs:
                    r.bold = False  # o título já é negrito
            if role == Role.TITLE and level == 1 and page is not None and page.blocks:
                page = new_page()  # um capítulo por "página": as notas ficam no capítulo certo
            boxed = _has_border(par)
            if boxed and not in_box:
                box_n += 1
            in_box = boxed
            b = Block(id=f"p{res.paragraphs + 1}", role=role, bbox=(0, 0, 0, 0), runs=runs, heading_level=level,
                      align=_alignment(par), indent_first=20.0 if _first_indent(par) > 1 else 0.0,
                      box_id=f"q{box_n}" if boxed else None, kind=sname)
            res.paragraphs += 1
            if role == Role.TITLE:
                res.headings += 1
            add(b)
            # Notas chamadas neste parágrafo: no mesmo capítulo.
            for r in runs:
                if r.note_ref:
                    fid = next(k for k, v in self.notes.items() if v == r.note_ref)
                    add(Block(id=f"n{r.note_ref}", role=Role.FOOTNOTE, bbox=(0, 0, 0, 0), note_label=r.note_ref,
                              runs=list(self.note_texts[fid])))
                    res.footnotes += 1
        proj.pages = pages
        if not pages:
            res.notes.append("O documento não tem texto.")
        return res


def read_docx(path: str, figures_dir: str | Path) -> ImportResult:
    figures = Path(figures_dir)
    figures.mkdir(parents=True, exist_ok=True)
    return _Reader(path, figures).read()


def docx_to_epub(docx_path: str, epub_path: str, title: str = "", author: str = "", cover: str = "") -> tuple:
    """Converte um DOCX em EPUB 3. Devolve (ImportResult, EpubResult)."""
    import tempfile

    from .export import epub_writer

    with tempfile.TemporaryDirectory(prefix="bookbuilder_docx_") as tmp:
        res = read_docx(docx_path, tmp)
        if title:
            res.project.export.title = title
        if author:
            res.project.export.author = author
        if cover:
            res.project.export.cover_image = cover
        if not res.project.pages:
            raise ValueError("O documento não tem texto para converter.")
        er = epub_writer.write(res.project, _Store(Path(tmp)), epub_path)
    return res, er
