"""SQLite-lagring.

Varför SQLite: en fil, ingen separat server, ACID-transaktioner. Med WAL och
`BEGIN IMMEDIATE` serialiseras skrivningar, så två samtidiga bokningar kan inte
skriva över varandra. Varje operation öppnar en egen anslutning, vilket gör
lagret trådsäkert (Bolt-handlers och schemalagda jobb körs i olika trådar).
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterator
from zoneinfo import ZoneInfo

from . import tid

STATUSAR = ("kommande", "spelad", "inställd", "flyttad")
BOKNINGSTYPER = ("privat", "kund")
BESOKT_VARDEN = ("ja", "nej")

SCHEMA = """
CREATE TABLE IF NOT EXISTS matches (
    match_id          TEXT PRIMARY KEY,
    datum             TEXT NOT NULL,                 -- YYYY-MM-DD
    tid               TEXT,                          -- HH:MM, lokal tid
    motstandare       TEXT NOT NULL,
    arena             TEXT NOT NULL,
    anmarkning        TEXT NOT NULL DEFAULT '',
    status            TEXT NOT NULL DEFAULT 'kommande'
                      CHECK (status IN ('kommande','spelad','inställd','flyttad')),
    serie             TEXT NOT NULL DEFAULT 'grundserie',
    kalla_id          TEXT UNIQUE,                   -- data-id på luleabasket.com
    kalla_anmarkning  TEXT,                          -- anmärkning enligt källsidan
    saknad_i_synk     INTEGER NOT NULL DEFAULT 0,    -- antal synkar i rad där matchen saknats
    senast_uppdaterad TEXT
);

CREATE TABLE IF NOT EXISTS tickets (
    match_id  TEXT NOT NULL REFERENCES matches(match_id) ON DELETE CASCADE,
    nr        INTEGER NOT NULL CHECK (nr IN (1, 2)),
    bokad_av  TEXT,                                  -- Slack-användar-ID
    typ       TEXT CHECK (typ IN ('privat','kund')),
    kund      TEXT,
    besokt    TEXT CHECK (besokt IN ('ja','nej')),
    besokare  TEXT,
    skapad_av TEXT,                                  -- vem som utförde bokningen
    bokad_tid TEXT,
    PRIMARY KEY (match_id, nr),
    CHECK (kund IS NULL OR typ = 'kund'),
    CHECK (typ IS NOT 'kund' OR (kund IS NOT NULL AND kund <> ''))
);

CREATE TABLE IF NOT EXISTS reminders_sent (
    match_id TEXT NOT NULL REFERENCES matches(match_id) ON DELETE CASCADE,
    typ      TEXT NOT NULL,                          -- t.ex. d0@10:00
    tidpunkt TEXT NOT NULL,
    status   TEXT NOT NULL CHECK (status IN ('skickar','skickad','hoppad','import')),
    PRIMARY KEY (match_id, typ)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    tid      TEXT NOT NULL,
    aktor    TEXT NOT NULL,                          -- Slack-ID eller 'system'/'synk'/'import'
    handling TEXT NOT NULL,
    match_id TEXT,
    detaljer TEXT                                    -- JSON
);

CREATE TABLE IF NOT EXISTS sync_runs (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    start     TEXT NOT NULL,
    slut      TEXT,
    utfall    TEXT,                                  -- ok | inga_andringar | avbruten | fel
    hittade   INTEGER,
    detaljer  TEXT
);

