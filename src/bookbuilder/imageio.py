"""Leitura das imagens de origem: varredura de pasta, ordenação natural,
TIFF multipágina, PDF (uma página por quadro), DPI, miniaturas e aplicação
do giro escolhido.

As imagens originais nunca são alteradas: tudo é feito em memória.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from .model import Page, SourceRef

Image.MAX_IMAGE_PIXELS = 400_000_000  # páginas digitalizadas em alta resolução

EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".pdf"}

_num_re = re.compile(r"(\d+)")


def natural_key(name: str) -> list:
    """Chave de ordenação natural: "pagina 2" antes de "pagina 10"."""
    parts = _num_re.split(name.casefold())
    return [int(p) if p.isdigit() else p for p in parts]


def is_pdf(path: str | os.PathLike) -> bool:
    return str(path).lower().endswith(".pdf")


def scan_kind(ref: SourceRef) -> str:
    """"pdf" (PDF de aplicativo de digitalização), "foto" (foto de câmera)
    ou "" (captura de tela, digitalização comum). Nas duas primeiras não há
    interface de leitor sobreposta, mas pode haver sombra e luz desigual."""
    if is_pdf(ref.path):
        return "pdf"
    if not ref.path.lower().endswith((".jpg", ".jpeg", ".tif", ".tiff")):
        return ""
    try:
        with Image.open(ref.path) as im:
            exif = im.getexif()
    except Exception:
        return ""
    return "foto" if exif.get(0x010F) or exif.get(0x0110) else ""  # Make / Model da câmera


def scan_folder(folder: str | os.PathLike) -> list[SourceRef]:
    """Lista as imagens da pasta (sem subpastas) em ordem natural, com um
    item por quadro de TIFF multipágina ou página de PDF. ``folder`` também
    pode ser um único arquivo PDF."""
    folder = Path(folder)
    if folder.is_file():
        files = [folder]
    else:
        files = [
            f for f in folder.iterdir()
            if f.is_file() and f.suffix.lower() in EXTENSIONS and not f.name.startswith("~$")
        ]
        files.sort(key=lambda f: natural_key(f.name))
    refs: list[SourceRef] = []
    for f in files:
        st = f.stat()
        frames = 1
        if f.suffix.lower() in (".tif", ".tiff"):
            try:
                with Image.open(f) as im:
                    frames = getattr(im, "n_frames", 1)
            except Exception:
                frames = 1
        elif is_pdf(f):
            frames = len(_pdf(str(f)))  # PDF danificado ou protegido: o erro chega ao usuário
        for i in range(frames):
            refs.append(SourceRef(path=str(f), frame=i, size=st.st_size, mtime=st.st_mtime))
    return refs


def source_changed(ref: SourceRef) -> bool:
    try:
        st = os.stat(ref.path)
    except OSError:
        return True
    return st.st_size != ref.size or abs(st.st_mtime - ref.mtime) > 1


def _pdf(path: str):
    import pypdfium2 as pdfium

    return pdfium.PdfDocument(path)


def _pdf_page(ref: SourceRef, max_side: int = 0) -> Image.Image:
    """Página do PDF como imagem. Páginas de aplicativos de digitalização são
    uma foto só: a página é renderizada na resolução dessa foto (sem perder
    nem inventar detalhe), já com o giro com que o PDF a mostra. Páginas sem
    imagem (texto vetorial) saem a 300 dpi."""
    import pypdfium2 as pdfium

    doc = _pdf(ref.path)
    try:
        page = doc[ref.frame]
        w, h = page.get_size()
        px = [o.get_px_size() for o in page.get_objects() if o.type == pdfium.raw.FPDF_PAGEOBJ_IMAGE]
        scale = max(max(p) for p in px) / max(w, h) if px else 300 / 72
        scale = min(max(scale, 150 / 72), 600 / 72)
        if max_side:
            scale = min(scale, max_side / max(w, h))
        im = page.render(scale=scale).to_pil()
        page.close()
    finally:
        doc.close()
    return im.convert("RGB")


def open_frame(ref: SourceRef) -> Image.Image:
    """Abre o quadro pedido, aplicando a orientação EXIF, em RGB."""
    if is_pdf(ref.path):
        return _pdf_page(ref)
    im = Image.open(ref.path)
    if ref.frame:
        im.seek(ref.frame)
    im = ImageOps.exif_transpose(im) if not ref.frame else im
    if im.mode not in ("RGB", "L"):
        if "A" in im.getbands() or im.mode == "P":
            bg = Image.new("RGB", im.size, (255, 255, 255))
            im = im.convert("RGBA")
            bg.paste(im, mask=im.getchannel("A"))
            im = bg
        else:
            im = im.convert("RGB")
    return im


def read_dpi(ref: SourceRef) -> float:
    """DPI gravado no arquivo, ou 0 quando ausente ou não confiável (72/96
    costumam ser valores padrão de capturas de tela, não medidas reais; o
    tamanho de página de PDFs de digitalização é um padrão do aplicativo,
    não o do livro)."""
    if is_pdf(ref.path):
        return 0.0
    try:
        with Image.open(ref.path) as im:
            if ref.frame:
                im.seek(ref.frame)
            dpi = im.info.get("dpi")
    except Exception:
        return 0.0
    if not dpi:
        return 0.0
    try:
        x = float(dpi[0])
    except (TypeError, ValueError, IndexError):
        return 0.0
    if x < 100 or x > 2400:
        return 0.0
    return x


def load_rgb(ref: SourceRef, rotation: int = 0) -> np.ndarray:
    """Imagem RGB (H×W×3, uint8) já girada pelo usuário (graus, horário)."""
    im = open_frame(ref)
    if rotation % 360:
        im = im.rotate(-rotation, expand=True)
    arr = np.asarray(im.convert("RGB"))
    return np.ascontiguousarray(arr)


def thumbnail(ref: SourceRef, rotation: int = 0, size: int = 200) -> Image.Image:
    if is_pdf(ref.path):
        im = _pdf_page(ref, max_side=size * 2)
        im.thumbnail((size, size))
        return im.rotate(-rotation, expand=True) if rotation % 360 else im
    im = open_frame(ref)
    im.draft("RGB", (size * 2, size * 2))  # leitura reduzida de JPEG: rápido e econômico
    im = im.convert("RGB")
    im.thumbnail((size, size))
    if rotation % 360:
        im = im.rotate(-rotation, expand=True)
    return im


def pages_from_folder(folder: str | os.PathLike) -> list[Page]:
    return [Page(source=ref, dpi=0.0) for ref in scan_folder(folder)]
