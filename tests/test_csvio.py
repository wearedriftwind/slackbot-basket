import pytest

from biljettbot import csvio
from biljettbot.db import Database

from .conftest import CSV, t


def test_import_av_startunderlaget(db_importerad):
    matcher = db_importerad.matcher()
    assert [m.match_id for m in matcher] == [f"M{i:02d}" for i in range(1, 12)]
    m05 = db_importerad.match("M05")
    assert (m05.datum, m05.tid, m05.motstandare, m05.status) == (
        "2026-11-27", "19:00", "Alvik Basket", "kommande")
    assert len(m05.biljetter) == 2 and not m05.bokade
    assert db_importerad.match("M01").anmarkning == "Premiär"
    assert db_importerad.audit()[0]["handling"] == "csv_import"


def test_export_ger_samma_csv_som_importen(db_importerad):
    original = CSV.read_text(encoding="utf-8")
    assert csvio.exportera(db_importerad).splitlines() == original.splitlines()


def test_export_med_bokning_och_paminnelse(db_importerad):
    with db_importerad.transaktion() as c:
        c.execute("UPDATE tickets SET bokad_av='U1', typ='kund', kund='Skellefteå kommun' "
                  "WHERE match_id='M05' AND nr=1")
        c.execute("INSERT INTO reminders_sent VALUES ('M05','d0@10:00','2026-11-27T10:00:00+01:00','skickad')")
    rad = next(r for r in csvio.las_csv(csvio.exportera(db_importerad)) if r["match_id"] == "M05")
    assert rad["biljett1_bokad_av"] == "U1"
    assert rad["biljett1_kund"] == "Skellefteå kommun"
    assert rad["paminnelse_skickad"] == "2026-11-27T10:00:00+01:00"


def test_rundtur_import_export_import(tmp_path, db_importerad):
    with db_importerad.transaktion() as c:
        c.execute("UPDATE tickets SET bokad_av='U9', typ='privat', besokt='ja', besokare='Anna' "
                  "WHERE match_id='M02' AND nr=2")
    data = csvio.exportera(db_importerad, utokad=True)
    db2 = Database(tmp_path / "b.db")
    csvio.importera(db2, data, t("2026-11-20 10:00"), "X")
    assert csvio.exportera(db2, utokad=True) == data


def test_import_vagrar_om_databasen_har_data(db_importerad):
    with pytest.raises(csvio.ImportFel):
        csvio.importera(db_importerad, CSV.read_text(encoding="utf-8-sig"), t("2026-11-20 10:00"), "X")


def test_import_vid_forsta_start_ar_idempotent(db):
    assert csvio.importera_vid_forsta_start(db, CSV, t("2026-11-20 10:00"), "X") == 11
    assert csvio.importera_vid_forsta_start(db, CSV, t("2026-11-20 10:00"), "X") == 0
    assert len(db.matcher()) == 11


@pytest.mark.parametrize("kolumn,varde", [
    ("status", "pågår"), ("datum", "27/11"), ("biljett1_typ", "vip"), ("biljett1_besokt", "kanske"),
])
def test_ogiltig_rad_avbryter_hela_importen(db, kolumn, varde):
    rader = CSV.read_text(encoding="utf-8-sig").splitlines()
    huvud = rader[0].split(",")
    falt = rader[5].split(",")
    falt[huvud.index(kolumn)] = varde
    rader[5] = ",".join(falt)
    with pytest.raises(csvio.ImportFel):
        csvio.importera(db, "\n".join(rader), t("2026-11-20 10:00"), "X")
    assert db.matcher() == []


def test_kund_kraver_kundnamn(db):
    rader = CSV.read_text(encoding="utf-8-sig").splitlines()
    huvud = rader[0].split(",")
    falt = rader[5].split(",")
    falt[huvud.index("biljett1_bokad_av")] = "U1"
    falt[huvud.index("biljett1_typ")] = "kund"
    rader[5] = ",".join(falt)
    with pytest.raises(csvio.ImportFel, match="kund krävs"):
        csvio.importera(db, "\n".join(rader), t("2026-11-20 10:00"), "X")
