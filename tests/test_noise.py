"""Ícones, molduras e telas do leitor lidos como conteúdo."""

from collections import Counter

import pytest

from bookbuilder.layout import noise
from bookbuilder.layout.fusion import Lexicon, load_dictionary
from bookbuilder.model import Block, Line, Page, Role, Word


@pytest.fixture(scope="module")
def lex():
    return Lexicon(Counter({"exculpante": 3}), load_dictionary())


@pytest.mark.parametrize("t", ["|", "—)", "o 5222 2A9\"\"\" 0 O CC“ C“C“CTTTITOOOOA",
                               "À o o o o o o o o A o o o o o o o", "AO A CA, 1.00", "aeentdo a",
                               "Losseeeeehehnenenenhennenenennnn A", "ME"])
def test_lixo(lex, t):
    assert noise.garbage_text(t, lex)


@pytest.mark.parametrize("t", ["É o dolo.", "Subdivide-se em:", "Arts. 154-A e 154-B", "Aris. 181 a 183-A",
                               "CAPÍTULO II-A", "c) excesso exculpante;", "53,8 § 5.º, CF);", "a)",
                               "> http://uqr.to/1yohc", "Admite-se."])
def test_texto_valido(lex, t):
    assert not noise.garbage_text(t, lex)


def _blk(role, bbox, text, bid="b"):
    words = [Word(w, bbox, 90) for w in text.split()]
    return Block(bid, role, bbox, lines=[Line(bbox, words)])


def test_moldura_larga_e_icones(lex):
    p = Page.__new__(Page)
    p.height, p.width = 2960, 1840
    p.blocks = [_blk(Role.BODY, (355, 1437, 1493, 1524), "Lone —", "a"),
                _blk(Role.BODY, (218, 206, 1630, 281), "Texto normal do livro com várias palavras válidas.", "b"),
                _blk(Role.FIGURE, (407, 694, 467, 762), "", "c"),
                _blk(Role.FIGURE, (746, 324, 1102, 651), "", "d"),
                _blk(Role.FOOTER, (625, 2848, 1227, 2873), "Updated 3 days ago on Nelson's Kindle Cloud Reader", "e")]
    assert noise.mark([p], lex) == 3
    assert [b.role for b in p.blocks] == [Role.ARTIFACT, Role.BODY, Role.ARTIFACT, Role.FIGURE, Role.ARTIFACT]
