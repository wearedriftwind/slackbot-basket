import threading

import pytest

from biljettbot.db import Database
from biljettbot.service import (Behorighet, BiljettService, EjBokningsbar, IngenBokning, Ogiltigt,
                                Upptagen)

from .conftest import t


@pytest.fixture
def klocka():
    class K:
        nu = t("2026-11-20 12:00")

        def __call__(self):
            return self.nu
    return K()


@pytest.fixture
def svc(db_importerad, cfg, klocka):
    return BiljettService(db_importerad, cfg, klocka)


def test_nasta_och_kommande(svc):
    assert svc.nasta().match_id == "M05"
    assert [m.match_id for m in svc.kommande()][:2] == ["M05", "M06"]
    assert len(svc.kommande()) == 7


def test_boka_en_privat(svc):
    r = svc.boka("M05", "U1", "U1")
    assert [b.nr for b in r.biljetter] == [1]
    b = svc.match("M05").biljett(1)
    assert (b.bokad_av, b.typ, b.kund, b.skapad_av) == ("U1", "privat", None, "U1")
    assert svc.match("M05").senast_uppdaterad == "2026-11-20T12:00:00+01:00"


def test_boka_kund_kraver_namn(svc):
    with pytest.raises(Ogiltigt):
        svc.boka("M05", "U1", "U1", typ="kund")
    r = svc.boka("M05", "U1", "U1", typ="kund", kund=" Skellefteå kommun ")
    assert r.biljetter[0].kund == "Skellefteå kommun"


def test_boka_bada(svc):
    r = svc.boka("M05", "U1", "U1", antal=2)
    assert [b.nr for b in r.biljetter] == [1, 2]
    assert svc.match("M05").lediga == []


def test_boka_at_nagon_annan(svc):
    r = svc.boka("M05", "U2", aktor="U1")
    assert r.biljetter[0].bokad_av == "U2" and r.biljetter[0].skapad_av == "U1"
    rad = svc.db.audit(1)[0]
    assert rad["aktor"] == "U1" and '"bokad_av": "U2"' in rad["detaljer"]


def test_dubbelbokning_visar_vem_som_har_biljetterna(svc):
    svc.boka("M05", "U1", "U1", typ="kund", kund="Skellefteå kommun")
    svc.boka("M05", "U2", "U2")
    with pytest.raises(Upptagen) as e:
        svc.boka("M05", "U3", "U3")
    assert "<@U1> (kund: Skellefteå kommun)" in e.value.meddelande
    assert "<@U2> (privat)" in e.value.meddelande
    assert {b.bokad_av for b in e.value.upptagna} == {"U1", "U2"}


def test_bada_nar_bara_en_ar_ledig(svc):
    svc.boka("M05", "U1", "U1")
    with pytest.raises(Upptagen, match="Bara en biljett"):
        svc.boka("M05", "U2", "U2", antal=2)
    assert svc.match("M05").biljett(2).bokad_av is None


def test_kan_inte_boka_spelad_installd_eller_pagaende(svc, klocka):
    with pytest.raises(EjBokningsbar):
        svc.boka("M01", "U1", "U1")
    svc.andra_match("M06", "UADMIN", status="inställd")
    with pytest.raises(EjBokningsbar, match="inställd"):
        svc.boka("M06", "U1", "U1")
    klocka.nu = t("2026-11-27 19:05")
    with pytest.raises(EjBokningsbar):
        svc.boka("M05", "U1", "U1")


def test_samtidiga_bokningar_ger_aldrig_dubbelbokning(tmp_path, cfg, klocka):
    from biljettbot import csvio
    from .conftest import CSV
    db = Database(tmp_path / "samtidigt.db")
    csvio.importera(db, CSV.read_text(encoding="utf-8-sig"), klocka.nu, "X")
    svc = BiljettService(db, cfg, klocka)
    lyckade, misslyckade = [], []
    start = threading.Barrier(8)

    def forsok(u):
        start.wait()
        try:
            svc.boka("M07", u, u)
            lyckade.append(u)
        except Upptagen:
            misslyckade.append(u)

    tradar = [threading.Thread(target=forsok, args=(f"U{i}",)) for i in range(8)]
    for tr in tradar:
        tr.start()
    for tr in tradar:
        tr.join()
    assert len(lyckade) == 2 and len(misslyckade) == 6
    assert {b.bokad_av for b in svc.match("M07").biljetter} == set(lyckade)


