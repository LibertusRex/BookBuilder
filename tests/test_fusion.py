"""Regras da fusão Tesseract × Surya (definidas pelo usuário)."""

from collections import Counter

import pytest

from bookbuilder.layout.fusion import Lexicon, Stats, fuse, load_dictionary
from bookbuilder.model import Run


@pytest.fixture(scope="module")
def lex():
    return Lexicon(Counter({"titulação": 5, "índices": 4, "criminologia": 9}), load_dictionary())


def _fuse(tess_text, surya_html, lex, low=()):
    runs = []
    for w in tess_text.split(" "):
        runs.append(Run(w, low_conf=w in low))
        runs.append(Run(" "))
    st = Stats()
    out = fuse(runs[:-1], surya_html, lex, st)
    return out, st


def text(runs):
    return "".join(r.text for r in runs)


def yellow(runs):
    return [r.text for r in runs if r.low_conf and r.text.strip()]


def test_concordancia_tira_amarelo(lex):
    out, st = _fuse("a pena é dura", "<p>a pena é dura</p>", lex, low={"dura"})
    assert text(out) == "a pena é dura" and not yellow(out) and st.agree == 4


def test_uma_letra_vence_tesseract(lex):
    out, _ = _fuse("da pena em todas as fases", "<p>da pena em todos as fases</p>", lex)
    assert text(out) == "da pena em todas as fases" and not yellow(out)


def test_palavra_junta_vence_surya(lex):
    out, _ = _fuse("2.2 Sistemadaexasperação aplica", "<p>2.2 Sistema da exasperação aplica</p>", lex)
    assert text(out) == "2.2 Sistema da exasperação aplica" and not yellow(out)


def test_acento_do_surya_so_se_existe(lex):
    out, _ = _fuse("n) titulação inadequada", "<p>n) títulação inadequada</p>", lex)
    assert "titulação" in text(out)  # "títulação" não existe
    out, _ = _fuse("dos Indices de", "<p>dos índices de</p>", lex)
    assert "índices" in text(out)


def test_romanos_numeracao_paragrafo_travessao(lex):
    out, _ = _fuse("inciso Il — ser tratada (S 1.º, 11).", "<p>inciso II – ser tratada (§ 1.º, II).</p>", lex)
    assert text(out) == "inciso II — ser tratada (§ 1.º, II)." and not yellow(out)
    out, _ = _fuse("22 Classificação", "<p>2.2 Classificação</p>", lex)
    assert text(out) == "2.2 Classificação"


def test_aspas_curvas_do_tesseract(lex):
    out, _ = _fuse("item 3.3, “a”).", '<p>item 3.3, "a").</p>', lex)
    assert text(out) == "item 3.3, “a”)."


def test_italico_do_surya(lex):
    out, _ = _fuse("Lições de direito penal", "<p><i>Lições de direito penal</i></p>", lex)
    assert all(r.italic for r in out if r.text.strip())


def test_outras_diferencas_surya_com_amarelo(lex):
    out, st = _fuse("o crime xyzzq foi", "<p>o crime contra foi</p>", lex)
    assert "contra" in text(out) and yellow(out) == ["contra"] and st.flagged == 1


def test_lixo_do_tesseract_descartado_marcas_mantidas(lex):
    out, _ = _fuse("aprimorame IS ] ] […] execução", "<p>aprimorame execução</p>", lex, low={"IS"})
    t = text(out)
    assert t == "aprimorame […] execução"


def test_hifen_pelo_dicionario(lex):
    out, _ = _fuse("do Código de Processo", "<p>do Có-digo de Processo</p>", lex)
    assert "Código" in text(out)
    out, _ = _fuse("mais detalhado seguindose o", "<p>mais detalhado seguindo-se o</p>", lex)
    assert "seguindo-se" in text(out)


def test_sem_surya_fica_tesseract(lex):
    runs = [Run("texto original")]
    assert fuse(runs, "", lex) == runs


def test_trecho_com_contagens_diferentes_pareado(lex):
    out, _ = _fuse("de violência ou grave ameaça", "<p>de violencia ou meaça</p>", lex)
    t = text(out)
    assert "violência" in t and "ameaça" in t  # uma letra/acento: Tesseract
    out, _ = _fuse("2.2 Classificação doftipo penal", "<p>2.2 Classificação do tipo penal</p>", lex)
    assert text(out) == "2.2 Classificação do tipo penal" and not yellow(out)
    out, _ = _fuse("comunidade; Ill — ser", "<p>comunidade; III – ser</p>", lex)
    assert text(out) == "comunidade; III — ser" and not yellow(out)


def test_alucinacao_descartada(lex):
    out, st = _fuse("o § 1.º do art. 338-A estabelece que o delito", "<p>– 0.7 – 0.5 – 0.8 – 0.2</p>", lex)
    assert text(out).startswith("o § 1.º do art. 338-A") and st.rejected == 1


