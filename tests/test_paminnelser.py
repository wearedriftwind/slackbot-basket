from dataclasses import replace

import pytest

from biljettbot.config import parse_paminnelser
from biljettbot.paminnelser import Paminnare, forfallna
from biljettbot.service import BiljettService

from .conftest import TZ, t


class Klocka:
    def __init__(self, s):
        self.nu = t(s)

    def __call__(self):
        return self.nu


class Postlada:
    def __init__(self):
        self.skickat = []
        self.fel = False

    def __call__(self, text, blocks):
        if self.fel:
            raise RuntimeError("Slack nere")
        self.skickat.append((text, blocks))


@pytest.fixture
def klocka():
    return Klocka("2026-11-27 09:59")


@pytest.fixture
def svc(db_importerad, cfg, klocka):
    return BiljettService(db_importerad, cfg, klocka)


@pytest.fixture
def post():
    return Postlada()


def test_matchdag_klockan_tio(svc, klocka, post):
    p = Paminnare(svc, post)
    assert p.kor() == []
    klocka.nu = t("2026-11-27 10:00")
    assert p.kor() == ["M05"]
    text, blocks = post.skickat[0]
    assert "Match idag!" in text and "Alvik Basket" in text and "19:00" in text
    assert "Båda biljetterna är lediga" in text
    knappar = [e["action_id"] for b in blocks if b["type"] == "actions" for e in b["elements"]]
    assert "boka_privat" in knappar and "boka_modal" in knappar


def test_skickas_aldrig_tva_ganger_aven_efter_omstart(svc, klocka, post):
    klocka.nu = t("2026-11-27 10:00")
    Paminnare(svc, post).kor()
    klocka.nu = t("2026-11-27 12:00")
    # "omstart": ny Paminnare och ny service mot samma databas
    svc2 = BiljettService(svc.db, svc.cfg, klocka)
    assert Paminnare(svc2, post).kor() == []
    assert len(post.skickat) == 1
    assert svc.db.paminnelse_skickad()["M05"].startswith("2026-11-27T10:00")


def test_ingen_paminnelse_nar_bada_ar_bokade(svc, klocka, post):
    svc.boka("M05", "U1", "U1", antal=2)
    klocka.nu = t("2026-11-27 10:00")
    assert Paminnare(svc, post).kor() == []


def test_en_ledig_namns(svc, klocka, post):
    svc.boka("M05", "U1", "U1")
    klocka.nu = t("2026-11-27 10:00")
    Paminnare(svc, post).kor()
    assert "En biljett är ledig" in post.skickat[0][0]


def test_ingen_paminnelse_efter_uppkast(svc, klocka, post):
    klocka.nu = t("2026-11-27 19:00")
    assert Paminnare(svc, post).kor() == []


def test_ingen_paminnelse_for_installd(svc, klocka, post):
    svc.andra_match("M05", "UADMIN", status="inställd")
    klocka.nu = t("2026-11-27 10:00")
    assert Paminnare(svc, post).kor() == []


def test_misslyckad_postning_forsoks_igen(svc, klocka, post):
    klocka.nu = t("2026-11-27 10:00")
    post.fel = True
    assert Paminnare(svc, post).kor() == []
    post.fel = False
    klocka.nu = t("2026-11-27 10:05")
    assert Paminnare(svc, post).kor() == ["M05"]


def test_flera_regler_bara_senaste_efter_driftstopp(svc, klocka, post):
    cfg = replace(svc.cfg, paminnelser=tuple(parse_paminnelser("3@09:00,0@10:00")))
    svc = BiljettService(svc.db, cfg, klocka)
    p = Paminnare(svc, post)
    klocka.nu = t("2026-11-24 09:00")
    assert p.kor() == ["M05"]
    assert "Match på fredag!" in post.skickat[-1][0]
    klocka.nu = t("2026-11-27 10:00")
    assert p.kor() == ["M05"]
    assert "Match idag!" in post.skickat[-1][0]
    # en match vars båda tidpunkter passerat när boten varit nere: bara en påminnelse
    klocka.nu = t("2026-12-18 11:00")
    assert p.kor() == ["M06"]
    assert p.kor() == []
    with svc.db.anslut() as c:
        rader = dict(c.execute("SELECT typ, status FROM reminders_sent WHERE match_id='M06'").fetchall())
    assert rader == {"d3@09:00": "hoppad", "d0@10:00": "skickad"}


def test_regelfunktionen_ar_ren(db_importerad):
    regler = tuple(parse_paminnelser("0@10:00"))
    matcher = db_importerad.matcher()
    f = forfallna(matcher, set(), regler, t("2026-11-27 10:00"), TZ)
    assert [x.match.match_id for x in f] == ["M05"]
    assert forfallna(matcher, {("M05", "d0@10:00")}, regler, t("2026-11-27 10:00"), TZ) == []


def test_sommartid_regel_foljer_lokal_tid(db_importerad, cfg):
    from biljettbot.paminnelser import regelns_tidpunkt
    svc = BiljettService(db_importerad, cfg)
    m = svc.ny_match("UADMIN", "2027-04-02", "19:00", "Slutspelslag")
    r = parse_paminnelser("0@10:00")[0]
    assert regelns_tidpunkt(m, r, TZ).isoformat() == "2027-04-02T10:00:00+02:00"


def test_ogiltig_paminnelseregel():
    with pytest.raises(ValueError):
        parse_paminnelser("matchdag")


def test_schemalaggaren_startar_och_kor_tick_direkt(db_importerad, cfg):
    import time as _time
    from biljettbot import jobb
    post = Postlada()
    svc = BiljettService(db_importerad, cfg, lambda: t("2026-11-27 10:00"))
    s = jobb.starta(svc, Paminnare(svc, post))
    try:
        for _ in range(50):
            if post.skickat:
                break
            _time.sleep(0.05)
        assert len(post.skickat) == 1
        assert {j.id for j in s.get_jobs()} == {"tick"}
    finally:
        s.shutdown(wait=False)
