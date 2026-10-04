"""Localização dos componentes empacotados (Tesseract, modelos) e das pastas
de dados do usuário, tanto rodando do código-fonte quanto do executável."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def app_root() -> Path:
    """Pasta que contém ``vendor/``: a raiz do projeto no código-fonte ou a
    pasta do executável gerado pelo PyInstaller."""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        return base
    return Path(__file__).resolve().parents[2]


def vendor_dir() -> Path:
    env = os.environ.get("BOOKBUILDER_VENDOR")
    return Path(env) if env else app_root() / "vendor"


def tesseract_exe() -> Path:
    return vendor_dir() / "tesseract" / "tesseract.exe"


def tessdata_dir() -> Path:
    return vendor_dir() / "tesseract" / "tessdata"


def layout_model() -> Path:
    return vendor_dir() / "models" / "pp_doc_layoutv3.onnx"


def llama_server() -> Path:
    return vendor_dir() / "llama" / "llama-server.exe"


def dictionary_base() -> Path:
    """Dicionário VERO pt_BR (LibreOffice): pt_BR.aff + pt_BR.dic."""
    return vendor_dir() / "dicionario" / "pt_BR"


def surya_model_dir() -> Path:
    """Modelo do Surya: na pasta do programa (instalação de desenvolvimento) ou
    na pasta de dados do usuário (baixado no primeiro uso)."""
    bundled = vendor_dir() / "models" / "surya"
    if (bundled / "surya-2.gguf").is_file():
        return bundled
    return user_data_dir() / "modelos" / "surya"


def user_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    p = Path(base) / "BookBuilder"
    p.mkdir(parents=True, exist_ok=True)
    return p


def missing_components() -> list[str]:
    """Componentes obrigatórios ausentes, em português, para avisar o usuário."""
    missing = []
    if not tesseract_exe().is_file():
        missing.append("Tesseract OCR (vendor/tesseract/tesseract.exe)")
    for lang in ("por", "eng"):
        if not (tessdata_dir() / f"{lang}.traineddata").is_file():
            missing.append(f"Modelo de idioma do Tesseract: {lang}")
    if not layout_model().is_file():
        missing.append("Modelo de análise de layout (vendor/models/pp_doc_layoutv3.onnx)")
    return missing
