"""Fontes instaladas no Windows e comparação visual com trechos da imagem.

Para cada estilo do documento, palavras de alta confiança são recortadas da
página e comparadas com as mesmas palavras desenhadas em cada fonte
instalada, na mesma altura de x. A fonte com formas e proporções mais
parecidas é sugerida. É uma estimativa: fontes comerciais de livros quase
nunca estão instaladas, então o resultado é "a mais parecida disponível", e
isso é informado como substituição.
"""

from __future__ import annotations

import json
import logging
import os
import statistics
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .. import paths

log = logging.getLogger(__name__)

SKIP = (
    "wingdings", "webdings", "symbol", "marlett", "mt extra", "mdl2", "fluent icons", "holomdl",
    "emoji", "dingbat", "bookshelf", "ms reference specialty", "segoe ui symbol", "segoe ui historic",
    "gadugi", "ebrima", "nirmala", "javanese", "mongolian", "myanmar", "sylfaen", "yi baiti",
    "phagspa", "tai le", "new tai lue", "leelawadee", "malgun", "meiryo", "yu gothic", "yu mincho",
    "ms gothic", "ms mincho", "simsun", "nsimsun", "simhei", "microsoft yahei", "microsoft jhenghei",
    "mingliu", "pmingliu", "batang", "gulim", "dotum", "gungsuh", "kaiti", "fangsong", "dengxian",
    "mv boli", "sitka", "cascadia code pl", "cascadia mono pl", "noto sans symbols",
)

# Fontes decorativas, manuscritas ou de títulos chamativos: nunca usadas como
# estimativa (dariam semelhanças enganosas em trechos pequenos).
DECORATIVE = (
    "algerian", "bauhaus", "bodoni mt black", "bodoni mt poster", "britannic", "broadway", "brush script",
    "castellar", "chiller", "colonna", "cooper black", "curlz", "elephant", "engravers", "forte",
    "freestyle", "french script", "gigi", "goudy stout", "harlow", "harrington", "impact", "imprint mt shadow",
    "informal roman", "jokerman", "juice itc", "kristen", "kunstler", "magneto", "matura", "mistral",
    "niagara", "old english", "onyx", "palace script", "papyrus", "parchment", "playbill", "poor richard",
    "ravie", "rage italic", "snap itc", "showcard", "stencil", "tempus sans", "viner hand", "vivaldi",
    "vladimir", "wide latin", "gill sans ultra", "ink free", "segoe print", "segoe script", "comic sans",
    "edwardian", "blackadder", "bradley hand", "felix titling", "lucida calligraphy", "lucida handwriting",
    "pristina", "script mt", "buxton sketch", "sketchflow", "gabriola", "berlin sans", "agency fb",
    "cooper", "copperplate", "desdemona", "eras ", "gloucester", "haettenschweiler", "jokerman",
    "modern no. 20", "ocr a", "ocr-b", "perpetua titling", "rockwell extra", "rockwell condensed",
    "tw cen mt condensed extra", "footlight", "goudy old style", "bernard mt", "showcard gothic", "marlett",
    "holomdl", "candara light", "hp simplified", "franklin gothic heavy", "franklin gothic demi cond",
    "small fonts", "modern", "roman", "script", "courier",
)
# Alfabetos não latinos (não servem para texto em português/inglês).
NON_LATIN = (
    "arabic", "naskh", "kufi", "hebrew", "georgian", "armenian", "devanagari", "bengali", "gurmukhi",
    "gujarati", "oriya", "tamil", "telugu", "kannada", "malayalam", "sinhala", "thai", "lao", "khmer",
    "myanmar", "tibetan", "ethiopic", "cherokee", "mongolian", "hangul", "cjk", "jp", " sc", " tc", " kr",
    "syriac", "thaana", "nko", "tifinagh", "vai", "yi ", "symbols", "math", "music", "emoji", "javanese",
    "balinese", "sundanese", "batak", "buginese", "canadian", "ogham", "runic", "coptic", "gothic ",
    "old ", "linear", "cuneiform", "egyptian", "hieroglyph", "adlam", "osage", "ol chiki", "lisu",
)


def usable_for_text(family: str) -> bool:
    k = family.casefold()
    if any(d in k for d in DECORATIVE):
        return False
    if k.startswith("noto") and any(s in k for s in NON_LATIN):
        return False
    if any(s in k for s in ("arabic", "naskh", "hebrew", "georgian", "armenian", "devanagari", "symbol", "emoji")):
        return False
    return True


