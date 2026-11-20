import json

from biljettbot import csvio
from biljettbot.kommandon import Kommandon
from biljettbot.service import BiljettService

from .conftest import CSV, t


def bekrafta(kmd, anv, svar):
    knapp = next(e for b in svar.blocks if b["type"] == "actions" for e in b["elements"]
                 if e["action_id"] == "bekrafta")
    return kmd.utfor(anv, json.loads(knapp["value"]))


def stokiga_till(svc):
    svc.boka("M05", "U1", "U1", typ="kund", kund="Testkund")
    svc.boka("M06", "U2", "U2", antal=2)
    svc.avboka("M06", "U2", nr=2)
    svc.registrera_besok("M01", 1, "U1", "ja", besokare="Anna", bokad_av="U1", typ="privat")
    svc.ny_match("UADMIN", "2027-04-02", "19:00", "Testlag", serie="slutspel")
    svc.andra_match("M07", "UADMIN", tid="12:00")
    with svc.db.transaktion() as c:
        c.execute("INSERT INTO reminders_sent VALUES ('M05','d0@10:00','2026-11-20T10:00:00+01:00','skickad')")
        c.execute("UPDATE matches SET kalla_id='4510' WHERE match_id='M05'")
        c.execute("INSERT INTO sync_runs (start, utfall) VALUES ('2026-11-20T06:30:00+01:00', 'ok')")


def test_aterstall_ger_exakt_startunderlaget(db_importerad, cfg):
    svc = BiljettService(db_importerad, cfg, lambda: t("2026-11-20 12:00"))
    stokiga_till(svc)
    kmd = Kommandon(svc, cfg)
    fraga = kmd.kommando("UADMIN", "admin återställ")
    assert "Återställa" in fraga.text and svc.match("M05").bokade  # inget hänt än
    r = bekrafta(kmd, "UADMIN", fraga)
    assert r.text.startswith("🔄")
    assert csvio.exportera(svc.db).splitlines() == CSV.read_text(encoding="utf-8").splitlines()
    assert svc.db.match("M12") is None
    assert svc.db.paminnelse_skickad() == {}
    with svc.db.anslut() as c:
        assert c.execute("SELECT COUNT(*) FROM sync_runs").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM reminders_sent").fetchone()[0] == 0
    logg = svc.db.audit()
    assert [(x["aktor"], x["handling"]) for x in logg] == [("UADMIN", "aterstall")]
    # backup av läget före återställningen, och notis i kanalen
    namn, backup = r.fil
    assert namn.startswith("backup_fore_aterstallning") and "Testkund" in backup and "Testlag" in backup
    assert "återställde" in r.notiser[0].text


def test_aterstall_kraver_admin(db_importerad, cfg):
    svc = BiljettService(db_importerad, cfg, lambda: t("2026-11-20 12:00"))
    svc.boka("M05", "U1", "U1")
    kmd = Kommandon(svc, cfg)
    assert "administratörer" in kmd.kommando("U1", "admin återställ").text
    # även en förfalskad bekräftelse från icke-admin nekas
    assert "administratörer" in kmd.utfor("U1", {"h": "aterstall", "a": "U1"}).text
    assert svc.match("M05").bokade


def test_aterstall_med_ogiltig_fil_andrar_inget(db_importerad, cfg, tmp_path):
    from dataclasses import replace
    trasig = tmp_path / "trasig.csv"
    trasig.write_text("match_id,datum\nM01,igår\n", encoding="utf-8")
    cfg = replace(cfg, csv_import=str(trasig))
    svc = BiljettService(db_importerad, cfg, lambda: t("2026-11-20 12:00"))
    svc.boka("M05", "U1", "U1")
    kmd = Kommandon(svc, cfg)
    r = bekrafta(kmd, "UADMIN", kmd.kommando("UADMIN", "admin återställ"))
    assert r.text.startswith("⚠️")
    assert svc.match("M05").bokade


def test_spelad_satts_direkt_efter_aterstallning(db_importerad, cfg):
    svc = BiljettService(db_importerad, cfg, lambda: t("2026-11-28 12:00"))
    kmd = Kommandon(svc, cfg)
    bekrafta(kmd, "UADMIN", kmd.kommando("UADMIN", "admin återställ"))
    assert svc.match("M05").status == "spelad"


def test_cli_aterstall(tmp_path, monkeypatch, capsys):
    from biljettbot import cli
    db = tmp_path / "cli.db"
    monkeypatch.setenv("DATABAS_SOKVAG", str(db))
    monkeypatch.setenv("CSV_IMPORT_SOKVAG", str(CSV))
    cli.main(["import"])
    assert cli.main(["aterstall"]) == 1        # kräver --ja
    assert cli.main(["aterstall", "--ja"]) == 0
    assert "Backup" in capsys.readouterr().out
    assert list(tmp_path.glob("backup_fore_aterstallning_*.csv"))
