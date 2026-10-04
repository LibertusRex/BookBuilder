"""Ponto de entrada da aplicação gráfica.

``BookBuilder.exe --autoteste <pasta-de-imagens> <pasta-de-saida>`` processa a
pasta sem interface (OCR em processos paralelos, layout, análise e exportação)
e grava ``autoteste.txt`` na saída: serve para validar o executável gerado.
"""

from __future__ import annotations

import logging
import multiprocessing
import sys


def _autotest(src: str, out: str) -> int:
    import dataclasses
    import traceback
    from pathlib import Path

    outp = Path(out)
    outp.mkdir(parents=True, exist_ok=True)
    log = outp / "autoteste.txt"
    try:
        from . import paths, validate
        from . import project as pm
        from .document import analyze_document
        from .export import docx_writer, epub_writer
        from .model import Page, PageStatus
        from .pipeline import PageJob, Runner

        missing = paths.missing_components()
        if missing:
            raise RuntimeError("componentes ausentes: " + ", ".join(missing))
        proj = pm.new_project(src)
        store = pm.ProjectStore(outp / "autoteste")
        store.save(proj)
        by = {p.uid: i for i, p in enumerate(proj.pages)}

        def on(d):
            pg = Page.from_dict(d)
            proj.pages[by[pg.uid]] = pg

        Runner(2).run([PageJob(p.to_dict(), dataclasses.asdict(proj.settings), str(store.figures_dir))
                       for p in proj.pages], on)
        bad = [p.error for p in proj.pages if p.status != PageStatus.DONE]
        if bad:
            raise RuntimeError("páginas com falha: " + "; ".join(bad))
        analyze_document(proj, store)
        docx_writer.write(proj, store, str(outp / "autoteste.docx"))
        epub_writer.write(proj, store, str(outp / "autoteste.epub"))
        c1 = validate.check_docx(str(outp / "autoteste.docx"), [])
        c2 = validate.check_epub(str(outp / "autoteste.epub"), [])
        ok = c1.ok and c2.ok
        from .layout.fusion import load_dictionary
        from .ocr import surya

        st = surya.status()
        extra = [f"surya: {'disponível' if st.available else 'indisponível'} ({st.reason})",
                 f"dicionário pt-BR: {'ok' if load_dictionary() is not None else 'ausente'}"]
        log.write_text(("OK" if ok else "FALHA") + f"\npáginas: {len(proj.pages)}\n"
                       + "\n".join(c1.messages + c2.messages + extra), encoding="utf-8")
        return 0 if ok else 2
    except Exception:  # noqa: BLE001
        log.write_text("ERRO\n" + traceback.format_exc(), encoding="utf-8")
        return 1


def main() -> int:
    multiprocessing.freeze_support()  # necessário no executável (processos de OCR)
    if len(sys.argv) >= 4 and sys.argv[1] == "--autoteste":
        return _autotest(sys.argv[2], sys.argv[3])

    from PySide6.QtWidgets import QApplication

    from . import paths
    from .ui.main_window import MainWindow

    logging.basicConfig(
        filename=str(paths.user_data_dir() / "bookbuilder.log"),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    app = QApplication(sys.argv)
    app.setApplicationName("BookBuilder")
    app.setOrganizationName("BookBuilder")
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
