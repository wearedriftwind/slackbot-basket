import copy

import pytest
from bs4 import BeautifulSoup

from biljettbot.kalla import HamtningsFel
from biljettbot.service import BiljettService
from biljettbot.synk import Synkare, synk_kommando

from .conftest import FIXTURER, t

HTML = (FIXTURER / "luleabasket_matcher_2026-11-20.html").read_text(encoding="utf-8")


class Klocka:
    def __init__(self, s):
        self.nu = t(s)

    def __call__(self):
        return self.nu


class Kalla:
    def __init__(self, html=HTML):
        self.html = html
        self.fel = None

    def __call__(self, url):
        if self.fel:
            raise self.fel
        return self.html


def andra(html, fn):
    soup = BeautifulSoup(html, "html.parser")
    fn(soup)
    return str(soup)


def rad(soup, data_id):
    return soup.select_one(f".matchesLine[data-id='{data_id}']")


def ta_bort(*ids):
    def fn(soup):
        for i in ids:
            rad(soup, i).parent.decompose()
    return fn


def satt_datumtext(data_id, text):
    def fn(soup):
        rad(soup, data_id).select_one(".matchesDateDiv").string = text
    return fn


def lagg_till_slutspel(soup):
    container = soup.select_one(".matchContainer")
    rubrik = soup.new_tag("h4", attrs={"class": "matchHeadline"})
    rubrik.string = "April 2027"
    container.append(rubrik)
    mall = rad(soup, "4523").parent
    ny = copy.copy(mall)
    ny.select_one(".matchesLine")["data-id"] = "9001"
    ny.select_one(".matchesDateDiv").string = "02 Fre 19:00"
    ny.select_one(".matchesDateTeams").string = "Luleå Basket - Alvik Basket | Kvartsfinal 1"
    container.append(ny)


@pytest.fixture
def klocka():
    return Klocka("2026-11-20 06:30")


@pytest.fixture
def svc(db_importerad, cfg, klocka):
    return BiljettService(db_importerad, cfg, klocka)


@pytest.fixture
def kalla():
    return Kalla()


@pytest.fixture
def synk(svc, kalla):
    return Synkare(svc, kalla)


def biljetter(svc):
    with svc.db.anslut() as c:
        return [tuple(r) for r in c.execute("SELECT * FROM tickets ORDER BY match_id, nr")]


def test_forsta_synk_kopplar_tyst_utan_meddelande(synk, svc):
    r = synk.kor()
    assert r.utfall == "inga_andringar" and r.notiser == []
    assert svc.match("M05").kalla_id == "4510"
    assert svc.match("M01").kalla_anmarkning == "PREMIÄREN"
    assert svc.match("M01").anmarkning == "Premiär"   # manuell anmärkning skrivs inte över
    assert synk.kor().utfall == "inga_andringar"


def test_andrad_tid_for_bokad_match_namner_bokaren(synk, svc, kalla):
    synk.kor()
    svc.boka("M06", "U1", "U1", typ="kund", kund="Luleå Energi")
    fore = biljetter(svc)
    kalla.html = andra(HTML, satt_datumtext("4513", "18 Fre 18:30"))
    r = synk.kor()
    assert r.utfall == "ok"
    assert svc.match("M06").tid == "18:30"
    assert biljetter(svc) == fore                       # bokningar orörda
    assert len(r.notiser) == 2
    assert "M06" in r.notiser[0].text and "18:30" in r.notiser[0].text
    assert "<@U1>" in r.notiser[1].text and "har biljett" in r.notiser[1].text


def test_flyttat_datum_utan_tidigare_koppling_matchas_pa_motstandare(synk, svc, kalla):
    svc.boka("M05", "U1", "U1")
    with svc.db.transaktion() as c:
        c.execute("INSERT INTO reminders_sent VALUES ('M05','d0@10:00','2026-11-20T06:00:00+01:00','skickad')")
    kalla.html = andra(HTML, satt_datumtext("4510", "28 Lör 18:00"))
    r = synk.kor()
    m = svc.match("M05")
    assert (m.datum, m.tid, m.status) == ("2026-11-28", "18:00", "kommande")
    assert m.biljett(1).bokad_av == "U1"
    assert "lör 28 nov 18:00" in r.notiser[0].text
    assert "<@U1>" in r.notiser[1].text
    with svc.db.anslut() as c:  # påminnelse kan skickas för nya datumet
        assert c.execute("SELECT COUNT(*) FROM reminders_sent WHERE match_id='M05'").fetchone()[0] == 0


