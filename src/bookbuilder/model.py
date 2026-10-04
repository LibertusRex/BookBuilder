"""Representação intermediária do documento.

Tudo o que o OCR, a análise de layout, a revisão e a exportação produzem ou
consomem passa por estas classes: projeto → páginas → blocos → linhas →
palavras, mais os estilos tipográficos do documento. Coordenadas são sempre em
pixels da *imagem de trabalho* da página (a imagem original já girada pelo
usuário e com a inclinação corrigida; ver ``Page.transform``).

As classes são dataclasses simples, serializadas em JSON (``to_dict`` /
``from_dict``), para que o projeto possa ser salvo e retomado.
"""

from __future__ import annotations

import dataclasses
import enum
import hashlib
from dataclasses import dataclass, field
from typing import Any

FORMAT_VERSION = 1

BBox = tuple[int, int, int, int]  # x0, y0, x1, y1


class Role(str, enum.Enum):
    """Função semântica de um bloco. Os seis primeiros são os que o usuário
    pode atribuir diretamente na revisão; os demais vêm da análise e também
    podem ser escolhidos."""

    BODY = "corpo"
    TITLE = "titulo"
    HEADER = "cabecalho"
    FOOTER = "rodape"
    PAGE_NUMBER = "numero_pagina"
    FOOTNOTE = "nota_rodape"
    CAPTION = "legenda"
    LIST_ITEM = "lista"
    QUOTE = "citacao"
    TABLE = "tabela"
    FIGURE = "imagem"
    ARTIFACT = "artefato"  # interface do leitor, barra de status, sobreposições: ignorado

    @property
    def label(self) -> str:
        return ROLE_LABELS[self]

    @property
    def is_running(self) -> bool:
        """Conteúdo de margem que não pertence ao corpo do texto."""
        return self in (Role.HEADER, Role.FOOTER, Role.PAGE_NUMBER, Role.ARTIFACT)


ROLE_LABELS = {
    Role.BODY: "Corpo do texto",
    Role.TITLE: "Título",
    Role.HEADER: "Cabeçalho",
    Role.FOOTER: "Rodapé",
    Role.PAGE_NUMBER: "Número de página",
    Role.FOOTNOTE: "Nota de rodapé",
    Role.CAPTION: "Legenda",
    Role.LIST_ITEM: "Item de lista",
    Role.QUOTE: "Citação",
    Role.TABLE: "Tabela",
    Role.FIGURE: "Imagem",
    Role.ARTIFACT: "Ignorar (artefato)",
}

ROLE_COLORS = {  # cores das marcações na revisão (RGB)
    Role.BODY: (66, 133, 244),
    Role.TITLE: (156, 39, 176),
    Role.HEADER: (230, 81, 0),
    Role.FOOTER: (230, 81, 0),
    Role.PAGE_NUMBER: (255, 160, 0),
    Role.FOOTNOTE: (0, 150, 136),
    Role.CAPTION: (121, 85, 72),
    Role.LIST_ITEM: (63, 81, 181),
    Role.QUOTE: (0, 121, 107),
    Role.TABLE: (76, 175, 80),
    Role.FIGURE: (139, 195, 74),
    Role.ARTIFACT: (120, 120, 120),
}


class PageStatus(str, enum.Enum):
    PENDING = "pendente"
    DONE = "concluida"
    ERROR = "erro"


# ---------------------------------------------------------------------------
# Serialização genérica


def _encode(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return value.to_dict() if hasattr(value, "to_dict") else {
            f.name: _encode(getattr(value, f.name)) for f in dataclasses.fields(value)
        }
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, (list, tuple)):
        return [_encode(v) for v in value]
    if isinstance(value, dict):
        return {k: _encode(v) for k, v in value.items()}
    return value


def _bbox(v: Any) -> BBox:
    return tuple(int(x) for x in v)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Texto


