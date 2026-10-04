"""Relatório final: páginas processadas, avisos, substituições de fontes e
falhas. Gerado em texto (para a tela) e em HTML (salvo ao lado dos arquivos)."""

from __future__ import annotations

import html
from datetime import datetime

from ..document import DocReport, flow_pages
from ..model import PageStatus, Project

FLAG_LABELS = {
    "baixa_confianca": "trechos de baixa confiança no OCR",
    "encoberto": "texto encoberto por sobreposição (marcado “[…]”)",
    "sobreposicao": "sobreposição da tela (aviso, botões) removida",
    "ilegivel": "trecho ilegível",
    "tabela_incerta": "tabela não reconhecida com segurança (inserida como imagem)",
    "nota_sem_chamada": "nota de rodapé sem chamada localizada",
    "verificar_margem": "texto de margem não repetido: conferir se é cabeçalho/rodapé",
    "margem_reclassificada": "bloco de margem devolvido ao conteúdo",
    "inclinacao_corrigida": "inclinação corrigida",
    "orientacao_corrigida": "orientação corrigida",
    "sem_texto": "página sem texto reconhecido",
}


def flag_label(flag: str) -> str:
    if flag.startswith("colunas:"):
        return f"Página com {flag.split(':')[1]} colunas de texto"
    extra = {
        "rotulo_capitulo": "rótulo “Capítulo …” incorporado ao título",
        "incorporado_ao_titulo": "incorporado ao título do capítulo",
        "juntar_sem_espaco": "palavra hifenizada unida à página anterior",
        "manter_duplicata": "incluída mesmo sendo captura repetida",
    }
    return FLAG_LABELS.get(flag) or extra.get(flag, flag)


def page_issues(project: Project) -> dict[str, list[str]]:
    """Problemas por página (nome → descrições), para o filtro da revisão e o relatório."""
    out: dict[str, list[str]] = {}
    for p in project.pages:
        if p.excluded:
            continue
        items: list[str] = []
        if p.status == PageStatus.ERROR:
            items.append(f"falha: {p.error}")
        if p.duplicate_of:
            items.append("página repetida (captura duplicada) — fora da exportação")
        flags = set(p.flags)
        for b in p.blocks:
            flags.update(b.flags)
        for f in sorted(flags):
            if f.startswith("colunas:"):
                items.append(f"{f.split(':')[1]} colunas")
            elif f in FLAG_LABELS:
                items.append(FLAG_LABELS[f])
        if items:
            out[p.source.display_name] = items
    return out


def text_report(project: Project, rep: DocReport | None, export_notes: list[str] | None = None) -> str:
    lines = [f"Relatório BookBuilder — {datetime.now():%d/%m/%Y %H:%M}", ""]
    active = [p for p in project.pages if not p.excluded]
    done = [p for p in active if p.status == PageStatus.DONE]
    errors = [p for p in active if p.status == PageStatus.ERROR]
    pend = [p for p in active if p.status == PageStatus.PENDING]
    excluded = [p for p in project.pages if p.excluded]
    lines.append(f"Páginas selecionadas: {len(active)} (excluídas pelo usuário: {len(excluded)})")
    lines.append(f"Processadas com sucesso: {len(done)}; com falha: {len(errors)}; pendentes: {len(pend)}")
    fp = flow_pages(project)
    lines.append(f"Páginas exportadas: {len(fp)}")
    if rep:
        if rep.duplicates:
            lines.append(f"Capturas repetidas deixadas de fora: {len(rep.duplicates)}")
            lines += [f"  • {d}" for d in rep.duplicates[:30]]
        lines.append("")
        lines.append("Cabeçalhos, rodapés e números de página identificados:")
        lines += [f"  • {n}" for n in rep.running_notes] or ["  • nenhum"]
        lines.append(f"Títulos: {rep.headings}; notas de rodapé: {rep.footnotes} (sem chamada localizada: {rep.footnotes_unlinked})")
        lines.append(f"Parágrafos unidos entre páginas: {rep.continuations}")
        lines.append(f"Parágrafos partidos pelo layout e reunidos: {rep.merged}")
        if getattr(rep, "noise", 0):
            lines.append(f"Ícones, molduras e telas do leitor retirados (podem ser devolvidos na revisão): {rep.noise}")
        if rep.fusion is not None:
            f = rep.fusion
            lines.append(f"Releitura com o Surya: {rep.surya_pages} páginas. Palavras em que os dois leram igual: {f.agree}; "
                         f"decididas pelo Tesseract: {f.tess}; pelo Surya: {f.surya}; descartadas (lixo do OCR): {f.dropped}; "
                         f"resolvidas pelo árbitro por recorte: {f.arbiter}; divergências ainda abertas (amarelo): "
                         f"{rep.disputes_open}; blocos com releitura descartada (sem relação com o texto): {f.rejected}.")
        else:
            lines.append("Releitura com o Surya: não usada (só Tesseract).")
        lines.append(f"Correções automáticas de OCR (marcadas para revisão): {rep.corrections}")
        lines.append(f"Sinais corrigidos (§ lido como S/$, travessões): {rep.symbol_fixes}")
        lines.append(f"Escala tipográfica: {rep.scale_source}")
        lines.append("")
        lines.append("Fontes (estimativas):")
        lines += [f"  • {n}" for n in rep.font_notes] or ["  • —"]
    lines.append("")
    if errors:
        lines.append("Falhas:")
        lines += [f"  • {p.source.display_name}: {p.error}" for p in errors]
        lines.append("")
    issues = page_issues(project)
    review = {k: v for k, v in issues.items() if any("baixa" in x or "encoberto" in x or "ilegível" in x or "falha" in x or "tabela" in x or "nota" in x for x in v)}
    if review:
        lines.append(f"Páginas que merecem revisão ({len(review)}):")
        for name, items in list(review.items())[:200]:
            lines.append(f"  • {name}: {'; '.join(items)}")
        if len(review) > 200:
            lines.append(f"  … e mais {len(review) - 200}")
        lines.append("")
    if export_notes:
        lines.append("Observações da exportação:")
        seen = set()
        for n in export_notes:
            if n not in seen:
                lines.append(f"  • {n}")
                seen.add(n)
        lines.append("")
    lines.append("Limitações: as fontes e tamanhos são estimativas; textos encobertos na imagem original não podem ser")
    lines.append("recuperados e aparecem como “[…]”; leitores de EPUB podem trocar a fonte e o tamanho definidos.")
    return "\n".join(lines)


def html_report(text: str) -> str:
    body = html.escape(text).replace("\n", "<br>\n")
    return ("<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'><title>Relatório BookBuilder</title>"
            "<style>body{font-family:Segoe UI,Arial,sans-serif;max-width:60em;margin:2em auto;line-height:1.45}</style>"
            f"</head><body>{body}</body></html>")