def test_avboka_egen(svc):
    svc.boka("M05", "U1", "U1", antal=2)
    r = svc.avboka("M05", "U1")
    assert [b.nr for b in r.avbokade] == [1, 2]
    assert not r.andras
    assert r.ledig_notis  # matchen är inom 7 dagar
    assert svc.match("M05").bokade == []


def test_avboka_specifik_biljett_for_annan_flaggas(svc):
    svc.boka("M05", "U1", "U1")
    svc.boka("M05", "U2", "U2")
    r = svc.avboka("M05", "U3", nr=2)
    assert r.andras and r.avbokade[0].bokad_av == "U2"
    assert svc.match("M05").biljett(1).bokad_av == "U1"


def test_avboka_utan_bokning(svc):
    svc.boka("M05", "U1", "U1")
    with pytest.raises(IngenBokning, match="<@U1>"):
        svc.avboka("M05", "U2")


def test_avboka_langt_fram_ger_ingen_ledig_notis(svc):
    svc.boka("M09", "U1", "U1")
    assert not svc.avboka("M09", "U1").ledig_notis


def test_uppdatera_spelade_tre_timmar_efter_uppkast(svc, klocka):
    klocka.nu = t("2026-11-27 21:59")
    assert svc.uppdatera_spelade() == []
    klocka.nu = t("2026-11-27 22:00")
    assert svc.uppdatera_spelade() == ["M05"]
    assert svc.uppdatera_spelade() == []
    assert svc.match("M05").status == "spelad"


def test_sommartid_hanteras(svc, klocka):
    # M11 2027-03-12 18:00 är vintertid (+01:00); en påhittad match efter omställningen ska få +02:00
    m = svc.ny_match("UADMIN", "2027-04-02", "19:00", "Slutspelslag", serie="slutspel")
    assert m.start(svc.cfg.tidszon).isoformat() == "2027-04-02T19:00:00+02:00"
    assert svc.match("M11").start(svc.cfg.tidszon).isoformat() == "2027-03-12T18:00:00+01:00"


def test_registrera_besok_i_efterhand_for_m01(svc):
    m = svc.registrera_besok("M01", 1, "U1", "ja", besokare="Anna", bokad_av="U1",
                             typ="kund", kund="Luleå kommun")
    b = m.biljett(1)
    assert (b.besokt, b.besokare, b.bokad_av, b.typ, b.kund) == ("ja", "Anna", "U1", "kund", "Luleå kommun")
    m = svc.registrera_besok("M01", 2, "U1", "nej")
    assert m.biljett(2).besokt == "nej" and m.biljett(2).besokare is None


def test_besok_standard_besokare_ar_den_som_bokat(svc, klocka):
    svc.boka("M05", "U1", "U1")
    klocka.nu = t("2026-11-28 10:00")
    svc.uppdatera_spelade()
    m = svc.registrera_besok("M05", 1, "U1", "ja")
    assert m.biljett(1).besokare == "<@U1>"


def test_besok_pa_kommande_match_nekas(svc):
    with pytest.raises(EjBokningsbar):
        svc.registrera_besok("M06", 1, "U1", "ja")


def test_avboka_spelad_nekas(svc):
    svc.registrera_besok("M01", 1, "U1", "ja", bokad_av="U1", typ="privat")
    with pytest.raises(EjBokningsbar):
        svc.avboka("M01", "U1")


def test_admin_kravs_for_schemaandring(svc):
    with pytest.raises(Behorighet):
        svc.andra_match("M05", "U1", tid="18:00")
    with pytest.raises(Behorighet):
        svc.ny_match("U1", "2027-04-01", "19:00", "X")
    with pytest.raises(Behorighet):
        svc.radera_match("M05", "U1")


def test_andra_match_rör_inte_bokningar(svc):
    svc.boka("M05", "U1", "U1")
    a = svc.andra_match("M05", "UADMIN", tid="18:00")
    assert a.falt == {"tid": ("19:00", "18:00")}
    assert a.match.biljett(1).bokad_av == "U1"
    with pytest.raises(Ogiltigt):
        svc.andra_match("M05", "UADMIN", biljett1_bokad_av="U9")


def test_radera_kraver_tvinga_vid_bokning(svc):
    svc.boka("M05", "U1", "U1")
    with pytest.raises(Ogiltigt):
        svc.radera_match("M05", "UADMIN")
    svc.radera_match("M05", "UADMIN", tvinga=True)
    assert svc.db.match("M05") is None


def test_ny_match_far_nasta_id(svc):
    assert svc.ny_match("UADMIN", "2027-03-20", "18:00", "Alvik Basket", serie="slutspel").match_id == "M12"