@dataclass(slots=True)
class Run:
    """Trecho de texto com formatação uniforme."""

    text: str
    bold: bool = False
    italic: bool = False
    underline: bool = False
    superscript: bool = False
    subscript: bool = False
    low_conf: bool = False
    note_ref: str | None = None  # chamada de nota de rodapé ("1", "*")
    link: str | None = None  # destino do hiperlink (documentos importados de DOCX)

    def same_format(self, other: "Run") -> bool:
        return (
            self.bold == other.bold
            and self.italic == other.italic
            and self.underline == other.underline
            and self.superscript == other.superscript
            and self.subscript == other.subscript
            and self.low_conf == other.low_conf
            and self.note_ref is None
            and other.note_ref is None
            and self.link == other.link
        )

    def to_dict(self) -> dict:
        d: dict[str, Any] = {"t": self.text}
        for k in ("bold", "italic", "underline", "superscript", "subscript", "low_conf"):
            if getattr(self, k):
                d[k] = True
        if self.note_ref is not None:
            d["note_ref"] = self.note_ref
        if self.link:
            d["link"] = self.link
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Run":
        return cls(
            text=d["t"],
            bold=d.get("bold", False),
            italic=d.get("italic", False),
            underline=d.get("underline", False),
            superscript=d.get("superscript", False),
            subscript=d.get("subscript", False),
            low_conf=d.get("low_conf", False),
            note_ref=d.get("note_ref"),
            link=d.get("link"),
        )


def merge_runs(runs: list[Run]) -> list[Run]:
    out: list[Run] = []
    for r in runs:
        if not r.text:
            continue
        if out and out[-1].same_format(r):
            prev = out[-1]
            out[-1] = Run(prev.text + r.text, prev.bold, prev.italic, prev.underline, prev.superscript,
                          prev.subscript, prev.low_conf, prev.note_ref, prev.link)
        else:
            out.append(r)
    return out


def runs_text(runs: list[Run]) -> str:
    return "".join(r.text for r in runs)


@dataclass(slots=True)
class Word:
    text: str
    bbox: BBox
    conf: float  # 0–100
    bold: bool = False
    italic: bool = False
    underline: bool = False
    superscript: bool = False
    subscript: bool = False
    stroke: float = 0.0  # espessura de traço média, px
    slant: float = 0.0  # inclinação estimada (tangente)
    chars: list[tuple[str, BBox]] | None = None  # caixas por caractere (só durante a análise da página)
    tail_sup: str = ""  # algarismos/sinais sobrescritos no fim da palavra (chamada de nota)

    def to_dict(self) -> dict:
        d: dict[str, Any] = {"t": self.text, "b": list(self.bbox), "c": round(self.conf, 1)}
        for k in ("bold", "italic", "underline", "superscript", "subscript"):
            if getattr(self, k):
                d[k] = True
        if self.stroke:
            d["sw"] = round(self.stroke, 2)
        if self.slant:
            d["sl"] = round(self.slant, 3)
        if self.chars:
            d["ch"] = [[c, list(b)] for c, b in self.chars]
        if self.tail_sup:
            d["ts"] = self.tail_sup
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Word":
        return cls(
            text=d["t"],
            bbox=_bbox(d["b"]),
            conf=d.get("c", 0.0),
            bold=d.get("bold", False),
            italic=d.get("italic", False),
            underline=d.get("underline", False),
            superscript=d.get("superscript", False),
            subscript=d.get("subscript", False),
            stroke=d.get("sw", 0.0),
            slant=d.get("sl", 0.0),
            chars=[(c, _bbox(b)) for c, b in d["ch"]] if d.get("ch") else None,
            tail_sup=d.get("ts", ""),
        )


