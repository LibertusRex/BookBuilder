"""Salvamento e abertura de projetos.

Um projeto é uma pasta ``<nome>.bookbuilder`` com:

* ``projeto.json`` — configurações, ordem das páginas, estilos, metadados;
* ``paginas/<uid>.json`` — resultado de cada página (blocos, linhas, palavras);
* ``figuras/`` — recortes de imagens e tabelas incertas.

Cada página é gravada assim que termina de ser processada, de forma atômica
(arquivo temporário + troca), para que uma interrupção não corrompa nada e o
processamento possa ser retomado sem repetir páginas concluídas.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from . import imageio
from .model import FORMAT_VERSION, Page, PageStatus, Project

PROJECT_FILE = "projeto.json"
PAGES_DIR = "paginas"
FIGURES_DIR = "figuras"
SUFFIX = ".bookbuilder"


def _atomic_write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class ProjectStore:
    def __init__(self, folder: str | os.PathLike):
        folder = Path(folder)
        if folder.suffix != SUFFIX:
            folder = folder.with_name(folder.name + SUFFIX)
        self.folder = folder

    @property
    def figures_dir(self) -> Path:
        return self.folder / FIGURES_DIR

    def page_file(self, uid: str) -> Path:
        return self.folder / PAGES_DIR / f"{uid}.json"

    def exists(self) -> bool:
        return (self.folder / PROJECT_FILE).is_file()

    # -- gravação ----------------------------------------------------------

    def save(self, project: Project, pages: list[Page] | None = None) -> None:
        """Grava o projeto. ``pages``: páginas cujo resultado mudou (``None`` =
        todas as processadas)."""
        self.folder.mkdir(parents=True, exist_ok=True)
        for p in (pages if pages is not None else project.pages):
            self.save_page(p)
        _atomic_write(self.folder / PROJECT_FILE,
                      json.dumps(project.to_dict(include_blocks=False), ensure_ascii=False, indent=1))

    def save_page(self, page: Page) -> None:
        if page.status == PageStatus.PENDING and not page.blocks:
            f = self.page_file(page.uid)
            if f.exists():
                f.unlink()
            return
        _atomic_write(self.page_file(page.uid), json.dumps(page.to_dict(), ensure_ascii=False, separators=(",", ":")))

    # -- leitura -----------------------------------------------------------

    def load(self) -> Project:
        data = json.loads((self.folder / PROJECT_FILE).read_text("utf-8"))
        if data.get("version", 0) > FORMAT_VERSION:
            raise ValueError("Este projeto foi criado por uma versão mais nova do BookBuilder.")
        project = Project.from_dict(data)
        for i, page in enumerate(project.pages):
            f = self.page_file(page.uid)
            if f.is_file():
                try:
                    full = Page.from_dict(json.loads(f.read_text("utf-8")))
                except (ValueError, KeyError):
                    full = None
                if full is not None:
                    # Ordem/giro/exclusão vêm do projeto; o resultado vem da página.
                    full.rotation = page.rotation
                    full.excluded = page.excluded
                    project.pages[i] = full
            elif page.status == PageStatus.DONE:
                page.status = PageStatus.PENDING  # cache perdido: será reprocessada
        return project


def needs_processing(page: Page) -> bool:
    """Página ainda não processada, com erro, ou cuja imagem mudou."""
    if page.excluded:
        return False
    if page.status != PageStatus.DONE:
        return True
    return imageio.source_changed(page.source)


def new_project(folder: str) -> Project:
    """``folder``: pasta de imagens ou um arquivo PDF."""
    pages = imageio.pages_from_folder(folder)
    proj = Project(source_folder=str(folder), pages=pages)
    src = Path(folder)
    base = (src.stem if src.is_file() else src.name) or "documento"
    proj.export.base_name = base
    proj.export.title = base
    proj.export.output_dir = str(src.parent if src.is_file() else src)
    return proj
