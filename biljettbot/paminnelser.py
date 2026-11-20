"""Påminnelser om lediga biljetter.

Regler (konfigurerbara via PAMINNELSER, standard "0@10:00" = matchdagen kl. 10):
- bara kommande matcher med minst en ledig biljett, och bara före uppkast;
- varje regel skickas högst en gång per match (tabellen reminders_sent);
- har flera regler passerats (t.ex. efter driftstopp eller när en match lagts
  till sent) skickas bara den senaste, de tidigare markeras som hoppade;
- så fort båda biljetterna är bokade skickas inget mer.

Idempotens: en påminnelse reserveras i databasen (status 'skickar') *innan* den
postas och markeras 'skickad' efteråt. Kraschar boten mitt emellan skickas den
inte igen (hellre en missad än en dubbel). Misslyckas själva postningen tas
reservationen bort så att nästa körning försöker igen.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable

from . import format as fmt
from .config import Paminnelseregel
from .db import Match
from .service import BiljettService
from .tid import iso

log = logging.getLogger(__name__)


@dataclass
class Forfallen:
    match: Match
    regel: Paminnelseregel
    hoppade: list[Paminnelseregel] = field(default_factory=list)


def regelns_tidpunkt(m: Match, r: Paminnelseregel, tz) -> datetime:
    dag = date.fromisoformat(m.datum) - timedelta(days=r.dagar_fore)
    return datetime.combine(dag, r.klockslag, tzinfo=tz)


def forfallna(matcher: list[Match], skickade: set[tuple[str, str]], regler: tuple[Paminnelseregel, ...],
              nu: datetime, tz) -> list[Forfallen]:
    ut = []
    for m in matcher:
        if m.status != "kommande" or not m.lediga or not m.tid:
            continue
        if nu >= m.start(tz):
            continue
        passerade = sorted(
            (r for r in regler
             if regelns_tidpunkt(m, r, tz) <= nu and (m.match_id, r.nyckel) not in skickade),
            key=lambda r: regelns_tidpunkt(m, r, tz))
        if passerade:
            ut.append(Forfallen(m, passerade[-1], passerade[:-1]))
    return ut


Postare = Callable[[str, list[dict]], None]


class Paminnare:
    def __init__(self, svc: BiljettService, posta: Postare):
        self.svc = svc
        self.posta = posta

    def kor(self) -> list[str]:
        """Skickar förfallna påminnelser. Returnerar match-id:n som fick påminnelse."""
        cfg = self.svc.cfg
        db = self.svc.db
        nu = self.svc.nu()
        with db.anslut() as c:
            skickade = {(r[0], r[1]) for r in c.execute("SELECT match_id, typ FROM reminders_sent")}
        skickat = []
        for f in forfallna(db.matcher(), skickade, cfg.paminnelser, nu, cfg.tidszon):
            mid, typ = f.match.match_id, f.regel.nyckel
            with db.transaktion() as c:
                if c.execute("SELECT 1 FROM reminders_sent WHERE match_id=? AND typ=?", (mid, typ)).fetchone():
                    continue  # någon annan körning hann före
                m = db.match(mid, c)
                if not m or not m.lediga or m.status != "kommande":
                    continue
                c.execute("INSERT INTO reminders_sent (match_id, typ, tidpunkt, status) VALUES (?,?,?,'skickar')",
                          (mid, typ, iso(nu)))
                for h in f.hoppade:
                    c.execute("INSERT OR IGNORE INTO reminders_sent (match_id, typ, tidpunkt, status) "
                              "VALUES (?,?,?,'hoppad')", (mid, h.nyckel, iso(nu)))
            text, blocks = fmt.paminnelse(m, fmt.paminnelse_rubrik(m, nu))
            try:
                self.posta(text, blocks)
            except Exception:
                log.exception("Påminnelse för %s kunde inte postas – försöker igen senare", mid)
                with db.transaktion() as c:
                    c.execute("DELETE FROM reminders_sent WHERE match_id=? AND typ=? AND status='skickar'",
                              (mid, typ))
                continue
            with db.transaktion() as c:
                c.execute("UPDATE reminders_sent SET status='skickad', tidpunkt=? WHERE match_id=? AND typ=?",
                          (iso(self.svc.nu()), mid, typ))
                db.logga(c, self.svc.nu(), "system", "paminnelse", mid, typ=typ, lediga=len(m.lediga),
                         hoppade=[h.nyckel for h in f.hoppade])
            skickat.append(mid)
        return skickat