@dataclass(slots=True)
class Line:
    bbox: BBox
    words: list[Word] = field(default_factory=list)
    baseline: int = 0  # y da linha de base
    xheight: float = 0.0  # altura de x, px
    capheight: float = 0.0  # altura das maiúsculas, px

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    def to_dict(self) -> dict:
        return {
            "b": list(self.bbox),
            "bl": self.baseline,
            "xh": round(self.xheight, 2),
            "ch": round(self.capheight, 2),
            "w": [w.to_dict() for w in self.words],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Line":
        return cls(
            bbox=_bbox(d["b"]),
            baseline=d.get("bl", 0),
            xheight=d.get("xh", 0.0),
            capheight=d.get("ch", 0.0),
            words=[Word.from_dict(w) for w in d.get("w", [])],
        )


# ---------------------------------------------------------------------------
# Blocos


@dataclass
class TableCell:
    row: int
    col: int
    text: str
    rowspan: int = 1
    colspan: int = 1
    bbox: BBox = (0, 0, 0, 0)


@dataclass
class Block:
    """Unidade de conteúdo em ordem de leitura: em geral um parágrafo, um
    título, uma linha de cabeçalho, uma nota, uma tabela ou uma imagem."""

    id: str
    role: Role
    bbox: BBox
    kind: str = ""  # rótulo bruto do detector de layout (text, paragraph_title...)
    lines: list[Line] = field(default_factory=list)
    runs: list[Run] = field(default_factory=list)
    conf: float = 100.0
    order: int = 0
    style_id: str | None = None
    heading_level: int = 0  # 1..6 para títulos
    align: str = "left"  # left, right, center, justify
    indent_first: float = 0.0  # recuo da primeira linha, px (negativo = deslocado)
    indent_left: float = 0.0  # px a partir da margem esquerda do texto da página
    indent_right: float = 0.0
    space_before: float = 0.0  # px
    line_pitch: float = 0.0  # distância entre linhas de base, px
    column: int = 0
    box_id: str | None = None  # quadro/caixa delimitada que contém o bloco
    continues_prev: bool = False  # continua o parágrafo da página anterior
    note_label: str | None = None  # para notas de rodapé: "1", "*", ...
    table: list[TableCell] | None = None
    image_file: str | None = None  # recorte salvo na pasta do projeto
    flags: list[str] = field(default_factory=list)
    user_edited: bool = False  # texto editado na revisão
    role_locked: bool = False  # função definida pelo usuário: a análise não muda
    occluded: bool = False  # parte do bloco estava encoberta por sobreposição
    surya_html: str | None = None  # releitura do bloco pelo Surya (HTML), quando houver
    disputes: list[dict] = field(default_factory=list)  # divergências Tesseract × Surya ainda abertas
    choices: dict[str, str] = field(default_factory=dict)  # decisões (árbitro/usuário) por divergência

    @property
    def text(self) -> str:
        return runs_text(self.runs)

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "role": self.role.value,
            "b": list(self.bbox),
            "kind": self.kind,
            "lines": [ln.to_dict() for ln in self.lines],
            "runs": [r.to_dict() for r in self.runs],
            "conf": round(self.conf, 1),
            "order": self.order,
            "style": self.style_id,
            "hl": self.heading_level,
            "align": self.align,
            "if": round(self.indent_first, 1),
            "il": round(self.indent_left, 1),
            "ir": round(self.indent_right, 1),
            "sb": round(self.space_before, 1),
            "lp": round(self.line_pitch, 1),
            "col": self.column,
            "box": self.box_id,
            "cont": self.continues_prev,
            "note": self.note_label,
            "img": self.image_file,
            "flags": list(self.flags),
            "edited": self.user_edited,
            "locked": self.role_locked,
            "occ": self.occluded,
        }
        if self.surya_html is not None:
            d["surya"] = self.surya_html
        if self.disputes:
            d["disp"] = self.disputes
        if self.choices:
            d["choices"] = self.choices
        if self.table is not None:
            d["table"] = [_encode(c) for c in self.table]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Block":
        return cls(
            id=d["id"],
            role=Role(d["role"]),
            bbox=_bbox(d["b"]),
            kind=d.get("kind", ""),
            lines=[Line.from_dict(x) for x in d.get("lines", [])],
            runs=[Run.from_dict(x) for x in d.get("runs", [])],
            conf=d.get("conf", 100.0),
            order=d.get("order", 0),
            style_id=d.get("style"),
            heading_level=d.get("hl", 0),
            align=d.get("align", "left"),
            indent_first=d.get("if", 0.0),
            indent_left=d.get("il", 0.0),
            indent_right=d.get("ir", 0.0),
            space_before=d.get("sb", 0.0),
            line_pitch=d.get("lp", 0.0),
            column=d.get("col", 0),
            box_id=d.get("box"),
            continues_prev=d.get("cont", False),
            note_label=d.get("note"),
            table=[TableCell(**{**c, "bbox": _bbox(c.get("bbox", (0, 0, 0, 0)))}) for c in d["table"]]
            if d.get("table") is not None
            else None,
            image_file=d.get("img"),
            flags=list(d.get("flags", [])),
            user_edited=d.get("edited", False),
            role_locked=d.get("locked", False),
            occluded=d.get("occ", False),
            surya_html=d.get("surya"),
            disputes=list(d.get("disp", [])),
            choices=dict(d.get("choices", {})),
        )