def test_paragrafo_com_parentese(lex):
    out, _ = _fuse("pena ($ 4.º, parte", "<p>pena ($ 4.º, parte</p>", lex)
    assert text(out) == "pena (§ 4.º, parte"


def test_juncao_preserva_acento_e_rejeita_fragmentos(lex):
    out, _ = _fuse("a) éocometimento da injúria", "<p>a) e o cometimento da injúria</p>", lex)
    assert text(out) == "a) é o cometimento da injúria"
    out, _ = _fuse("a) éocometimento da injúria", "<p>a) e o c cometimento da injúria</p>", lex)
    assert "c cometimento" not in text(out) or yellow(out)


def test_numeros_inventados_rejeitados(lex):
    out, st = _fuse("b) o § 2.º fixa norma de conteúdo processual penal",
                    "<p>b) 0.5 ± 2.5° fixa – 0.7 – 0.5 – 0.3 norma de conteúdo processual penal</p>", lex)
    assert text(out).startswith("b) o § 2.º fixa norma") and st.rejected == 1


def test_latex_e_paragrafo_em_palavra_junta(lex):
    out, _ = _fuse("c) o 3.º prevê a", "<p>c) 0 $3.^{\\circ}$ prevê a</p>", lex)
    assert "3.º prevê" in text(out)
    out, _ = _fuse("a) oS1.ºdoart.338-A estabelece", "<p>a) o § 1.º do art. 338-A estabelece</p>", lex)
    assert text(out) == "a) o § 1.º do art. 338-A estabelece" and not yellow(out)
    out, _ = _fuse("pena do & 2.º-A (dois", "<p>pena do § 2.º–A (dois</p>", lex)
    assert "§ 2.º" in text(out) and not yellow(out)


def test_deteccao_de_placa_dedicada():
    from bookbuilder.ocr.surya import parse_devices

    txt = """Available devices:
  Vulkan0: AMD Radeon RX 9070 XT (16304 MiB, 15416 MiB free)
  Vulkan1: Intel(R) UHD Graphics 770 (32768 MiB, 30000 MiB free)
  Vulkan2: AMD Radeon(TM) Graphics (8192 MiB, 8000 MiB free)
  Vulkan3: NVIDIA GeForce GTX 1050 (2048 MiB, 1900 MiB free)
  Vulkan4: Intel(R) Arc(TM) A770 Graphics (16032 MiB, 15000 MiB free)
"""
    ds = {g.name: g.dedicated for g in parse_devices(txt)}
    assert ds == {"AMD Radeon RX 9070 XT": True, "Intel(R) UHD Graphics 770": False,
                  "AMD Radeon(TM) Graphics": False, "NVIDIA GeForce GTX 1050": False,
                  "Intel(R) Arc(TM) A770 Graphics": True}


def test_marcador_de_lista_do_tesseract(lex):
    out, _ = _fuse("g) crime praticado contra criança", "<ul><li>crime praticado contra criança</li></ul>", lex)
    assert text(out) == "g) crime praticado contra criança" and not yellow(out)


def test_url_do_surya(lex):
    out, _ = _fuse("podcast > http//ugrto/Iyohd fim", "<p>podcast &gt; http://uqr.to/1yohd fim</p>", lex)
    assert "http://uqr.to/1yohd" in text(out) and not yellow(out)


def test_numero_cnj_pelo_digito_verificador(lex):
    from bookbuilder.layout.fusion import cnj_valid

    assert cnj_valid("1501054-54.2019.8.26.0197") is True
    assert cnj_valid("1501054-54.2019.8.26.0191") is False
    out, _ = _fuse("(REsp 1501054-54.2019.8.26.0191, rel.", "<p>(REsp 1501054-54.2019.8.26.0197, rel.</p>", lex)
    assert "1501054-54.2019.8.26.0197" in text(out) and not yellow(out)


def test_divergencia_registrada_e_decisao_aplicada(lex):
    from bookbuilder.layout.fusion import Stats, fuse_block

    runs = [Run("o crime xyzzq foi grave")]
    r1 = fuse_block(runs, "<p>o crime contra foi grave</p>", lex, Stats())
    assert len(r1.disputes) == 1 and r1.disputes[0].tess == "xyzzq" and r1.disputes[0].surya == "contra"
    k = r1.disputes[0].key
    r2 = fuse_block(runs, "<p>o crime contra foi grave</p>", lex, Stats(), choices={k: "tess"})
    assert text(r2.runs) == "o crime xyzzq foi grave" and not yellow(r2.runs) and not r2.disputes
    r3 = fuse_block(runs, "<p>o crime contra foi grave</p>", lex, Stats(), choices={k: "surya"})
    assert text(r3.runs) == "o crime contra foi grave" and not yellow(r3.runs)


