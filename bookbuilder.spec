# -*- mode: python ; coding: utf-8 -*-
# Configuração do PyInstaller: gera dist\BookBuilder\BookBuilder.exe (pasta
# completa, com Python, Qt, Tesseract e os modelos — funciona sem internet).
#
#   uv run pyinstaller bookbuilder.spec --noconfirm

import os
from pathlib import Path

import sys

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

ROOT = Path(SPECPATH)
VENDOR = ROOT / "vendor"
KEEP_LLAMA = {"llama-server.exe", "LICENSE-LLVM-OpenMP"}
STRIPPED = ROOT / "build" / "stripped"  # cópias sem informação de depuração

sys.path.insert(0, str(ROOT / "scripts"))
from strip_pe import strip_debug  # noqa: E402


def lean(path: Path) -> Path:
    """DLL/EXE do MinGW sem a depuração (libtesseract: 97 MB → 3 MB)."""
    if path.suffix.lower() not in (".dll", ".exe"):
        return path
    out = STRIPPED / path.relative_to(VENDOR)
    if not out.exists() or out.stat().st_mtime < path.stat().st_mtime:
        out.parent.mkdir(parents=True, exist_ok=True)
        strip_debug(path, out)
    return out

# Componentes de OCR: Tesseract (sem ferramentas de treino nem documentação) e modelos.
datas = []
skip_parts = {"doc", "$PLUGINSDIR"}
for path in (VENDOR / "tesseract").rglob("*"):
    if path.is_dir() or any(p in skip_parts for p in path.parts):
        continue
    name = path.name.lower()
    if name.endswith((".jar", ".html")) or (name.endswith(".exe") and name != "tesseract.exe"):
        continue
    rel = path.relative_to(ROOT).parent
    datas.append((str(lean(path)), str(rel)))
datas.append((str(VENDOR / "models" / "pp_doc_layoutv3.onnx"), "vendor/models"))
# llama.cpp (Vulkan) para o Surya; o modelo do Surya NÃO vai no pacote (download opcional no 1º uso).
for path in (VENDOR / "llama").glob("*"):
    if path.is_file() and (path.suffix.lower() == ".dll" or path.name in KEEP_LLAMA):
        datas.append((str(path), "vendor/llama"))
for name in ("pt_BR.aff", "pt_BR.dic", "README_pt_BR.txt"):
    datas.append((str(VENDOR / "dicionario" / name), "vendor/dicionario"))
datas += collect_data_files("rapid_layout")  # configurações YAML do detector
datas += collect_data_files("spylls")  # verificador ortográfico (dados internos)

binaries = collect_dynamic_libs("onnxruntime")

a = Analysis(
    [str(ROOT / "src" / "bookbuilder" / "__main__.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=["bookbuilder.app", "rapid_layout"],
    excludes=["tkinter", "matplotlib", "IPython", "pytest", "PySide6.QtWebEngineCore", "PySide6.Qt3DCore",
              "PySide6.QtMultimedia", "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtPdf"],
    noarchive=False,
)
# Enxugamento:
# * as DLLs do Tesseract e do llama.cpp já vão em vendor/; a análise de
#   dependências do PyInstaller as copiava também para a raiz (177 MB a mais);
# * partes do Qt e do OpenCV que o programa não usa (QML/Quick, PDF, OpenGL por
#   software, vídeo/ffmpeg) e traduções do Qt que não sejam de português.
vendor_names = {f.name.lower() for d in ("tesseract", "llama") for f in (VENDOR / d).glob("*.dll")}
UNUSED = ("opengl32sw.dll", "qt6quick", "qt6qml", "qt6pdf", "qt6virtualkeyboard", "opencv_videoio_ffmpeg")


def wanted(dest: str) -> bool:
    d = dest.replace("\\", "/").lower()
    name = d.rsplit("/", 1)[-1]
    if "/" not in d and name in vendor_names:
        return False
    if any(u in name for u in UNUSED):
        return False
    if "pyside6/translations/" in d and not ("_pt" in name):
        return False
    return True


a.binaries = [t for t in a.binaries if wanted(t[0])]
a.datas = [t for t in a.datas if wanted(t[0])]

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BookBuilder",
    console=False,
    icon=str(ROOT / "assets" / "bookbuilder.ico") if (ROOT / "assets" / "bookbuilder.ico").exists() else None,
    version=None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="BookBuilder")