# ---------------------------------------------------------------------------
# Páginas


@dataclass
class SourceRef:
    path: str
    frame: int = 0  # quadro de TIFF multipágina
    size: int = 0  # bytes, para detectar alteração do arquivo
    mtime: float = 0.0

    @property
    def uid(self) -> str:
        h = hashlib.sha1(f"{self.path}|{self.frame}".encode("utf-8")).hexdigest()
        return h[:16]

    @property
    def display_name(self) -> str:
        import os

        name = os.path.basename(self.path)
        return f"{name} [quadro {self.frame + 1}]" if self.frame else name


@dataclass
class Overlay:
    """Região da imagem encoberta por algo que não é o documento (aviso do
    sistema, botões do leitor). O texto embaixo dela é desconhecido."""

    bbox: BBox
    kind: str = "sobreposicao"


@dataclass
class Page:
    source: SourceRef
    rotation: int = 0  # giro escolhido pelo usuário: 0, 90, 180, 270 (horário)
    excluded: bool = False
    status: PageStatus = PageStatus.PENDING
    error: str = ""
    # Resultado do processamento
    width: int = 0
    height: int = 0
    deskew: float = 0.0  # graus aplicados para endireitar
    auto_rotation: int = 0  # giro adicional detectado pela orientação (OSD)
    inverted: bool = False  # página em modo escuro (texto claro em fundo escuro)
    dpi: float = 0.0  # DPI informado no arquivo (0 = ausente)
    blocks: list[Block] = field(default_factory=list)
    overlays: list[Overlay] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    printed_number: str | None = None  # número impresso detectado na página
    duplicate_of: str | None = None  # uid da página idêntica anterior
    mean_conf: float = 0.0
    surya: bool = False  # texto relido pelo Surya

    @property
    def uid(self) -> str:
        return self.source.uid

    def ordered_blocks(self) -> list[Block]:
        return sorted(self.blocks, key=lambda b: b.order)

    def to_dict(self) -> dict:
        return {
            "source": _encode(self.source),
            "rotation": self.rotation,
            "excluded": self.excluded,
            "status": self.status.value,
            "error": self.error,
            "width": self.width,
            "height": self.height,
            "deskew": round(self.deskew, 3),
            "auto_rotation": self.auto_rotation,
            "inverted": self.inverted,
            "dpi": self.dpi,
            "blocks": [b.to_dict() for b in self.blocks],
            "overlays": [{"b": list(o.bbox), "kind": o.kind} for o in self.overlays],
            "flags": list(self.flags),
            "printed_number": self.printed_number,
            "duplicate_of": self.duplicate_of,
            "mean_conf": round(self.mean_conf, 1),
            "surya": self.surya,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Page":
        return cls(
            source=SourceRef(**d["source"]),
            rotation=d.get("rotation", 0),
            excluded=d.get("excluded", False),
            status=PageStatus(d.get("status", PageStatus.PENDING.value)),
            error=d.get("error", ""),
            width=d.get("width", 0),
            height=d.get("height", 0),
            deskew=d.get("deskew", 0.0),
            auto_rotation=d.get("auto_rotation", 0),
            inverted=d.get("inverted", False),
            dpi=d.get("dpi", 0.0),
            blocks=[Block.from_dict(b) for b in d.get("blocks", [])],
            overlays=[Overlay(_bbox(o["b"]), o.get("kind", "sobreposicao")) for o in d.get("overlays", [])],
            flags=list(d.get("flags", [])),
            printed_number=d.get("printed_number"),
            duplicate_of=d.get("duplicate_of"),
            mean_conf=d.get("mean_conf", 0.0),
            surya=d.get("surya", False),
        )

    def clear_results(self) -> None:
        self.status = PageStatus.PENDING
        self.error = ""
        self.blocks = []
        self.overlays = []
        self.flags = []
        self.printed_number = None
        self.duplicate_of = None
        self.surya = False


# ---------------------------------------------------------------------------
# Estilos


@dataclass
class Style:
    """Estilo tipográfico detectado (agrupamento de blocos parecidos)."""

    id: str
    name: str
    role: Role = Role.BODY
    heading_level: int = 0
    category: str = "serif"  # serif, sans, mono
    detected_family: str = ""  # fonte instalada mais parecida
    match_score: float = 0.0  # 0–1
    family_override: str = ""  # escolhida pelo usuário
    size_pt: float = 11.0  # estimado
    size_override: float = 0.0
    bold: bool = False
    italic: bool = False
    xheight_px: float = 0.0
    stroke_ratio: float = 0.0
    condensed: float = 1.0  # largura relativa ao corpo
    sample_count: int = 0
    missing_font: str = ""  # família pedida pelo usuário mas não instalada

    @property
    def family(self) -> str:
        return self.family_override or self.detected_family or GENERIC_FALLBACK[self.category]

    @property
    def size(self) -> float:
        return self.size_override or self.size_pt

    def category_label(self) -> str:
        return CATEGORY_LABELS.get(self.category, self.category)

    def to_dict(self) -> dict:
        return _encode({f.name: getattr(self, f.name) for f in dataclasses.fields(self)})

    @classmethod
    def from_dict(cls, d: dict) -> "Style":
        d = dict(d)
        d["role"] = Role(d.get("role", Role.BODY.value))
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


GENERIC_FALLBACK = {"serif": "Georgia", "sans": "Segoe UI", "mono": "Consolas"}
CATEGORY_LABELS = {"serif": "serifada", "sans": "sem serifa", "mono": "monoespaçada"}


# ---------------------------------------------------------------------------
# Projeto


@dataclass
class Settings:
    language: str = "por"  # por, eng, por+eng
    # Escala tipográfica: como converter pixels em pontos.
    scale_mode: str = "auto"  # auto, dpi, page_size, body_size
    dpi_override: float = 0.0
    page_width_mm: float = 0.0  # 0 = desconhecido
    page_height_mm: float = 0.0
    body_size_pt: float = 11.0
    detect_orientation: bool = True
    remove_duplicates: bool = True
    low_conf_threshold: float = 50.0  # palavras abaixo disso são destacadas para revisão
    use_surya: bool = True  # reler o texto com o Surya quando houver placa de vídeo dedicada
    use_arbiter: bool = True  # terceira opinião (recorte ampliado) para as divergências


@dataclass
class ExportOptions:
    output_dir: str = ""
    base_name: str = ""
    make_docx: bool = True
    make_epub: bool = True
    docx_page_breaks: bool = False  # preservar as quebras das páginas originais
    docx_headers_footers: bool = True
    highlight_low_conf: bool = True
    epub_mark_low_conf: bool = False
    title: str = ""
    author: str = ""
    language: str = "pt-BR"
    cover_image: str = ""
    publisher: str = ""


@dataclass
class Project:
    source_folder: str = ""
    pages: list[Page] = field(default_factory=list)
    styles: dict[str, Style] = field(default_factory=dict)
    settings: Settings = field(default_factory=Settings)
    export: ExportOptions = field(default_factory=ExportOptions)
    analysis: dict[str, Any] = field(default_factory=dict)  # resumo da análise do documento
    version: int = FORMAT_VERSION

    def active_pages(self) -> list[Page]:
        return [p for p in self.pages if not p.excluded]

    def page_by_uid(self, uid: str) -> Page | None:
        for p in self.pages:
            if p.uid == uid:
                return p
        return None

    def to_dict(self, include_blocks: bool = True) -> dict:
        pages = []
        for p in self.pages:
            d = p.to_dict()
            if not include_blocks:
                d.pop("blocks")
            pages.append(d)
        return {
            "version": self.version,
            "source_folder": self.source_folder,
            "pages": pages,
            "styles": {k: s.to_dict() for k, s in self.styles.items()},
            "settings": _encode(self.settings),
            "export": _encode(self.export),
            "analysis": self.analysis,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Project":
        def known(klass, data):
            names = {f.name for f in dataclasses.fields(klass)}
            return klass(**{k: v for k, v in (data or {}).items() if k in names})

        return cls(
            version=d.get("version", FORMAT_VERSION),
            source_folder=d.get("source_folder", ""),
            pages=[Page.from_dict(p) for p in d.get("pages", [])],
            styles={k: Style.from_dict(s) for k, s in d.get("styles", {}).items()},
            settings=known(Settings, d.get("settings")),
            export=known(ExportOptions, d.get("export")),
            analysis=d.get("analysis", {}),
        )
