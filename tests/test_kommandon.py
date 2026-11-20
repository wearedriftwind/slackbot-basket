import json

import pytest

from biljettbot.kommandon import Kommandon
from biljettbot.service import BiljettService

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


def test_slash_boka_utfors_direkt_och_notis_till_kanalen(kmd):
    svar = kmd.kommando("U1", "boka Alvik kund: Skellefteå kommun")
    assert svar.text.startswith("✅")
    assert not svar.offentlig
    assert len(svar.notiser) == 1 and "<@U1>" in svar.notiser[0].text
    assert "Skellefteå kommun" in svar.notiser[0].text
    assert kmd.svc.match("M05").biljett(1).kund == "Skellefteå kommun"


def test_fritext_boka_kraver_bekraftelse(kmd):
    svar = kmd.fritext("U1", "boka en biljett till Alvik, kund: Skellefteå kommun")
    assert svar.personlig
    assert kmd.svc.match("M05").bokade == []
    resultat = bekrafta(kmd, "U1", svar)
    assert resultat.text.startswith("✅")
    assert kmd.svc.match("M05").biljett(1).bokad_av == "U1"


def test_bara_den_som_fragade_kan_bekrafta(kmd):
    svar = kmd.fritext("U1", "boka båda till Alvik")
    r = bekrafta(kmd, "U2", svar)
    assert "Bara <@U1>" in r.text
    assert kmd.svc.match("M05").bokade == []


def test_fritext_boka_at_nagon_annan(kmd):
    svar = kmd.fritext("U1", "<@UBOT> boka åt <@U2|bertil> till östersund", bot_id="UBOT")
    assert "åt <@U2>" in svar.text
    r = bekrafta(kmd, "U1", svar)
    assert "<@U1> bokade åt <@U2>" in r.notiser[0].text
    b = kmd.svc.match("M06").biljett(1)
    assert (b.bokad_av, b.skapad_av) == ("U2", "U1")


def test_upptagen_visas_direkt_utan_bekraftelse(kmd):
    kmd.kommando("U1", "boka båda alvik privat")
    svar = kmd.fritext("U3", "boka en biljett till Alvik")
    assert svar.text.startswith("⛔") and "<@U1>" in svar.text


def test_vem_har_biljetterna(kmd):
    kmd.kommando("U1", "boka 18 dec kund: Luleå Energi")
    svar = kmd.fritext("U2", "vem har biljetterna 18 dec?")
    assert "<@U1> – kund: Luleå Energi" in svar.text
    assert "Biljett 2: _ledig_" in svar.text


def test_avboka_med_bekraftelse_och_ledig_notis(kmd):
    kmd.kommando("U1", "boka alvik")
    svar = kmd.fritext("U1", "avboka min biljett till alvik")
    assert "Avboka biljett 1" in svar.text
    r = bekrafta(kmd, "U1", svar)
    assert r.text.startswith("↩️")
    # matchen är inom 7 dagar → notisen innehåller bokningsknappar
    assert any(b["type"] == "actions" for b in r.notiser[0].blocks)


def test_avboka_nagon_annans_biljett_nämner_den(kmd):
    kmd.kommando("U1", "boka alvik")
    r = kmd.kommando("U2", "avboka alvik biljett 1")
    assert "<@U1> – din bokning är borttagen" in r.notiser[0].text


def test_slash_boka_utan_match_oppnar_modal(kmd):
    svar = kmd.kommando("U1", "boka")
    assert svar.modal and svar.modal["callback_id"] == "boka_vy"


def test_slash_boka_kund_utan_namn_oppnar_modal_med_match(kmd):
    svar = kmd.kommando("U1", "boka alvik kund")
    sel = svar.modal["blocks"][0]["element"]
    assert sel["initial_option"]["value"] == "M05"


def test_modal_bokning(kmd):
    values = {"match": {"v": {"selected_option": {"value": "M06"}}},
              "antal": {"v": {"selected_option": {"value": "2"}}},
              "typ": {"v": {"selected_option": {"value": "kund"}}},
              "kund": {"v": {"value": ""}},
              "for": {"v": {"selected_user": "U2"}}}
    fel, _ = kmd.boka_fran_modal("U1", values)
    assert "kund" in fel
    values["kund"]["v"]["value"] = "Norrbottens Energi"
    fel, svar = kmd.boka_fran_modal("U1", values)
    assert fel is None and svar.text.startswith("✅")
    assert {b.bokad_av for b in kmd.svc.match("M06").biljetter} == {"U2"}


def test_listor(kmd):
    assert "M05" in kmd.kommando("U1", "kommande").text
    assert "M01" not in kmd.kommando("U1", "kommande").text
    assert "M01" in kmd.kommando("U1", "säsong").text
    assert "Alvik" in kmd.kommando("U1", "").text   # tomt = nästa


def test_knapp_boka_privat(kmd):
    assert kmd.knapp_boka_privat("U1", "M05").text.startswith("✅")
    kmd.knapp_boka_privat("U2", "M05")
    assert kmd.knapp_boka_privat("U3", "M05").text.startswith("⛔")


def test_admin_skyddat(kmd):
    assert "administratörer" in kmd.kommando("U1", "admin ändra M05 tid=18:00").text
    assert "administratörer" in kmd.kommando("U1", "export").text


def test_admin_andra_nämner_bokare(kmd):
    kmd.kommando("U1", "boka alvik")
    r = kmd.kommando("UADMIN", "admin ändra M05 tid=18:00")
    assert "tid: 19:00 → 18:00" in r.notiser[0].text and "<@U1>" in r.notiser[0].text


def test_admin_ny_och_export(kmd):
    r = kmd.kommando("UADMIN", 'admin ny 2027-03-20 18:00 Alvik Basket serie=slutspel')
    assert "M12" in r.text
    ex = kmd.kommando("UADMIN", "export")
    assert ex.fil[0].endswith(".csv") and "M12,2027-03-20" in ex.fil[1]


def test_spelad_satts_vid_kommando(kmd, klocka):
    klocka.nu = t("2026-11-28 09:00")
    kmd.kommando("U1", "kommande")
    assert kmd.svc.match("M05").status == "spelad"
