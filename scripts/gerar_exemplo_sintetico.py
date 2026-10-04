"""Gera o livro sintético de testes, processa e exporta DOCX/EPUB em build/.

Uso: python scripts/gerar_exemplo_sintetico.py
"""

import dataclasses
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))


def main() -> None:
    from bookbuilder import project as pm
    from bookbuilder.document import analyze_document
    from bookbuilder.export import docx_writer, epub_writer
    from bookbuilder.model import Page
    from bookbuilder.pipeline import PageJob, Runner
    from tests.synth import make_book

    out = ROOT / "build"
    make_book(out / "sintetico")
    proj = pm.new_project(str(out / "sintetico"))
    store = pm.ProjectStore(out / "sintetico_proj")
    store.save(proj)
    by = {p.uid: i for i, p in enumerate(proj.pages)}

    def on(d):
        pg = Page.from_dict(d)
        proj.pages[by[pg.uid]] = pg

    Runner(4).run([PageJob(p.to_dict(), dataclasses.asdict(proj.settings), str(store.figures_dir)) for p in proj.pages], on)
    analyze_document(proj, store)
    proj.export.title = "Livro de testes"
    proj.export.author = "BookBuilder"
    docx_writer.write(proj, store, str(out / "sintetico.docx"))
    epub_writer.write(proj, store, str(out / "sintetico.epub"))
    print("gerados:", out / "sintetico.docx", out / "sintetico.epub")


if __name__ == "__main__":
    main()
