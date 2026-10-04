"""Execução do Tesseract e leitura da saída hOCR.

Segurança: o Tesseract é chamado com uma lista de argumentos (sem shell) e a
imagem é gravada num arquivo temporário de nome fixo, numa pasta temporária
criada pelo sistema. O nome dos arquivos do usuário nunca entra no comando.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field

import cv2
import numpy as np
from lxml import html

from .. import paths
from ..model import BBox

_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class TesseractError(RuntimeError):
    pass


@dataclass
class OcrChar:
    text: str
    bbox: BBox
    conf: float


@dataclass
class OcrWord:
    text: str
    bbox: BBox
    conf: float
    chars: list[OcrChar] = field(default_factory=list)


@dataclass
class OcrLine:
    bbox: BBox
    words: list[OcrWord]
    baseline: tuple[float, float] = (0.0, 0.0)  # inclinação, deslocamento (a partir do fundo da caixa)
    x_size: float = 0.0
    x_descenders: float = 0.0
    x_ascenders: float = 0.0
    par_id: str = ""
    block_id: str = ""


def _env() -> dict:
    env = dict(os.environ)
    env["TESSDATA_PREFIX"] = str(paths.tessdata_dir())
    env["OMP_THREAD_LIMIT"] = "1"  # paralelismo é feito por processos
    return env


def _run(args: list[str], timeout: float) -> bytes:
    exe = paths.tesseract_exe()
    if not exe.is_file():
        raise TesseractError("O Tesseract não foi encontrado. Execute o instalador de componentes.")
    try:
        r = subprocess.run(
            [str(exe), *args],
            capture_output=True,
            env=_env(),
            timeout=timeout,
            creationflags=_CREATE_NO_WINDOW,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise TesseractError("O Tesseract demorou demais nesta página.") from e
    if r.returncode != 0:
        msg = r.stderr.decode("utf-8", errors="replace").strip()
        raise TesseractError(f"Falha no Tesseract: {msg[:300]}")
    return r.stdout


def _with_temp_image(img: np.ndarray, fn):
    with tempfile.TemporaryDirectory(prefix="bookbuilder_") as d:
        p = os.path.join(d, "pagina.png")
        ok, buf = cv2.imencode(".png", img)
        if not ok:
            raise TesseractError("Não foi possível preparar a imagem para o OCR.")
        with open(p, "wb") as f:
            f.write(buf.tobytes())
        return fn(p)


def version() -> str:
    out = _run(["--version"], timeout=30)
    return out.decode("utf-8", errors="replace").splitlines()[0] if out else ""


_title_re = re.compile(r"(\w+)\s+([^;]+)")


def _title(el) -> dict[str, list[str]]:
    t = el.get("title", "")
    out: dict[str, list[str]] = {}
    for part in t.split(";"):
        part = part.strip()
        if not part:
            continue
        m = _title_re.match(part)
        if m:
            out[m.group(1)] = m.group(2).split()
    return out


def _bbox_of(props: dict, key: str = "bbox") -> BBox:
    v = props.get(key) or ["0", "0", "0", "0"]
    return tuple(int(float(x)) for x in v[:4])  # type: ignore[return-value]


def parse_hocr(data: bytes) -> list[OcrLine]:
    doc = html.fromstring(data)
    lines: list[OcrLine] = []
    for line_el in doc.xpath("//*[contains(concat(' ', normalize-space(@class), ' '), ' ocr_line ') "
                             "or contains(@class, 'ocr_caption') or contains(@class, 'ocr_textfloat') "
                             "or contains(@class, 'ocr_header')]"):
        props = _title(line_el)
        words: list[OcrWord] = []
        for w_el in line_el.xpath(".//*[contains(@class, 'ocrx_word')]"):
            wp = _title(w_el)
            chars: list[OcrChar] = []
            for c_el in w_el.xpath(".//*[contains(@class, 'ocrx_cinfo')]"):
                cp = _title(c_el)
                ctext = c_el.text_content()
                if not ctext:
                    continue
                chars.append(OcrChar(ctext, _bbox_of(cp, "x_bboxes"), float((cp.get("x_conf") or ["0"])[0])))
            text = "".join(c.text for c in chars) if chars else w_el.text_content()
            text = text.strip()
            if not text:
                continue
            conf = float((wp.get("x_wconf") or ["0"])[0])
            words.append(OcrWord(text, _bbox_of(wp), conf, chars))
        if not words:
            continue
        bl = props.get("baseline") or ["0", "0"]
        par = line_el.getparent()
        block = par.getparent() if par is not None else None
        lines.append(
            OcrLine(
                bbox=_bbox_of(props),
                words=words,
                baseline=(float(bl[0]), float(bl[1])),
                x_size=float((props.get("x_size") or ["0"])[0]),
                x_descenders=float((props.get("x_descenders") or ["0"])[0]),
                x_ascenders=float((props.get("x_ascenders") or ["0"])[0]),
                par_id=par.get("id", "") if par is not None else "",
                block_id=block.get("id", "") if block is not None else "",
            )
        )
    return lines


def ocr_hocr(img: np.ndarray, lang: str = "por", psm: int = 3, dpi: int = 300, timeout: float = 300) -> list[OcrLine]:
    """OCR da imagem (cinza, texto escuro). ``lang``: "por", "eng" ou "por+eng"."""
    if not re.fullmatch(r"[a-z_]{3,8}(\+[a-z_]{3,8})*", lang):
        raise ValueError("idioma inválido")

    def go(p: str) -> list[OcrLine]:
        out = _run(
            [p, "stdout", "-l", lang, "--psm", str(int(psm)), "--dpi", str(int(dpi)),
             "-c", "hocr_char_boxes=1", "-c", "preserve_interword_spaces=0", "hocr"],
            timeout=timeout,
        )
        return parse_hocr(out)

    return _with_temp_image(img, go)


def detect_orientation(img: np.ndarray) -> tuple[int, float]:
    """Giro necessário (graus horários: 0, 90, 180, 270) e confiança, pelo OSD."""

    def go(p: str) -> tuple[int, float]:
        try:
            out = _run([p, "stdout", "--psm", "0"], timeout=120).decode("utf-8", errors="replace")
        except TesseractError:
            return 0, 0.0
        rot = re.search(r"Rotate:\s*(\d+)", out)
        conf = re.search(r"Orientation confidence:\s*([\d.]+)", out)
        return (int(rot.group(1)) if rot else 0), (float(conf.group(1)) if conf else 0.0)

    return _with_temp_image(img, go)


def ocr_text_simple(img: np.ndarray, lang: str = "por", psm: int = 7) -> tuple[str, float]:
    """Texto e confiança média de um recorte pequeno (célula de tabela, legenda)."""
    lines = ocr_hocr(img, lang=lang, psm=psm)
    words = [w for ln in lines for w in ln.words]
    if not words:
        return "", 0.0
    return " ".join(w.text for w in words), float(np.mean([w.conf for w in words]))