def test_saknad_match_installd_forst_efter_tva_synkar_och_kan_aterkomma(synk, svc, kalla):
    synk.kor()
    svc.boka("M07", "U2", "U2")
    kalla.html = andra(HTML, ta_bort("4517"))
    r = synk.kor()
    assert svc.match("M07").status == "kommande"
    assert "saknas på sidan" in r.notiser[0].text
    r = synk.kor()
    assert svc.match("M07").status == "inställd"
    assert "inställd" in r.notiser[0].text and "<@U2>" in r.notiser[1].text
    assert svc.match("M07").biljett(1).bokad_av == "U2"
    kalla.html = HTML
    r = synk.kor()
    assert svc.match("M07").status == "kommande"
    assert "tillbaka" in r.notiser[0].text


def test_installd_pa_sidan_satts_direkt(synk, svc, kalla):
    kalla.html = HTML.replace("Luleå Basket - Alvik Basket", "Luleå Basket - Alvik Basket | INSTÄLLD")
    synk.kor()
    assert svc.match("M05").status == "inställd"


def test_uppskjuten_blir_flyttad_och_sedan_kommande_med_nytt_datum(synk, svc, kalla):
    synk.kor()
    kalla.html = HTML.replace("Luleå Basket - AIK Basket", "Luleå Basket - AIK Basket | Uppskjuten")
    synk.kor()
    assert svc.match("M08").status == "flyttad"
    kalla.html = andra(HTML, satt_datumtext("4519", "07 Sön 15:00"))
    r = synk.kor()
    m = svc.match("M08")
    assert (m.status, m.datum, m.tid) == ("kommande", "2027-02-07", "15:00")
    assert "tillbaka" in r.notiser[0].text


def test_ny_slutspelsmatch_laggs_till(synk, svc, kalla):
    kalla.html = andra(HTML, lagg_till_slutspel)
    r = synk.kor()
    m = svc.match("M12")
    assert (m.datum, m.tid, m.motstandare, m.serie, m.status) == (
        "2027-04-02", "19:00", "Alvik Basket", "slutspel", "kommande")
    assert len(m.biljetter) == 2 and not m.bokade
    assert "Ny match `M12`" in r.notiser[0].text
    assert synk.kor().utfall == "inga_andringar"


def test_stort_tapp_avbryter_utan_andringar(synk, svc, kalla):
    synk.kor()
    svc.boka("M09", "U1", "U1")
    fore = svc.matcher()
    kalla.html = andra(HTML, ta_bort("4513", "4517", "4519", "4520", "4522", "4523"))
    r = synk.kor()
    assert r.utfall == "avbruten"
    assert "Inga matcher har ändrats" in r.notiser[0].text
    assert svc.matcher() == fore


def test_tvinga_kor_trots_varning(synk, svc, kalla):
    synk.kor()
    kalla.html = andra(HTML, ta_bort("4513", "4517", "4519", "4520", "4522", "4523"))
    assert synk.kor(tvinga=True).utfall == "ok"
    assert svc.match("M06").saknad_i_synk == 1


def test_hamtningsfel_varnar_och_rör_inget(synk, svc, kalla):
    fore = svc.matcher()
    kalla.fel = HamtningsFel("luleabasket.com svarade med HTTP 503")
    r = synk.kor()
    assert r.utfall == "fel" and "503" in r.notiser[0].text
    assert svc.matcher() == fore


def test_andrad_layout_varnar(synk, svc, kalla):
    fore = svc.matcher()
    kalla.html = "<html><body><div class='nyLayout'>Matcher</div>" + "x" * 2000 + "</body></html>"
    r = synk.kor()
    assert r.utfall == "fel" and "layouten" in r.notiser[0].text
    assert svc.matcher() == fore


def test_spelade_matcher_andras_inte(synk, svc, kalla):
    svc.registrera_besok("M02", 1, "U1", "ja", besokare="Anna", bokad_av="U1", typ="privat")
    kalla.html = andra(HTML, satt_datumtext("4504", "12 Mån 19:00"))
    synk.kor()
    m = svc.match("M02")
    assert (m.datum, m.tid) == ("2026-10-11", "15:00")
    assert m.biljett(1).besokare == "Anna"


def test_sync_runs_loggas(synk, svc):
    synk.kor()
    with svc.db.anslut() as c:
        r = c.execute("SELECT utfall, hittade FROM sync_runs").fetchone()
    assert tuple(r) == ("inga_andringar", 11)


def test_synk_kommando_tvinga_kraver_admin(synk, svc):
    assert "administratörer" in synk_kommando(svc, synk, "U1", True).text
    assert "oförändrat" in synk_kommando(svc, synk, "U1", False).text


def test_synka_via_slash(svc, synk):
    from biljettbot.kommandon import Kommandon
    kmd = Kommandon(svc, svc.cfg, synka=lambda a, tv: synk_kommando(svc, synk, a, tv))
    assert "oförändrat" in kmd.kommando("U1", "synka").text
    assert "administratörer" in kmd.kommando("U1", "admin synka tvinga").text
