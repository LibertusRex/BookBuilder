"""Leitura híbrida Tesseract × Surya no nível do documento: fusão de cada
bloco, árbitro por recorte e releitura do texto repetido das margens."""

from __future__ import annotations

import re
from collections import Counter

from ..layout import fusion
from ..model import Block, Page, Project, Role

def _margin_text(b: Block) -> str:
    return " ".join(ln.text for ln in b.lines).strip()


def reread_margins(project: Project, pages: list[Page], say) -> None:
    """Relê com o Surya o texto repetido de cabeçalhos e rodapés (ex.: "22º"
    × "22ª"). Cada texto distinto é lido em até três páginas; a leitura da
    maioria vale para todas as ocorrências, que depois passam pela fusão
    como os demais blocos."""
    from collections import defaultdict

    groups: dict[str, list[tuple[Page, Block]]] = defaultdict(list)
    for p in pages:
        for b in p.blocks:
            if b.role in (Role.HEADER, Role.FOOTER) and b.lines and not b.surya_html and not b.user_edited:
                t = _margin_text(b)
                if t and not re.fullmatch(r"[\d\s%]+", t):  # progresso de leitura ("61%") muda a cada página
                    groups[t].append((p, b))
    if not groups or not project.settings.use_surya:
        return
    from . import surya
    from ..pipeline import work_gray

    if not surya.status().available:
        return
    say(f"Relendo cabeçalhos e rodapés com o Surya ({len(groups)} textos distintos)…")
    server = surya.Server(parallel=4)
    try:
        server.start()
    except Exception:  # noqa: BLE001 - segue só com o Tesseract
        return
    try:
        for t, items in groups.items():
            step = max(1, len(items) // 3)
            readings = []
            for p, b in items[::step][:3]:
                try:
                    gray = work_gray(p)[0]
                    x0 = min(ln.bbox[0] for ln in b.lines)
                    y0 = min(ln.bbox[1] for ln in b.lines)
                    x1 = max(ln.bbox[2] for ln in b.lines)
                    y1 = max(ln.bbox[3] for ln in b.lines)
                    h, w = gray.shape[:2]
                    crop = gray[max(0, y0 - 6):min(h, y1 + 6), max(0, x0 - 6):min(w, x1 + 6)]
                    html = surya.ocr_block(server.url, crop, len(t) + 20)
                except Exception:  # noqa: BLE001
                    continue
                if html and fusion.html_to_text(html).strip():
                    readings.append(html)
            if not readings:
                continue
            votes = Counter(" ".join(fusion.html_to_text(h).split()) for h in readings)
            best_text = votes.most_common(1)[0][0]
            best = next(h for h in readings if " ".join(fusion.html_to_text(h).split()) == best_text)
            for _, b in items:
                b.surya_html = best
    finally:
        server.stop()


def fuse_into(b: Block, tess_runs, lex, fstats) -> None:
    """Funde as leituras do bloco (com as decisões já tomadas) e registra as
    divergências abertas com a posição na imagem."""
    res = fusion.fuse_block(tess_runs, b.surya_html, lex, fstats, b.choices)
    b.runs = res.runs
    words = [w for ln in b.lines for w in ln.words]
    tb = fusion.token_boxes(res.tess_tokens, words)
    b.disputes = []
    for d in res.disputes:
        boxes = [tb[i] for i in d.ti if i < len(tb) and tb[i]]
        bbox = (min(x[0] for x in boxes), min(x[1] for x in boxes), max(x[2] for x in boxes),
                max(x[3] for x in boxes)) if boxes else None
        b.disputes.append(d.to_dict(bbox))
    has_low = any(r.low_conf for r in b.runs)
    if not has_low and "baixa_confianca" in b.flags:
        b.flags.remove("baixa_confianca")
    elif has_low and "baixa_confianca" not in b.flags:
        b.flags.append("baixa_confianca")


def run_arbiter(project: Project, pages: list[Page], lex, tess_cache: dict, fstats, say) -> None:
    """Terceira opinião (recorte ampliado) para as divergências ainda não
    arbitradas. As decisões ficam gravadas em ``block.choices``."""
    from . import arbiter, surya
    from ..pipeline import work_gray

    todo = [(p, b, d) for p in pages for b in p.blocks for d in b.disputes
            if d.get("bbox") and (d["tess"] + fusion.SEP_KEY + d["surya"]) not in b.choices]
    if not todo:
        return
    say(f"Árbitro por recorte: preparando {len(todo)} divergências…")
    jobs = []
    by_page: dict[str, list] = {}
    for p, b, d in todo:
        by_page.setdefault(p.uid, []).append((p, b, d))
    for items in by_page.values():
        try:
            gray = work_gray(items[0][0])[0]
        except Exception:  # noqa: BLE001
            continue
        for p, b, d in items:
            crop = arbiter.make_crop(gray, d["bbox"])
            if crop is not None:
                jobs.append(arbiter.Job(p.uid, b.id, d, crop))
    server = None
    st = surya.status()
    if project.settings.use_surya and st.available:
        server = surya.Server(parallel=8)
        try:
            server.start()
        except Exception:  # noqa: BLE001 - segue só com o Tesseract
            server = None
    try:
        results = arbiter.arbitrate(jobs, server.url if server else None, project.settings.language,
                                    progress=lambda k, n: k % 25 == 0 and say(f"Árbitro por recorte: {k} de {n}…"))
    finally:
        if server is not None:
            server.stop()
    blocks = {(p.uid, b.id): b for p in pages for b in p.blocks}
    touched = set()
    for (puid, bid, k), choice in results.items():
        b = blocks.get((puid, bid))
        if b is None:
            continue
        b.choices[k] = choice if choice else "?"  # "?" = árbitro empatou: fica para a revisão
        touched.add((puid, bid))
    for key_ in touched:
        b = blocks[key_]
        if b.id in tess_cache:
            fuse_into(b, tess_cache[b.id], lex, fusion.Stats())
    fstats.arbiter += sum(1 for c in results.values() if c)
    fstats.flagged -= sum(1 for c in results.values() if c)
