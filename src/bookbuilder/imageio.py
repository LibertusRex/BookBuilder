"""Leitura das imagens de origem: varredura de pasta, ordenação natural,
TIFF multipágina, DPI, miniaturas e aplicação do giro escolhido.

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

EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}

_num_re = re.compile(r"(\d+)")


def natural_key(name: str) -> list:
    """Chave de ordenação natural: "pagina 2" antes de "pagina 10"."""
    parts = _num_re.split(name.casefold())
    return [int(p) if p.isdigit() else p for p in parts]


def scan_folder(folder: str | os.PathLike) -> list[SourceRef]:
    """Lista as imagens da pasta (sem subpastas) em ordem natural, com um
    item por quadro de TIFF multipágina."""
    folder = Path(folder)
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
        for i in range(frames):
            refs.append(SourceRef(path=str(f), frame=i, size=st.st_size, mtime=st.st_mtime))
    return refs


def source_changed(ref: SourceRef) -> bool:
    try:
        st = os.stat(ref.path)
    except OSError:
        return True
    return st.st_size != ref.size or abs(st.st_mtime - ref.mtime) > 1


def open_frame(ref: SourceRef) -> Image.Image:
    """Abre o quadro pedido, aplicando a orientação EXIF, em RGB."""
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
    costumam ser valores padrão de capturas de tela, não medidas reais)."""
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
    im = open_frame(ref)
    im.draft("RGB", (size * 2, size * 2))  # leitura reduzida de JPEG: rápido e econômico
    im = im.convert("RGB")
    im.thumbnail((size, size))
    if rotation % 360:
        im = im.rotate(-rotation, expand=True)
    return im


def pages_from_folder(folder: str | os.PathLike) -> list[Page]:
    return [Page(source=ref, dpi=0.0) for ref in scan_folder(folder)]
