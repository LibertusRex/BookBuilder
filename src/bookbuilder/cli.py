"""Uso em linha de comando (sem interface), útil para lotes e testes:

    python -m bookbuilder.cli <pasta-de-imagens | livro.pdf> --projeto <pasta.bookbuilder> [--limite N]
           [--docx saida.docx] [--epub saida.epub] [--sem-surya] [--refazer]

    python -m bookbuilder.cli <documento.docx> [--epub saida.epub] [--titulo T] [--autor A] [--capa img]
"""

from __future__ import annotations

import argparse
import sys
import time

from . import project as projmod
from .model import PageStatus
from .pipeline import process_project


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bookbuilder", description="Imagens de páginas → DOCX/EPUB")
    ap.add_argument("pasta")
    ap.add_argument("--projeto", help="pasta do projeto (obrigatória para imagens)")
    ap.add_argument("--titulo", default="")
    ap.add_argument("--autor", default="")
    ap.add_argument("--capa", default="")
    ap.add_argument("--limite", type=int, default=0, help="processar só as N primeiras páginas")
    ap.add_argument("--inicio", type=int, default=0)
    ap.add_argument("--idioma", default="por")
    ap.add_argument("--docx")
    ap.add_argument("--epub")
    ap.add_argument("--processos", type=int, default=0)
    ap.add_argument("--refazer", action="store_true", help="processar de novo as páginas já concluídas")
    ap.add_argument("--sem-surya", action="store_true", help="não reler o texto com o Surya")
    ap.add_argument("--refazer-surya", action="store_true", help="reler com o Surya páginas já relidas")
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    if args.pasta.lower().endswith(".docx"):
        from pathlib import Path

        from .docx_import import docx_to_epub

        out = args.epub or str(Path(args.pasta).with_suffix(".epub"))
        t0 = time.time()
        imp, er = docx_to_epub(args.pasta, out, args.titulo, args.autor, args.capa)
        print(f"EPUB: {out} — {imp.paragraphs} parágrafos, {imp.headings} títulos, {imp.footnotes} notas, "
              f"{er.images} imagens, {imp.tables} tabelas, {er.chapters} capítulos ({time.time() - t0:.0f}s)")
        return 0
    if not args.projeto:
        ap.error("--projeto é obrigatório ao converter uma pasta de imagens ou um PDF")

    store = projmod.ProjectStore(args.projeto)
    proj = store.load() if store.exists() else projmod.new_project(args.pasta)
    proj.settings.language = args.idioma
    proj.settings.use_surya = not args.sem_surya
    if args.limite:
        for i, p in enumerate(proj.pages):
            p.excluded = not (args.inicio <= i < args.inicio + args.limite)
    if args.refazer_surya:
        for p in proj.active_pages():
            p.surya = False
            for b in p.blocks:
                b.surya_html = None
    store.save(proj, pages=proj.pages if args.refazer_surya else None)

    t0 = time.time()
    counts = {"t": 0, "s": 0}

    def on_tess(page):
        counts["t"] += 1
        st = "ok" if page.status == PageStatus.DONE else f"ERRO {page.error}"
        print(f"[tesseract {counts['t']}] {page.source.display_name} {st} ({time.time() - t0:.0f}s)", flush=True)

    def on_surya(page):
        counts["s"] += 1
        print(f"[surya {counts['s']}] {page.source.display_name} ({time.time() - t0:.0f}s)", flush=True)

    summary = process_project(proj, store, workers=args.processos or None, redo=args.refazer,
                              on_tess_page=on_tess, on_surya_page=on_surya, on_status=lambda m: print(m, flush=True))
    print(f"Tesseract: {summary['tesseract']} páginas; Surya: {summary['surya']} páginas"
          + ("" if summary["surya_used"] else f" (Surya não usado: {summary['surya_reason']})")
          + f" — {time.time() - t0:.0f}s")
    store.save(proj, pages=[])

    if args.docx or args.epub:
        from .document import analyze_document
        from .export import docx_writer, epub_writer, report

        analysis = analyze_document(proj, store)
        notes = []
        if args.docx:
            notes += docx_writer.write(proj, store, args.docx).notes
            print("DOCX:", args.docx)
        if args.epub:
            notes += epub_writer.write(proj, store, args.epub).notes
            print("EPUB:", args.epub)
        print(report.text_report(proj, analysis, notes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
