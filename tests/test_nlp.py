import pytest

from biljettbot import nlp

from .conftest import t

NU = t("2026-11-20 12:00")


@pytest.fixture
def matcher(db_importerad):
    return db_importerad.matcher()


def hitta(text, matcher, foredra="kommande"):
    a = nlp.tolka(text)
    return a, nlp.hitta_match(a.ref, matcher, NU, foredra)


def test_exempel_ur_kravet_boka_kund(matcher):
    a, tr = hitta("boka en biljett till Alvik, kund: Skellefteå kommun", matcher)
    assert a.typ == "boka" and a.antal == 1
    assert a.bokningstyp == "kund" and a.kund == "Skellefteå kommun"
    assert tr.match.match_id == "M05"


def test_exempel_ur_kravet_vem_har_biljetterna(matcher):
    a, tr = hitta("vem har biljetterna 18 dec?", matcher)
    assert a.typ == "status"
    assert tr.match.match_id == "M06"


@pytest.mark.parametrize("text,match_id", [
    ("boka båda till östersund", "M06"),
    ("boka två biljetter till Ostersund", "M06"),
    ("boka 2 biljetter M7", "M07"),
    ("boka en till eos lund", "M07"),
    ("boka en biljett 2027-02-06", "M08"),
    ("boka en biljett den 20/2", "M09"),
    ("boka en biljett till norrköpingsmatchen", "M09"),
    ("boka en biljett till onsla pirates", "M10"),      # stavfel
    ("boka nästa match", "M05"),
    ("boka fredag", "M05"),
    ("boka en biljett 12:e mars", "M11"),
])
def test_boka_matchreferenser(matcher, text, match_id):
    a, tr = hitta(text, matcher)
    assert a.typ == "boka"
    assert tr.match is not None, tr.fel
    assert tr.match.match_id == match_id


def test_antal(matcher):
    assert nlp.tolka("boka båda till alvik").antal == 2
    assert nlp.tolka("boka två biljetter till alvik").antal == 2
    assert nlp.tolka("boka en biljett till alvik").antal == 1
    assert nlp.tolka("boka till alvik").antal is None


def test_boka_at_nagon_annan():
    a = nlp.tolka("boka åt <@U123ABC|anna> till Alvik, privat")
    assert a.typ == "boka" and a.for_anvandare == "U123ABC" and a.bokningstyp == "privat"


def test_botens_egen_mention_ignoreras():
    a = nlp.tolka("<@UBOT> boka en biljett till alvik", bot_id="UBOT")
    assert a.typ == "boka" and a.for_anvandare is None


def test_kund_utan_kolon_och_fore_matchen(matcher):
    a, tr = hitta("boka en biljett för kund Luleå Energi till Alvik", matcher)
    assert a.kund == "Luleå Energi" and tr.match.match_id == "M05"
    a, tr = hitta("boka kund: Skellefteå kommun på fredag", matcher)
    assert a.kund == "Skellefteå kommun" and tr.match.match_id == "M05"


def test_kund_utan_namn():
    a = nlp.tolka("boka en kundbiljett till alvik")
    assert a.typ == "boka"
    a = nlp.tolka("boka en biljett till alvik för kund")
    assert a.bokningstyp == "kund" and a.kund is None


@pytest.mark.parametrize("text,typ", [
    ("avboka min biljett till alvik", "avboka"),
    ("jag kan inte gå på fredag", "avboka"),
    ("släpp biljetten till AIK", "avboka"),
    ("när är nästa match?", "nasta"),
    ("nästa", "nasta"),
    ("visa schemat", "sasong"),
    ("hela säsongen", "sasong"),
    ("kommande matcher", "kommande"),
    ("finns det lediga biljetter till alvik?", "status"),
    ("vem gick på alvik?", "status"),
    ("vem har gått flest gånger?", "statistik"),
    ("vilka kunder har fått biljetter", "statistik"),
    ("statistik", "statistik"),
    ("synka", "synka"),
    ("hjälp", "hjalp"),
    ("jag gick på Visby-matchen", "besok"),
    ("biljett 2 till Brahe användes inte", "besok"),
    ("blablabla", "okand"),
])
def test_avsikter(text, typ):
    assert nlp.tolka(text).typ == typ


def test_avboka_specifik_biljett_och_alla():
    a = nlp.tolka("avboka biljett 2 till alvik")
    assert a.nr == 2 and not a.alla
    assert nlp.tolka("avboka båda till alvik").alla
    assert not nlp.tolka("avboka biljetterna till alvik").alla


def test_besok_ja_nej(matcher):
    a, tr = hitta("jag gick på Visby-matchen", matcher, "spelad")
    assert a.besokt == "ja" and tr.match.match_id == "M01"
    a, tr = hitta("biljett 2 till Brahe användes inte", matcher, "spelad")
    assert a.besokt == "nej" and a.nr == 2 and tr.match.match_id == "M02"


def test_ingen_matchreferens(matcher):
    _, tr = hitta("boka en biljett", matcher)
    assert tr.match is None and "Vilken match" in tr.fel


def test_okant_lag(matcher):
    _, tr = hitta("boka en biljett till Real Madrid", matcher)
    assert tr.match is None


def test_samma_motstandare_flera_ganger_foredrar_nasta_kommande(db_importerad):
    from biljettbot.config import Config
    from biljettbot.service import BiljettService
    BiljettService(db_importerad, Config(admins=frozenset({"A"})), lambda: NU).ny_match(
        "A", "2027-03-25", "19:00", "Alvik Basket", serie="slutspel")
    matcher = db_importerad.matcher()
    assert hitta("boka alvik", matcher)[1].match.match_id == "M05"
    later = t("2026-12-01 12:00")
    a = nlp.tolka("boka alvik")
    assert nlp.hitta_match(a.ref, matcher, later).match.match_id == "M12"
