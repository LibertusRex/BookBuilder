"""Verificações estruturais dos arquivos gerados (usadas nos testes e
disponíveis para o usuário avançado):

DOCX
* o corpo (``word/document.xml``) não contém os textos de cabeçalho/rodapé;
* esses textos estão nas partes ``header*.xml``/``footer*.xml``;
* números de página são campos PAGE;
* notas de rodapé estão em ``word/footnotes.xml`` e são referenciadas no corpo.

EPUB
* todos os XHTML são XML bem formado;
* nenhum capítulo contém os cabeçalhos/rodapés repetidos;
* cada chamada de nota aponta para uma nota existente e vice-versa;
* o sumário (nav) tem entradas.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field

from lxml import etree

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


@dataclass
class Check:
    ok: bool = True
    messages: list[str] = field(default_factory=list)

    def fail(self, msg: str) -> None:
        self.ok = False
        self.messages.append("FALHA: " + msg)

    def info(self, msg: str) -> None:
        self.messages.append("ok: " + msg)


def _texts(xml: bytes) -> str:
    root = etree.fromstring(xml)
    paras = []
    for p in root.iter(W + "p"):
        paras.append("".join(t.text or "" for t in p.iter(W + "t")))
    return "\n".join(paras)


def check_docx(path: str, running_texts: list[str], expect_footnotes: int | None = None,
               body_must_contain: list[str] | None = None) -> Check:
    c = Check()
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        body_xml = z.read("word/document.xml")
        body = _texts(body_xml)
        headers = "\n".join(_texts(z.read(n)) for n in names if re.match(r"word/header\d*\.xml", n))
        footers_xml = [z.read(n) for n in names if re.match(r"word/footer\d*\.xml", n)]
        footers = "\n".join(_texts(x) for x in footers_xml)
        for t in running_texts:
            if t in body:
                c.fail(f"o corpo contém o texto de margem “{t[:50]}”")
            elif t in headers or t in footers:
                c.info(f"“{t[:50]}” está no cabeçalho/rodapé, fora do corpo")
            else:
                c.messages.append(f"aviso: “{t[:50]}” não está nem no corpo nem nas margens")
        if any(b"PAGE" in x for x in footers_xml) or any(b"PAGE" in z.read(n) for n in names if "header" in n):
            c.info("número de página como campo PAGE")
        fn_count = 0
        if "word/footnotes.xml" in names:
            root = etree.fromstring(z.read("word/footnotes.xml"))
            ids = [int(f.get(W + "id")) for f in root.iter(W + "footnote") if int(f.get(W + "id")) > 0]
            fn_count = len(ids)
            refs = {int(r.get(W + "id")) for r in etree.fromstring(body_xml).iter(W + "footnoteReference")}
            missing = set(ids) - refs
            if missing:
                c.fail(f"notas sem referência no corpo: {sorted(missing)[:10]}")
            else:
                c.info(f"{fn_count} notas de rodapé nativas, todas referenciadas no corpo")
        if expect_footnotes is not None and fn_count != expect_footnotes:
            c.fail(f"esperadas {expect_footnotes} notas de rodapé, encontradas {fn_count}")
        for t in body_must_contain or []:
            if t not in body:
                c.fail(f"o corpo não contém “{t[:60]}”")
    return c


def check_epub(path: str, running_texts: list[str], expect_footnotes: int | None = None,
               body_must_contain: list[str] | None = None) -> Check:
    c = Check()
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        if names[0] != "mimetype" or z.read("mimetype") != b"application/epub+zip":
            c.fail("o primeiro arquivo do EPUB deve ser 'mimetype'")
        if z.getinfo("mimetype").compress_type != zipfile.ZIP_STORED:
            c.fail("'mimetype' não pode ser comprimido")
        xhtml = [n for n in names if n.endswith(".xhtml")]
        all_text = []
        note_ids, ref_targets = set(), set()
        for n in xhtml:
            data = z.read(n)
            try:
                root = etree.fromstring(data)
            except etree.XMLSyntaxError as e:
                c.fail(f"{n} não é XML bem formado: {e}")
                continue
            if n.endswith("nav.xhtml"):
                links = root.findall(".//{http://www.w3.org/1999/xhtml}a")
                if links:
                    c.info(f"sumário com {len(links)} entradas")
                else:
                    c.fail("sumário vazio")
                continue
            txt = "".join(root.itertext())
            all_text.append(txt)
            for el in root.iter():
                t = el.get("{http://www.idpf.org/2007/ops}type")
                if t == "footnote":
                    note_ids.add(el.get("id"))
                elif t == "noteref":
                    ref_targets.add(el.get("href", "").lstrip("#"))
        full = "\n".join(all_text)
        for t in running_texts:
            if t in full:
                c.fail(f"o EPUB contém o texto de margem “{t[:50]}”")
        if running_texts:
            c.info("nenhum cabeçalho/rodapé repetido no texto do EPUB")
        if ref_targets - note_ids:
            c.fail(f"chamadas para notas inexistentes: {sorted(ref_targets - note_ids)[:5]}")
        if note_ids:
            c.info(f"{len(note_ids)} notas de rodapé com links de ida e volta")
        if expect_footnotes is not None and len(note_ids) != expect_footnotes:
            c.fail(f"esperadas {expect_footnotes} notas no EPUB, encontradas {len(note_ids)}")
        for t in body_must_contain or []:
            if t not in full:
                c.fail(f"o EPUB não contém “{t[:60]}”")
    return c