CREATE TABLE IF NOT EXISTS meta (
    nyckel TEXT PRIMARY KEY,
    varde  TEXT
);
"""


@dataclass
class Biljett:
    match_id: str
    nr: int
    bokad_av: str | None = None
    typ: str | None = None
    kund: str | None = None
    besokt: str | None = None
    besokare: str | None = None
    skapad_av: str | None = None
    bokad_tid: str | None = None

    @property
    def bokad(self) -> bool:
        return bool(self.bokad_av)

    def beskrivning(self) -> str:
        if self.typ == "kund":
            return f"kund: {self.kund}"
        return self.typ or "okänd typ"


@dataclass
class Match:
    match_id: str
    datum: str
    tid: str | None
    motstandare: str
    arena: str
    anmarkning: str = ""
    status: str = "kommande"
    serie: str = "grundserie"
    kalla_id: str | None = None
    kalla_anmarkning: str | None = None
    saknad_i_synk: int = 0
    senast_uppdaterad: str | None = None
    biljetter: list[Biljett] = field(default_factory=list)

    def start(self, tz: ZoneInfo) -> datetime:
        return tid.matchstart(self.datum, self.tid, tz)

    @property
    def lediga(self) -> list[Biljett]:
        if self.status != "kommande":
            return []
        return [b for b in self.biljetter if not b.bokad]

    @property
    def bokade(self) -> list[Biljett]:
        return [b for b in self.biljetter if b.bokad]

    def biljett(self, nr: int) -> Biljett:
        return next(b for b in self.biljetter if b.nr == nr)


class Database:
    def __init__(self, sokvag: str | Path):
        self.sokvag = str(sokvag)
        if self.sokvag != ":memory:":
            Path(self.sokvag).parent.mkdir(parents=True, exist_ok=True)
        with self.anslut() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(SCHEMA)

    @contextmanager
    def anslut(self) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(self.sokvag, timeout=30, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA busy_timeout=30000")
        try:
            yield c
        finally:
            c.close()

    @contextmanager
    def transaktion(self) -> Iterator[sqlite3.Connection]:
        """Skrivtransaktion. BEGIN IMMEDIATE tar skrivlåset direkt, så läs-sedan-skriv
        inom transaktionen är fri från kapplöpning."""
        with self.anslut() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                yield c
            except BaseException:
                c.execute("ROLLBACK")
                raise
            else:
                c.execute("COMMIT")

    # ---- läsning -------------------------------------------------------

    def matcher(self, c: sqlite3.Connection | None = None) -> list[Match]:
        if c is None:
            with self.anslut() as c2:
                return self.matcher(c2)
        rader = c.execute("SELECT * FROM matches ORDER BY datum, tid, match_id").fetchall()
        biljetter: dict[str, list[Biljett]] = {}
        for r in c.execute("SELECT * FROM tickets ORDER BY match_id, nr"):
            biljetter.setdefault(r["match_id"], []).append(Biljett(**dict(r)))
        return [Match(**dict(r), biljetter=biljetter.get(r["match_id"], [])) for r in rader]

    def match(self, match_id: str, c: sqlite3.Connection | None = None) -> Match | None:
        if c is None:
            with self.anslut() as c2:
                return self.match(match_id, c2)
        r = c.execute("SELECT * FROM matches WHERE match_id = ?", (match_id,)).fetchone()
        if r is None:
            return None
        b = [Biljett(**dict(x)) for x in c.execute(
            "SELECT * FROM tickets WHERE match_id = ? ORDER BY nr", (match_id,))]
        return Match(**dict(r), biljetter=b)

    def paminnelse_skickad(self, c: sqlite3.Connection | None = None) -> dict[str, str]:
        """Senaste påminnelsetidpunkt per match (motsvarar CSV-kolumnen)."""
        if c is None:
            with self.anslut() as c2:
                return self.paminnelse_skickad(c2)
        return {r[0]: r[1] for r in c.execute(
            "SELECT match_id, MAX(tidpunkt) FROM reminders_sent "
            "WHERE status IN ('skickad','import') GROUP BY match_id")}

    def audit(self, n: int = 20) -> list[sqlite3.Row]:
        with self.anslut() as c:
            return c.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (n,)).fetchall()

    def meta(self, nyckel: str) -> str | None:
        with self.anslut() as c:
            r = c.execute("SELECT varde FROM meta WHERE nyckel = ?", (nyckel,)).fetchone()
            return r[0] if r else None

    # ---- skrivning (anropas inom transaktion) ---------------------------

    @staticmethod
    def logga(c: sqlite3.Connection, nu: datetime, aktor: str, handling: str,
              match_id: str | None = None, **detaljer) -> None:
        c.execute(
            "INSERT INTO audit_log (tid, aktor, handling, match_id, detaljer) VALUES (?,?,?,?,?)",
            (tid.iso(nu), aktor, handling, match_id,
             json.dumps(detaljer, ensure_ascii=False) if detaljer else None),
        )

    @staticmethod
    def satt_meta(c: sqlite3.Connection, nyckel: str, varde: str) -> None:
        c.execute("INSERT INTO meta (nyckel, varde) VALUES (?, ?) "
                  "ON CONFLICT(nyckel) DO UPDATE SET varde = excluded.varde", (nyckel, varde))

    @staticmethod
    def nasta_match_id(c: sqlite3.Connection) -> str:
        ids = [r[0] for r in c.execute("SELECT match_id FROM matches")]
        nummer = [int(i[1:]) for i in ids if i[:1] == "M" and i[1:].isdigit()]
        return f"M{(max(nummer, default=0) + 1):02d}"

    @staticmethod
    def skapa_match(c: sqlite3.Connection, m: Match) -> None:
        c.execute(
            "INSERT INTO matches (match_id, datum, tid, motstandare, arena, anmarkning, status, "
            "serie, kalla_id, kalla_anmarkning, saknad_i_synk, senast_uppdaterad) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (m.match_id, m.datum, m.tid, m.motstandare, m.arena, m.anmarkning or "", m.status,
             m.serie, m.kalla_id, m.kalla_anmarkning, m.saknad_i_synk, m.senast_uppdaterad),
        )
        for nr in (1, 2):
            c.execute("INSERT INTO tickets (match_id, nr) VALUES (?, ?)", (m.match_id, nr))
