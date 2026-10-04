"""Prévia do resultado de uma página em HTML (para o QTextBrowser), com as
fontes e tamanhos estimados de cada estilo. Cabeçalhos e rodapés aparecem
separados e acinzentados, para deixar claro que não fazem parte do corpo."""

from __future__ import annotations

import html

from ..model import Block, Page, Project, Role, Run, Style

FLOW = (Role.BODY, Role.LIST_ITEM, Role.QUOTE)


def _runs(runs: list[Run]) -> str:
    out = []
    for r in runs:
        t = html.escape(r.text).replace("\t", "&emsp;").replace("\n", "<br>")
        if r.note_ref:
            t = f"<sup><a href='nota:{html.escape(r.note_ref)}'>{t}</a></sup>"
        if r.superscript and not r.note_ref:
            t = f"<sup>{t}</sup>"
        if r.subscript:
            t = f"<sub>{t}</sub>"
        if r.underline:
            t = f"<u>{t}</u>"
        if r.italic:
            t = f"<i>{t}</i>"
        if r.bold:
            t = f"<b>{t}</b>"
        if r.low_conf:
            t = f"<span style='background-color:#fff3a0'>{t}</span>"
        out.append(t)
    return "".join(out)


def _font_css(st: Style | None, scale: float) -> str:
    if st is None:
        return ""
    css = [f"font-family:'{st.family}'", f"font-size:{st.size * scale:.1f}pt"]
    if st.bold:
        css.append("font-weight:bold")
    if st.italic:
        css.append("font-style:italic")
    return ";".join(css)


def block_html(b: Block, project: Project, selected: bool, scale: float = 1.0) -> str:
    st = project.styles.get(b.style_id or "")
    css = [_font_css(st, scale)]
    align = {"justify": "justify", "center": "center", "right": "right"}.get(b.align, "left")
    css.append(f"text-align:{align}")
    ppp = float(project.analysis.get("px_per_pt") or 4.0)
    if b.indent_first and b.role in FLOW:
        css.append(f"text-indent:{b.indent_first / ppp * scale:.0f}pt")
    if b.box_id:
        css.append("background-color:#f1f3f6")
    if selected:
        css.append("background-color:#dbe9ff")
    text = _runs(b.runs) if b.runs else html.escape(" ".join(ln.text for ln in b.lines))
    anchor = f"<a name='{b.id}'></a>"
    style = ";".join(c for c in css if c)
    if b.role == Role.TITLE:
        lvl = min(6, b.heading_level or 2)
        return f"{anchor}<h{lvl} style=\"{style};margin-top:8px\">{text}</h{lvl}>"
    if b.role == Role.FIGURE:
        alt = html.escape(b.text[:120]) if b.text else ""
        return f"{anchor}<p style='color:#557;text-align:center'>[imagem{': ' + alt if alt else ''}]</p>"
    if b.role == Role.TABLE:
        if b.table:
            rows = max(c.row for c in b.table) + 1
            cols = max(c.col for c in b.table) + 1
            grid = [["" for _ in range(cols)] for _ in range(rows)]
            for c in b.table:
                grid[c.row][c.col] = html.escape(c.text)
            body = "".join("<tr>" + "".join(f"<td>{x}</td>" for x in r) + "</tr>" for r in grid)
            return f"{anchor}<table border='1' cellspacing='0' cellpadding='3'>{body}</table>"
        return f"{anchor}<p style='color:#a60'>[tabela incerta — exportada como imagem]</p>"
    if b.role == Role.FOOTNOTE:
        label = (b.note_label or "").split(":")[-1]
        return f"{anchor}<p style=\"{style};font-size:{(st.size if st else 9) * scale:.1f}pt\"><sup>{html.escape(label)}</sup> {text}</p>"
    if b.role == Role.CAPTION:
        return f"{anchor}<p style=\"{style};font-style:italic\">{text}</p>"
    if b.role == Role.QUOTE:
        return f"{anchor}<blockquote style=\"{style}\">{text}</blockquote>"
    prefix = "<span style='color:#888'>↪ </span>" if b.continues_prev else ""
    return f"{anchor}<p style=\"{style};margin:0 0 4px 0\">{prefix}{text}</p>"


def page_html(project: Project, page: Page, selected_id: str | None = None, scale: float = 1.0) -> str:
    parts = ["<html><body style='background:#ffffff;color:#111'>"]
    heads = [b for b in page.ordered_blocks() if b.role.is_running and (b.bbox[1] + b.bbox[3]) / 2 < page.height / 2]
    foots = [b for b in page.ordered_blocks() if b.role.is_running and (b.bbox[1] + b.bbox[3]) / 2 >= page.height / 2]
    if heads:
        parts.append("<div style='color:#999;font-size:8pt;border-bottom:1px dashed #bbb'>"
                     + " · ".join(f"{html.escape(b.role.label)}: {html.escape(' '.join(ln.text for ln in b.lines))}" for b in heads)
                     + "</div>")
    notes = []
    for b in page.ordered_blocks():
        if b.role.is_running:
            continue
        if b.role == Role.FOOTNOTE:
            notes.append(b)
            continue
        parts.append(block_html(b, project, b.id == selected_id, scale))
    if notes:
        parts.append("<hr width='30%' align='left'>")
        for b in notes:
            parts.append(block_html(b, project, b.id == selected_id, scale))
    if foots:
        parts.append("<div style='color:#999;font-size:8pt;border-top:1px dashed #bbb;margin-top:8px'>"
                     + " · ".join(f"{html.escape(b.role.label)}: {html.escape(' '.join(ln.text for ln in b.lines))}" for b in foots)
                     + "</div>")
    parts.append("</body></html>")
    return "".join(parts)