KNOWN_CATEGORY = {
    # serifadas
    "georgia": "serif", "times new roman": "serif", "cambria": "serif", "constantia": "serif",
    "palatino linotype": "serif", "book antiqua": "serif", "garamond": "serif", "century": "serif",
    "century schoolbook": "serif", "bookman old style": "serif", "baskerville old face": "serif",
    "bodoni mt": "serif", "californian fb": "serif", "calisto mt": "serif", "goudy old style": "serif",
    "perpetua": "serif", "rockwell": "serif", "sylfaen": "serif", "high tower text": "serif",
    "centaur": "serif", "bell mt": "serif", "footlight mt light": "serif", "sitka text": "serif",
    "sitka small": "serif", "lucida bright": "serif", "dubai": "sans",
    # sem serifa
    "arial": "sans", "calibri": "sans", "segoe ui": "sans", "verdana": "sans", "tahoma": "sans",
    "trebuchet ms": "sans", "candara": "sans", "corbel": "sans", "bahnschrift": "sans",
    "aptos": "sans", "gill sans mt": "sans", "franklin gothic medium": "sans", "century gothic": "sans",
    "lucida sans": "sans", "lucida sans unicode": "sans", "arial narrow": "sans", "microsoft sans serif": "sans",
    "tw cen mt": "sans", "selawik": "sans",
    # monoespaçadas
    "consolas": "mono", "courier new": "mono", "lucida console": "mono", "cascadia code": "mono",
    "cascadia mono": "mono", "lucida sans typewriter": "mono", "ocr a extended": "mono",
}

REGULAR_STYLES = {"regular", "normal", "book", "roman", "medium", "text"}


@dataclass
class FontFace:
    family: str
    style: str
    path: str
    xratio: float  # altura de x / tamanho do corpo (em)
    capratio: float
    category: str