def test_romano_minusculo(lex):
    out, _ = _fuse("e; (11) aplicar", "<p>e; (ii) aplicar</p>", lex)
    assert text(out) == "e; (ii) aplicar" and not yellow(out)


def test_revisao_aplica_divergencia(lex):
    from bookbuilder.layout.fusion import Stats, fuse_block
    from bookbuilder.model import Block, Page, Project, Role, SourceRef
    from bookbuilder.review import apply_dispute

    def make():
        r = fuse_block([Run("o crime xyzzq foi grave")], "<p>o crime contra foi grave</p>", lex, Stats())
        b = Block("b", Role.BODY, (0, 0, 1, 1), runs=r.runs, disputes=[d.to_dict(None) for d in r.disputes])
        p = Page(SourceRef("x.png"))
        p.blocks = [b]
        return Project(pages=[p]), b

    proj, b = make()
    apply_dispute(proj, b, 0, "tess")
    assert text(b.runs) == "o crime xyzzq foi grave" and not yellow(b.runs) and not b.disputes
    proj, b = make()
    apply_dispute(proj, b, 0, "surya", everywhere=True)
    assert text(b.runs) == "o crime contra foi grave" and not yellow(b.runs)


# --- ruído de ícone, confusão romano/§ × 1/l/|, ordinais

def test_inciso_romano_lido_como_1_vence_surya(lex):
    out, st = _fuse("e o art. 66,1, da Lei de Execução Penal",
                    "<p>e o art. 66, I, da Lei de Execução Penal</p>", lex)
    assert text(out) == "e o art. 66, I, da Lei de Execução Penal" and not yellow(out)


def test_inciso_11_vence_surya(lex):
    out, _ = _fuse("(art. 122,11, LEP).", "<p>(art. 122, II, LEP).</p>", lex)
    assert text(out) == "(art. 122, II, LEP)." and not yellow(out)


def test_barra_vertical_como_romano(lex):
    out, _ = _fuse("mental: |– ter acesso", "<p>mental: I – ter acesso</p>", lex)
    assert text(out) == "mental: I – ter acesso" and not yellow(out)


def test_icone_no_inicio_do_bloco_some(lex):
    out, _ = _fuse("L PONTO RELEVANTE PARA DEBATE", "<p>PONTO RELEVANTE PARA DEBATE</p>", lex)
    assert text(out) == "PONTO RELEVANTE PARA DEBATE" and not yellow(out)
    out, _ = _fuse("Equivalência dos antecedentes", "<p>a Equivalência dos antecedentes</p>", lex)
    assert text(out) == "Equivalência dos antecedentes" and not yellow(out)


def test_romano_inserido_no_meio_continua(lex):
    out, _ = _fuse("do inciso § 1.º", "<p>do inciso I, § 1.º</p>", lex)
    assert "I," in text(out)


def test_ordinal_vence_surya(lex):
    out, _ = _fuse("22º EDIÇÃO", "<p>22ª EDIÇÃO</p>", lex)
    assert text(out) == "22ª EDIÇÃO" and not yellow(out)
    out, _ = _fuse("168, § 1.9; 226", "<p>168, § 1.º; 226</p>", lex)
    assert text(out) == "168, § 1.º; 226" and not yellow(out)


def test_numeracao_de_secao_omitida_pelo_surya_fica(lex):
    out, _ = _fuse("1.2 Costumee fonte", "<p>Costume e fonte</p>", lex)
    assert text(out) == "1.2 Costume e fonte" and not yellow(out)


def test_marcador_lido_errado(lex):
    out, _ = _fuse("£) parto suposto", "<p>f) parto suposto</p>", lex)
    assert text(out) == "f) parto suposto" and not yellow(out)


def test_tamanho_do_traco_vem_da_imagem(lex):
    out, _ = _fuse("a antiga –, verificando", "<p>a antiga —, verificando</p>", lex)
    assert text(out) == "a antiga –, verificando" and not yellow(out)


def test_romano_duplo_lido_como_um_1(lex):
    out, _ = _fuse("no art. 61,1, e, do Código", "<p>no art. 61, II, e, do Código</p>", lex)
    assert text(out) == "no art. 61, II, e, do Código" and not yellow(out)


def test_hifen_de_artigo_nao_vira_traco(lex):
    out, _ = _fuse("do art. 121-A deste", "<p>do art. 121–A deste</p>", lex)
    assert text(out) == "do art. 121-A deste"


def test_surya_repete_vizinha_e_pontuacao_acompanha(lex):
    out, _ = _fuse("computando-se o último. Se este", "<p>computando-se o último dia. Se este</p>", lex)
    assert text(out) == "computando-se o último dia. Se este"
    out, _ = _fuse("possível de diferenciar a preparação", "<p>possível de diferencia ciar a preparação</p>", lex)
    assert text(out) == "possível de diferenciar a preparação"
