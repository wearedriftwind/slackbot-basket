import json

import pytest

from biljettbot.kommandon import Kommandon
from biljettbot.service import BiljettService
from biljettbot.statistik import berakna, format_statistik

from .conftest import t


class Klocka:
    def __init__(self, s):
        self.nu = t(s)

    def __call__(self):
        return self.nu


@pytest.fixture
def klocka():
    return Klocka("2026-11-20 12:00")


@pytest.fixture
def kmd(db_importerad, cfg, klocka):
    return Kommandon(BiljettService(db_importerad, cfg, klocka), cfg)


def bekrafta(kmd, anv, svar):
    knapp = next(e for b in svar.blocks if b["type"] == "actions" for e in b["elements"]
                 if e["action_id"] == "bekrafta")
    return kmd.utfor(anv, json.loads(knapp["value"]))


def test_slash_besok_m01_i_efterhand(kmd):
    r = kmd.kommando("U1", "besök M01 1 ja Anna Andersson")
    assert r.text.startswith("📝")
    b = kmd.svc.match("M01").biljett(1)
    assert (b.besokt, b.besokare) == ("ja", "Anna Andersson")
    kmd.kommando("U1", "besök Visby biljett 2 nej")
    assert kmd.svc.match("M01").biljett(2).besokt == "nej"


def test_slash_besok_utan_ja_nej_oppnar_modal(kmd):
    r = kmd.kommando("U1", "besök Brahe")
    assert r.modal["callback_id"] == "besok_vy"
    assert json.loads(r.modal["private_metadata"])["m"] == "M02"


def test_slash_besok_utan_match_listar_spelade(kmd):
    r = kmd.kommando("U1", "besök")
    texter = " ".join(b["text"]["text"] for b in r.blocks if b["type"] == "section")
    assert "M01" in texter and "M04" in texter and "M05" not in texter


def test_fritext_jag_gick(kmd, klocka):
    kmd.kommando("U1", "boka alvik")
    klocka.nu = t("2026-11-28 10:00")
    svar = kmd.fritext("U1", "jag gick på Alvik-matchen")
    assert "besökt av <@U1>" in svar.text
    bekrafta(kmd, "U1", svar)
    b = kmd.svc.match("M05").biljett(1)
    assert (b.besokt, b.besokare) == ("ja", "<@U1>")


def test_fritext_oanvand_biljett(kmd, klocka):
    kmd.kommando("U1", "boka båda alvik kund: Skellefteå kommun")
    klocka.nu = t("2026-11-28 10:00")
    svar = kmd.fritext("U1", "biljett 2 till Alvik användes inte")
    bekrafta(kmd, "U1", svar)
    m = kmd.svc.match("M05")
    assert m.biljett(2).besokt == "nej" and m.biljett(1).besokt is None


def test_fritext_besokare_med_namn(kmd):
    svar = kmd.fritext("U1", "biljett 1 till Högsbo besökt, besökare: Kalle från Skellefteå kommun")
    assert "Kalle från Skellefteå kommun" in svar.text


def test_modal_besok_med_bokningsuppgifter(kmd):
    values = {
        "bokad_av1": {"v": {"selected_user": "U7"}}, "typ1": {"v": {"selected_option": {"value": "kund"}}},
        "kund1": {"v": {"value": "Luleå kommun"}}, "besokt1": {"v": {"selected_option": {"value": "ja"}}},
        "besokare1": {"v": {"value": "Eva"}},
        "bokad_av2": {"v": {"selected_user": None}}, "typ2": {"v": {"selected_option": None}},
        "kund2": {"v": {"value": None}}, "besokt2": {"v": {"selected_option": {"value": "nej"}}},
        "besokare2": {"v": {"value": None}},
    }
    fel, svar = kmd.besok_fran_modal("U1", "M03", values)
    assert fel is None
    m = kmd.svc.match("M03")
    assert (m.biljett(1).bokad_av, m.biljett(1).kund, m.biljett(1).besokare) == ("U7", "Luleå kommun", "Eva")
    assert m.biljett(2).besokt == "nej"


def test_modal_besok_kraver_ja_nej_och_kundnamn(kmd):
    values = {"bokad_av1": {"v": {"selected_user": "U7"}}, "besokt1": {"v": {"selected_option": None}}}
    fel, _ = kmd.besok_fran_modal("U1", "M03", values)
    assert "besokt1" in fel
    values = {"typ1": {"v": {"selected_option": {"value": "kund"}}},
              "besokt1": {"v": {"selected_option": {"value": "ja"}}}}
    fel, _ = kmd.besok_fran_modal("U1", "M03", values)
    assert "kund1" in fel


def test_statistik(kmd, klocka):
    s = kmd.svc
    s.registrera_besok("M01", 1, "U1", "ja", besokare="Anna", bokad_av="U1", typ="privat")
    s.registrera_besok("M01", 2, "U1", "ja", besokare="anna ", bokad_av="U2", typ="kund", kund="Luleå kommun")
    s.registrera_besok("M02", 1, "U1", "nej", bokad_av="U2", typ="kund", kund="luleå kommun")
    s.registrera_besok("M03", 1, "U1", "ja", besokare="Bertil", bokad_av="U3", typ="kund", kund="Skellefteå kommun")
    s.boka("M05", "U1", "U1")
    st = berakna(s.matcher())
    assert st.spelade == 4
    assert (st.anvanda, st.oanvanda) == (3, 1)
    assert [(m.match_id, n) for m, n in st.besokta_matcher] == [("M01", 2), ("M03", 1)]
    assert st.besokare[0] == ("Anna", 2)
    assert st.bokare[:2] == [("<@U1>", 2), ("<@U2>", 2)]  # lika – alfabetiskt
    assert (st.privat, st.kund) == (2, 3)
    assert st.kunder[0] == ("Luleå kommun", 2)
    assert [m.match_id for m in st.saknar_historik] == ["M01", "M02", "M03", "M04"][1:]
    text = kmd.kommando("U1", "statistik").text
    assert "Använda: *3*" in text and "Luleå kommun – 2" in text
    assert "Saknar besöksuppgifter" in text


def test_statistik_fritext(kmd):
    assert "Flest besök" in kmd.fritext("U1", "vem har gått flest gånger?").text


def test_statistik_del():
    from biljettbot.statistik import Statistik
    st = Statistik(0, [], 0, 0, 0, [], [], 0, 0, [], [])
    assert "inga registrerade besök" in format_statistik(st, "besok")


def test_statuskort_for_spelad_match_har_besoksknapp(kmd):
    r = kmd.kommando("U1", "status M02")
    knappar = [e["action_id"] for b in r.blocks if b["type"] == "actions" for e in b["elements"]]
    assert knappar == ["besok_modal"]
