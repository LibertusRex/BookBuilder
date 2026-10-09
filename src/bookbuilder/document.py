"""Análise do documento inteiro, depois do OCR de cada página.

Ordem: duplicatas → cabeçalhos/rodapés/números → correções pelo vocabulário
do próprio documento → ruído gráfico → releitura das margens → parágrafos
partidos → notas de rodapé → estilos e fontes → texto contínuo de cada bloco
(hifenização, fusão Tesseract × Surya, árbitro, acertos finais) → chamadas
de nota → títulos → parágrafos que continuam na página seguinte → quadros
que atravessam páginas.

Cada etapa mora no seu módulo (layout.structure, layout.titles,
layout.notes, layout.cleanup, layout.noise, ocr.hybrid); aqui fica só a
sequência.

É rápida e pode ser refeita a qualquer momento (por exemplo depois de uma
correção do usuário); o que o usuário editou ou travou é preservado.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from rapidfuzz import fuzz

from .layout import cleanup, fusion, noise, notes, running, text, titles
from .layout.structure import (FLOW_ROLES, continuations, even_body_paragraphs, merge_split_paragraphs, recompute_alignment,
                               style_alignment, unify_boxes)
from .model import Block, Page, PageStatus, Project, Role
from .ocr import hybrid
from .typography import fonts, styles


@dataclass
class DocReport:
    duplicates: list[str] = field(default_factory=list)
    running_notes: list[str] = field(default_factory=list)
    corrections: int = 0
    symbol_fixes: int = 0
    merged: int = 0
    fusion: object = None  # estatísticas da fusão Tesseract × Surya
    disputes_open: int = 0
    noise: int = 0  # ícones/molduras/telas do leitor retirados
    surya_pages: int = 0
    low_conf_pages: list[str] = field(default_factory=list)
    occluded_pages: list[str] = field(default_factory=list)
    footnotes: int = 0
    footnotes_unlinked: int = 0
    headings: int = 0
    continuations: int = 0
    scale_source: str = ""
    font_notes: list[str] = field(default_factory=list)


def flow_pages(project: Project) -> list[Page]:
    """Páginas que entram na exportação, na ordem escolhida."""
    out = []
    for p in project.pages:
        if p.excluded or p.status != PageStatus.DONE:
            continue
        if p.duplicate_of and project.settings.remove_duplicates:
            continue
        out.append(p)
    return out


def analyze_document(project: Project, store=None, progress: Callable[[str], None] | None = None,
                     sample_loader=None) -> DocReport:
    rep = DocReport()
    say = progress or (lambda m: None)
    done = [p for p in project.pages if not p.excluded and p.status == PageStatus.DONE]

    say("Procurando páginas repetidas…")
    _duplicates(done, rep)
    pages = flow_pages(project)
    noise.cover_page(pages, (lambda p: _save_cover(store, p)) if store is not None else None)

    say("Identificando cabeçalhos, rodapés e números de página…")
    rr = running.classify(pages)
    running.numbers_in_margins(pages)
    rep.running_notes = rr.notes
    project.analysis["page_number_pattern"] = rr.page_number_pattern
    project.analysis["page_number_pos"] = rr.page_number_pos

    say("Corrigindo confusões de caracteres pelo vocabulário do documento…")
    all_lines = [ln for p in pages for b in p.blocks if b.role in FLOW_ROLES or b.role == Role.TITLE for ln in b.lines]
    vocab = text.vocabulary(all_lines)
    rep.symbol_fixes, rep.corrections = cleanup.fix_words(pages, vocab)

    say("Retirando ícones, molduras e telas do leitor lidos como conteúdo…")
    rep.noise = noise.mark(pages, fusion.Lexicon(vocab, fusion.load_dictionary()), noise.figure_text)
    if store is not None:
        noise.figures_from_garbage(pages, lambda p, box: _save_crop(store, p, box))

    hybrid.reread_margins(project, pages, say)

    say("Unindo parágrafos partidos e conferindo o alinhamento…")
    rep.merged = merge_split_paragraphs(pages)
    cleanup.dot_leaders(pages)  # depois de juntar as linhas da mesma altura
    recompute_alignment(pages)

    say("Identificando notas de rodapé…")
    body_xh = notes.body_xh(pages)
    notes.detect_footnotes(pages, body_xh)

    say("Agrupando estilos e estimando fontes…")
    if sample_loader is None:
        from .pipeline import work_gray

        def sample_loader(p: Page):
            return work_gray(p)[0]

    project.styles = styles.build_styles(project, pages, sample_loader, progress=say)
    rep.scale_source = project.analysis.get("scale_source", "")
    titles.fix_stray_styles(pages, project)
    style_alignment(pages)
    # Antes de montar o texto: parágrafo justificado não leva tabulação.
    even_body_paragraphs(pages, next((s.id for s in project.styles.values() if s.name == "Corpo do texto"), None))
    is_bold_f, is_italic_f = styles.word_formatter(project.styles, styles.page_norms(pages))

    say("Reconstruindo parágrafos e hifenização…")
    low = project.settings.low_conf_threshold
    use_fusion = any(b.surya_html for p in pages for b in p.blocks)
    lex = fusion.Lexicon(vocab, fusion.load_dictionary()) if use_fusion else None
    fstats = fusion.Stats()
    tess_cache: dict[str, list] = {}
    if use_fusion:
        say("Combinando as leituras do Tesseract e do Surya…")
    for p in pages:
        for b in p.blocks:
            if b.user_edited or b.role in (Role.FIGURE,):
                continue
            if b.role == Role.TABLE and b.table:
                continue
            b.runs = text.build_runs(b, vocab, is_bold_f(b), is_italic_f(b), low)
            _confidence_flags(b, low)
            b.disputes = []
            if lex is not None and b.surya_html:
                tess_cache[b.id] = b.runs
                hybrid.fuse_into(b, b.runs, lex, fstats)
    if use_fusion and project.settings.use_arbiter:
        hybrid.run_arbiter(project, pages, lex, tess_cache, fstats, say)
    cleanup.drop_repeated_tails(pages)
    cleanup.final_text_fixes(pages, vocab, lex if lex is not None else fusion.Lexicon(vocab, fusion.load_dictionary()))
    rep.fusion = fstats if use_fusion else None
    rep.disputes_open = sum(len(b.disputes) for p in pages for b in p.blocks)
    rep.surya_pages = sum(1 for p in pages if p.surya)

    say("Ligando notas de rodapé às chamadas…")
    notes.link_footnotes(pages, rep)

    say("Definindo níveis de título…")
    titles.assign_headings(pages, project, rep)

    say("Unindo parágrafos que continuam na página seguinte…")
    continuations(pages, vocab, rep)
    unify_boxes(pages)
    cleanup.toc_entries(pages, lex if lex is not None else fusion.Lexicon(vocab, fusion.load_dictionary()))

    for p in pages:
        if p.mean_conf and p.mean_conf < 75 or any("baixa_confianca" in b.flags for b in p.blocks if b.role in FLOW_ROLES):
            rep.low_conf_pages.append(p.source.display_name)
        if "sobreposicao" in p.flags or any(b.occluded for b in p.blocks):
            rep.occluded_pages.append(p.source.display_name)
    for s in project.styles.values():
        if s.family_override and not fonts.is_installed(s.family_override):
            rep.font_notes.append(f"{s.name}: a fonte escolhida “{s.family_override}” não está instalada; será usada “{s.detected_family or s.family}”.")
        elif not s.family_override and s.detected_family:
            rep.font_notes.append(f"{s.name}: fonte original não identificada com certeza; usada a mais parecida instalada, “{s.detected_family}” "
                                  f"({s.category_label()}, semelhança {s.match_score:.0%}), {s.size:g} pt.")
    project.analysis["report"] = {
        "duplicates": rep.duplicates,
        "corrections": rep.corrections,
        "footnotes": rep.footnotes,
        "footnotes_unlinked": rep.footnotes_unlinked,
        "headings": rep.headings,
        "continuations": rep.continuations,
        "running": rep.running_notes,
    }
    if store is not None:
        store.save(project, pages=pages)
    say("Análise concluída.")
    return rep


# ---------------------------------------------------------------------------


def _confidence_flags(b: Block, low: float) -> None:
    """Sinaliza o bloco para revisão só quando há indício real de erro: média
    baixa, várias palavras duvidosas ou texto encoberto."""
    for f in ("baixa_confianca", "ilegivel"):
        if f in b.flags:
            b.flags.remove(f)
    if b.role in (Role.ARTIFACT, Role.FIGURE) or not b.lines:
        return
    if any(w.text == "[trecho ilegível]" for ln in b.lines for w in ln.words):
        b.flags.append("ilegivel")
        return
    confs = [w.conf for ln in b.lines for w in ln.words if w.text != "[…]"]
    if not confs:
        return
    mean = sum(confs) / len(confs)
    n_low = sum(c < low for c in confs)
    if mean < 35:
        b.flags.append("ilegivel")
    elif mean < 75 or n_low >= max(3, len(confs) * 0.06):
        b.flags.append("baixa_confianca")


def _save_crop(store, p: Page, box) -> str:
    """Recorte colorido da imagem de trabalho (mesma geometria dos blocos)."""
    import os

    import cv2

    from .pipeline import work_gray

    _, rgb = work_gray(p)
    x0, y0, x1, y1 = box
    crop = rgb[y0:y1, x0:x1]
    if crop.size == 0:
        return ""
    name = f"{p.uid[:8]}-{x0}-{y0}.png"
    os.makedirs(store.figures_dir, exist_ok=True)
    ok, buf = cv2.imencode(".png", cv2.cvtColor(crop, cv2.COLOR_RGB2BGR))
    if not ok:
        return ""
    with open(os.path.join(store.figures_dir, name), "wb") as f:
        f.write(buf.tobytes())
    return name


def _save_cover(store, p: Page) -> str:
    """Capa: a página inteira, nas cores originais (sem a correção de luz)."""
    import os

    import cv2
    import numpy as np

    from . import imageio

    rgb = np.asarray(imageio.load_rgb(p.source))
    h, w = rgb.shape[:2]
    if max(h, w) > 2400:
        f = 2400 / max(h, w)
        rgb = cv2.resize(rgb, (round(w * f), round(h * f)), interpolation=cv2.INTER_AREA)
    name = f"{p.uid[:8]}-capa.jpg"
    os.makedirs(store.figures_dir, exist_ok=True)
    ok, buf = cv2.imencode(".jpg", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not ok:
        return ""
    with open(os.path.join(store.figures_dir, name), "wb") as f:
        f.write(buf.tobytes())
    return name


def _page_text(p: Page) -> str:
    return " ".join(b.text or " ".join(ln.text for ln in b.lines) for b in p.ordered_blocks()
                    if b.role in FLOW_ROLES or b.role == Role.TITLE)


def _duplicates(pages: list[Page], rep: DocReport) -> None:
    prev: Page | None = None
    prev_text = ""
    for p in pages:
        p.duplicate_of = None
        t = _page_text(p)
        if "manter_duplicata" in p.flags:
            prev, prev_text = p, t
            continue
        if prev is not None and len(t) > 80 and len(prev_text) > 80:
            same_num = (p.printed_number is None or prev.printed_number is None or p.printed_number == prev.printed_number)
            if same_num and fuzz.ratio(t, prev_text) >= 97:
                p.duplicate_of = prev.uid
                rep.duplicates.append(f"{p.source.display_name} repete {prev.source.display_name}")
                continue
        prev, prev_text = p, t
