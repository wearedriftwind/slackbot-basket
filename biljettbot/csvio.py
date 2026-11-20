"""Import av startunderlaget (CSV) och export tillbaka till exakt samma format."""
from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime, time
from pathlib import Path

from . import tid
from .db import BESOKT_VARDEN, BOKNINGSTYPER, STATUSAR, Database, Match

KOLUMNER = [
    "match_id", "datum", "veckodag", "tid", "motstandare", "arena", "anmarkning", "status",
    "biljett1_bokad_av", "biljett1_typ", "biljett1_kund", "biljett1_besokt", "biljett1_besokare",
    "biljett2_bokad_av", "biljett2_typ", "biljett2_kund", "biljett2_besokt", "biljett2_besokare",
    "paminnelse_skickad", "senast_uppdaterad",
]
# Extra kolumner som exporteras med `utokad=True` och accepteras vid import.
EXTRA_KOLUMNER = ["serie", "kalla_id"]


class ImportFel(ValueError):
    pass


def _tom(v: str | None) -> str | None:
    v = (v or "").strip()
    return v or None


def las_csv(text: str) -> list[dict[str, str]]:
    text = text.lstrip("﻿")
    lasare = csv.DictReader(io.StringIO(text))
    saknas = [k for k in KOLUMNER if k not in (lasare.fieldnames or [])]
    if saknas:
        raise ImportFel(f"CSV saknar kolumner: {', '.join(saknas)}")
    return list(lasare)


def validera_rad(rad: dict[str, str], radnr: int) -> None:
    def fel(msg: str) -> ImportFel:
        return ImportFel(f"Rad {radnr} ({rad.get('match_id')}): {msg}")

    if not re.fullmatch(r"M\d{2,}", rad["match_id"].strip()):
        raise fel("ogiltigt match_id")
    try:
        date.fromisoformat(rad["datum"].strip())
    except ValueError:
        raise fel("datum ska vara YYYY-MM-DD") from None
    if _tom(rad["tid"]):
        try:
            time.fromisoformat(rad["tid"].strip())
        except ValueError:
            raise fel("tid ska vara HH:MM") from None
    if rad["status"].strip() not in STATUSAR:
        raise fel(f"status måste vara en av {STATUSAR}")
    for n in (1, 2):
        typ = _tom(rad[f"biljett{n}_typ"])
        if typ and typ not in BOKNINGSTYPER:
            raise fel(f"biljett{n}_typ måste vara privat/kund")
        if typ == "kund" and not _tom(rad[f"biljett{n}_kund"]):
            raise fel(f"biljett{n}_kund krävs när typ = kund")
        if _tom(rad[f"biljett{n}_kund"]) and typ != "kund":
            raise fel(f"biljett{n}_kund får bara anges när typ = kund")
        besokt = _tom(rad[f"biljett{n}_besokt"])
        if besokt and besokt not in BESOKT_VARDEN:
            raise fel(f"biljett{n}_besokt måste vara ja/nej")
        bokad_av = _tom(rad[f"biljett{n}_bokad_av"])
        if bokad_av and not re.fullmatch(r"[UW][A-Z0-9]+", bokad_av):
            raise fel(f"biljett{n}_bokad_av ska vara ett Slack-användar-ID (U…)")
    for k in ("paminnelse_skickad", "senast_uppdaterad"):
        if _tom(rad[k]):
            try:
                datetime.fromisoformat(rad[k].strip())
            except ValueError:
                raise fel(f"{k} ska vara ISO 8601") from None


def importera(db: Database, text: str, nu: datetime, arena_standard: str) -> int:
    """Importerar alla rader i en transaktion. Returnerar antal importerade matcher.
    Avbryter utan ändringar om någon rad är ogiltig eller om databasen redan har data."""
    rader = las_csv(text)
    for i, rad in enumerate(rader, start=2):
        validera_rad(rad, i)
    with db.transaktion() as c:
        if c.execute("SELECT COUNT(*) FROM matches").fetchone()[0]:
            raise ImportFel("Databasen innehåller redan matcher – import avbruten")
        _skriv_rader(db, c, rader, nu, arena_standard)
        db.logga(c, nu, "import", "csv_import", antal=len(rader))
    return len(rader)