def font_dirs() -> list[Path]:
    dirs = [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        dirs.append(Path(local) / "Microsoft" / "Windows" / "Fonts")
    return [d for d in dirs if d.is_dir()]


def _measure(path: str) -> tuple[str, str, float, float] | None:
    try:
        f = ImageFont.truetype(path, 200)
        fam, sty = f.getname()
    except Exception:
        return None
    try:
        xb = f.getbbox("x")
        hb = f.getbbox("H")
    except Exception:
        return None
    xr = (xb[3] - xb[1]) / 200.0
    cr = (hb[3] - hb[1]) / 200.0
    if xr <= 0.2 or xr > 0.8:
        return None
    return fam, sty, xr, cr


def _category(path: str, family: str) -> str:
    key = family.casefold()
    for name, cat in KNOWN_CATEGORY.items():
        if key == name or key.startswith(name + " "):
            return cat
    try:
        f = ImageFont.truetype(path, 100)
        wi = f.getlength("iiii")
        wm = f.getlength("MMMM")
        if wm > 0 and abs(wi - wm) / wm < 0.05:
            return "mono"
        return "serif" if _has_serifs(f) else "sans"
    except Exception:
        return "sans"


def _has_serifs(font: ImageFont.FreeTypeFont) -> bool:
    img = Image.new("L", (120, 140), 0)
    ImageDraw.Draw(img).text((30, 10), "l", font=font, fill=255)
    return serif_score(np.asarray(img)) > 1.6


def serif_score(glyph: np.ndarray) -> float:
    """Largura do pé da letra / largura da haste no meio. Serifas alargam o pé."""
    m = glyph > 127
    rows = np.where(m.any(axis=1))[0]
    if rows.size < 10:
        return 1.0
    top, bot = rows[0], rows[-1]
    mid = m[(top + bot) // 2]
    foot = m[bot - max(1, (bot - top) // 20)]
    mw = mid.sum()
    return float(foot.sum()) / mw if mw else 1.0


def _cache_file() -> Path:
    return paths.user_data_dir() / "fontes.json"


def _dirs_signature() -> str:
    sig = []
    for d in font_dirs():
        try:
            sig.append(f"{d}:{int(d.stat().st_mtime)}:{len(os.listdir(d))}")
        except OSError:
            pass
    return "|".join(sig)


@lru_cache(maxsize=1)
def installed_faces() -> list[FontFace]:
    sig = _dirs_signature()
    cf = _cache_file()
    if cf.is_file():
        try:
            data = json.loads(cf.read_text("utf-8"))
            if data.get("sig") == sig:
                return [FontFace(**x) for x in data["faces"]]
        except Exception:
            pass
    faces: list[FontFace] = []
    for d in font_dirs():
        for f in d.iterdir():
            if f.suffix.lower() not in (".ttf", ".otf", ".ttc"):
                continue
            m = _measure(str(f))
            if not m:
                continue
            fam, sty, xr, cr = m
            if any(s in fam.casefold() for s in SKIP):
                continue
            faces.append(FontFace(fam, sty, str(f), xr, cr, _category(str(f), fam)))
    try:
        cf.write_text(json.dumps({"sig": sig, "faces": [asdict(x) for x in faces]}, ensure_ascii=False), "utf-8")
    except OSError:
        pass
    return faces


def families() -> list[str]:
    return sorted({f.family for f in installed_faces()}, key=str.casefold)


def is_installed(family: str) -> bool:
    k = family.casefold()
    return any(f.family.casefold() == k for f in installed_faces())


def regular_face(family: str) -> FontFace | None:
    k = family.casefold()
    cands = [f for f in installed_faces() if f.family.casefold() == k]
    for f in cands:
        if f.style.casefold() in REGULAR_STYLES:
            return f
    return cands[0] if cands else None


def xratio(family: str, default: float = 0.47) -> float:
    f = regular_face(family)
    return f.xratio if f else default


# ---------------------------------------------------------------------------
# Comparação visual


@dataclass
class Sample:
    text: str
    image: np.ndarray  # binária, tinta = 255, recorte justo da palavra
    xheight: float


def _render(face: FontFace, text: str, xheight: float) -> np.ndarray | None:
    size = max(6, int(round(xheight / face.xratio)))
    try:
        font = _font(face.path, size)
    except Exception:
        return None
    try:
        box = font.getbbox(text)
    except Exception:
        return None
    w, h = box[2] - box[0] + 4, box[3] - box[1] + 4
    if w <= 4 or h <= 4 or w > 4000:
        return None
    img = Image.new("L", (w, h), 0)
    ImageDraw.Draw(img).text((2 - box[0], 2 - box[1]), text, font=font, fill=255)
    arr = np.asarray(img)
    ys, xs = np.where(arr > 127)
    if ys.size == 0:
        return None
    return arr[ys.min():ys.max() + 1, xs.min():xs.max() + 1]


@lru_cache(maxsize=512)
def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def _similarity(sample: np.ndarray, rendered: np.ndarray) -> float:
    sh, sw = sample.shape
    rh, rw = rendered.shape
    if sw < 4 or rw < 4:
        return 0.0
    width_ratio = (rw / rh) / (sw / sh)
    shape_pen = float(np.exp(-abs(np.log(width_ratio)) * 5.0))
    r = cv2.resize(rendered, (sw, sh), interpolation=cv2.INTER_AREA)
    a = (sample > 127).astype(np.float32)
    b = (r > 127).astype(np.float32)
    # Tolerância de um pixel: compara cada imagem com a outra dilatada.
    k = np.ones((3, 3), np.uint8)
    da = cv2.dilate(a, k)
    db = cv2.dilate(b, k)
    prec = float((b * da).sum() / max(1.0, b.sum()))
    rec = float((a * db).sum() / max(1.0, a.sum()))
    f1 = 2 * prec * rec / max(1e-6, prec + rec)
    ink = float(b.sum() / max(1.0, a.sum()))  # massa de tinta (peso do traço)
    weight_pen = float(np.exp(-abs(np.log(max(ink, 1e-3))) * 1.5))
    return f1 * shape_pen * weight_pen


def match(samples: list[Sample], want_bold: bool = False, want_italic: bool = False,
          limit: int = 5) -> list[tuple[str, float, str]]:
    """Famílias mais parecidas: (família, pontuação 0–1, categoria)."""
    faces = installed_faces()
    if not samples or not faces:
        return []
    by_family: dict[str, list[FontFace]] = {}
    for f in faces:
        if usable_for_text(f.family):
            by_family.setdefault(f.family, []).append(f)
    scores: list[tuple[str, float, str]] = []
    for fam, group in by_family.items():
        face = _pick_style(group, want_bold, want_italic)
        vals = []
        for s in samples:
            r = _render(face, s.text, s.xheight)
            if r is None:
                continue
            vals.append(_similarity(s.image, r))
        if len(vals) >= max(1, len(samples) // 2):
            scores.append((fam, statistics.mean(vals), face.category))
    scores.sort(key=lambda x: -x[1])
    return scores[:limit]


def _pick_style(group: list[FontFace], bold: bool, italic: bool) -> FontFace:
    def score(f: FontFace) -> int:
        s = f.style.casefold()
        b = "bold" in s or "black" in s or "heavy" in s or "semibold" in s
        i = "italic" in s or "oblique" in s
        val = (2 if b == bold else 0) + (2 if i == italic else 0)
        if not bold and not italic and s in REGULAR_STYLES:
            val += 1
        return val

    return max(group, key=score)

