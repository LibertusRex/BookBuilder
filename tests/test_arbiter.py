from bookbuilder.ocr.arbiter import vote


def test_maioria_decide():
    assert vote("todas", "todos", ["da pena em todas as fases", "em todas as"]) == "tess"
    assert vote("doftipo", "do tipo", ["Classificação do tipo", "do tipo penal"]) == "surya"
    assert vote("contra", "contro", ["contra", "contro"]) is None  # empate: fica para a revisão


def test_alternativa_vazia():
    # Só o Surya leu "grave"; os recortes também leem → Surya
    assert vote("", "grave", ["ou grave ameaça"]) == "surya"
    # Só o Tesseract leu "IS"; nenhum recorte confirma → Surya (sem o texto)
    assert vote("IS", "", ["aprimoramento da legislação"]) == "surya"