def aterstall(db: Database, text: str, nu: datetime, arena_standard: str, aktor: str) -> int:
    """Raderar ALLT (matcher, bokningar, besök, påminnelser, synkhistorik, audit-logg) och
    importerar CSV-filen på nytt – i en transaktion. Filen valideras innan något raderas."""
    rader = las_csv(text)
    for i, rad in enumerate(rader, start=2):
        validera_rad(rad, i)
    with db.transaktion() as c:
        for tabell in ("reminders_sent", "tickets", "matches", "sync_runs", "audit_log", "meta"):
            c.execute(f"DELETE FROM {tabell}")
        _skriv_rader(db, c, rader, nu, arena_standard)
        db.logga(c, nu, aktor, "aterstall", antal=len(rader))
    return len(rader)


def _skriv_rader(db: Database, c, rader: list[dict[str, str]], nu: datetime, arena_standard: str) -> None:
    for rad in rader:
        mid = rad["match_id"].strip()
        db.skapa_match(c, Match(
            match_id=mid,
            datum=rad["datum"].strip(),
            tid=_tom(rad["tid"]),
            motstandare=rad["motstandare"].strip(),
            arena=_tom(rad["arena"]) or arena_standard,
            anmarkning=(rad["anmarkning"] or "").strip(),
            status=rad["status"].strip(),
            serie=_tom(rad.get("serie")) or "grundserie",
            kalla_id=_tom(rad.get("kalla_id")),
            senast_uppdaterad=_tom(rad["senast_uppdaterad"]),
        ))
        for n in (1, 2):
            c.execute(
                "UPDATE tickets SET bokad_av=?, typ=?, kund=?, besokt=?, besokare=? "
                "WHERE match_id=? AND nr=?",
                (_tom(rad[f"biljett{n}_bokad_av"]), _tom(rad[f"biljett{n}_typ"]),
                 _tom(rad[f"biljett{n}_kund"]), _tom(rad[f"biljett{n}_besokt"]),
                 _tom(rad[f"biljett{n}_besokare"]), mid, n),
            )
        if _tom(rad["paminnelse_skickad"]):
            c.execute("INSERT INTO reminders_sent (match_id, typ, tidpunkt, status) "
                      "VALUES (?, 'import', ?, 'import')", (mid, rad["paminnelse_skickad"].strip()))
    db.satt_meta(c, "csv_importerad", tid.iso(nu))


def importera_vid_forsta_start(db: Database, sokvag: str | Path, nu: datetime,
                               arena_standard: str) -> int:
    """Importerar CSV om databasen är tom. Idempotent: gör inget om data redan finns."""
    with db.anslut() as c:
        if c.execute("SELECT COUNT(*) FROM matches").fetchone()[0]:
            return 0
    p = Path(sokvag)
    if not p.exists():
        return 0
    return importera(db, p.read_text(encoding="utf-8-sig"), nu, arena_standard)


def exportera(db: Database, utokad: bool = False) -> str:
    """Skriver CSV i startunderlagets format (UTF-8 med BOM, kommaseparerad)."""
    with db.anslut() as c:
        matcher = db.matcher(c)
        paminnelser = db.paminnelse_skickad(c)
    ut = io.StringIO()
    kolumner = KOLUMNER + (EXTRA_KOLUMNER if utokad else [])
    w = csv.DictWriter(ut, fieldnames=kolumner, lineterminator="\n")
    w.writeheader()
    for m in sorted(matcher, key=lambda m: m.match_id):
        rad = {
            "match_id": m.match_id, "datum": m.datum,
            "veckodag": tid.veckodag(date.fromisoformat(m.datum)),
            "tid": m.tid or "", "motstandare": m.motstandare, "arena": m.arena,
            "anmarkning": m.anmarkning or "", "status": m.status,
            "paminnelse_skickad": paminnelser.get(m.match_id, ""),
            "senast_uppdaterad": m.senast_uppdaterad or "",
        }
        for b in m.biljetter:
            for k in ("bokad_av", "typ", "kund", "besokt", "besokare"):
                rad[f"biljett{b.nr}_{k}"] = getattr(b, k) or ""
        if utokad:
            rad["serie"] = m.serie
            rad["kalla_id"] = m.kalla_id or ""
        w.writerow(rad)
    return "﻿" + ut.getvalue()
