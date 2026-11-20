import pytest

from biljettbot import csvio
from biljettbot.kalla import ParserFel, parsa

from .conftest import CSV, FIXTURER

HTML = (FIXTURER / "luleabasket_matcher_2026-11-20.html").read_text(encoding="utf-8")


def test_fixturen_ger_samma_hemmamatcher_som_startunderlaget():
    p = parsa(HTML)
    assert len(p.matcher) == 22
    assert p.varningar == []
    rader = csvio.las_csv(CSV.read_text(encoding="utf-8-sig"))
    assert [(k.datum, k.tid, k.bortalag) for k in p.hemma] == [
        (r["datum"], r["tid"], r["motstandare"]) for r in rader]


def test_detaljer():
    p = parsa(HTML)
    forsta = p.hemma[0]
    assert (forsta.kalla_id, forsta.anmarkning, forsta.resultat) == ("4503", "PREMIÄREN", "105 - 74")
    # "15.00" normaliseras till "15:00"
    assert p.hemma[2].tid == "15:00"
    assert not any(k.hemma for k in p.matcher if k.bortalag == "Luleå Basket")
    assert all(k.status_hint is None for k in p.matcher)


def test_saknad_struktur_ger_tydligt_fel():
    with pytest.raises(ParserFel, match="layouten"):
        parsa("<html><body><h1>Ny sajt!</h1></body></html>")


def test_okand_manadsrubrik():
    with pytest.raises(ParserFel, match="månadsrubrik"):
        parsa(HTML.replace("Oktober 2026", "Okt-26"))


def test_inga_hemmamatcher():
    with pytest.raises(ParserFel, match="ingen hemmamatch"):
        parsa(HTML, hemmalag="Annat Lag")


def test_trasigt_datum():
    with pytest.raises(ParserFel):
        parsa(HTML.replace("03 Lör 18:00", "Lördag kväll"))


def test_installd_och_flyttad_kanns_igen():
    html = (HTML.replace("Luleå Basket - Alvik Basket", "Luleå Basket - Alvik Basket | INSTÄLLD")
                .replace("Luleå Basket - AIK Basket", "Luleå Basket - AIK Basket | Uppskjuten"))
    p = {k.bortalag: k for k in parsa(html).hemma}
    assert p["Alvik Basket"].status_hint == "inställd"
    assert p["AIK Basket"].status_hint == "flyttad"


def test_fel_veckodag_ger_varning():
    p = parsa(HTML.replace("27 Fre 19.00", "27 Lör 19.00"))
    assert any("Veckodagen" in v for v in p.varningar)
