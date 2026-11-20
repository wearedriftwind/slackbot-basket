"""Schema- och bokningslogik utan Slack-beroenden.

Alla skrivningar sker i `BEGIN IMMEDIATE`-transaktioner och loggas i audit_log.
Dubbelbokning förhindras dels genom att läsning och skrivning sker i samma
transaktion, dels genom villkoret `bokad_av IS NULL` i själva UPDATE-satsen.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Callable

from . import tid as tidmod
from .config import Config
from .db import BESOKT_VARDEN, STATUSAR, Biljett, Database, Match


# ---- fel -----------------------------------------------------------------

class Fel(Exception):
    """Fel som ska visas för användaren."""

    def __init__(self, meddelande: str):
        super().__init__(meddelande)
        self.meddelande = meddelande


class MatchSaknas(Fel):
    pass


class EjBokningsbar(Fel):
    pass


class Upptagen(Fel):
    def __init__(self, meddelande: str, match: Match, upptagna: list[Biljett]):
        super().__init__(meddelande)
        self.match = match
        self.upptagna = upptagna


class IngenBokning(Fel):
    pass


class Behorighet(Fel):
    pass


class Ogiltigt(Fel):
    pass


# ---- resultat ------------------------------------------------------------

@dataclass
class Bokning:
    match: Match
    biljetter: list[Biljett]


@dataclass
class Avbokning:
    match: Match
    avbokade: list[Biljett]           # så som de såg ut före avbokningen
    andras: bool                      # någon avbokade någon annans biljett
    ledig_notis: bool                 # posta "biljett ledig" i kanalen


@dataclass
class Andring:
    match: Match
    falt: dict[str, tuple] = field(default_factory=dict)  # fält -> (före, efter)


def beskriv_innehavare(b: Biljett) -> str:
    return f"<@{b.bokad_av}> ({b.beskrivning()})"


class BiljettService:
    def __init__(self, db: Database, cfg: Config, klocka: Callable[[], datetime] | None = None):
        self.db = db
        self.cfg = cfg
        self.klocka = klocka or (lambda: datetime.now(cfg.tidszon))

    def nu(self) -> datetime:
        return self.klocka().astimezone(self.cfg.tidszon)

    def ar_admin(self, anvandare: str) -> bool:
        return anvandare in self.cfg.admins

    def _krav_admin(self, aktor: str) -> None:
        if not self.ar_admin(aktor):
            raise Behorighet("Det här får bara administratörer göra.")

    # ---- schema ----------------------------------------------------------

    def uppdatera_spelade(self) -> list[str]:
        """Sätter `spelad` på kommande matcher vars uppkast passerat med
        `spelad_efter_timmar`. Idempotent, körs vid varje tick och kommando."""
        nu = self.nu()
        grans = timedelta(hours=self.cfg.spelad_efter_timmar)
        andrade = []
        with self.db.transaktion() as c:
            for m in self.db.matcher(c):
                if m.status == "kommande" and m.start(self.cfg.tidszon) + grans <= nu:
                    c.execute("UPDATE matches SET status='spelad', senast_uppdaterad=? "
                              "WHERE match_id=? AND status='kommande'", (tidmod.iso(nu), m.match_id))
                    self.db.logga(c, nu, "system", "satt_spelad", m.match_id)
                    andrade.append(m.match_id)
        return andrade

    def matcher(self) -> list[Match]:
        return self.db.matcher()

    def match(self, match_id: str) -> Match:
        m = self.db.match(match_id.upper())
        if m is None:
            raise MatchSaknas(f"Hittar ingen match med id {match_id}.")
        return m

    def kommande(self) -> list[Match]:
        """Kommande (inkl. flyttade utan nytt datum), sorterade på datum."""
        nu = self.nu()
        return [m for m in self.db.matcher()
                if m.status == "flyttad"
                or (m.status == "kommande" and m.start(self.cfg.tidszon) > nu)]

    def nasta(self) -> Match | None:
        return next((m for m in self.kommande() if m.status == "kommande"), None)

    def spelade(self) -> list[Match]:
        return [m for m in self.db.matcher() if m.status == "spelad"]

    # ---- bokning ---------------------------------------------------------

    def boka(self, match_id: str, bokad_av: str, aktor: str, antal: int = 1,
             typ: str = "privat", kund: str | None = None) -> Bokning:
        if antal not in (1, 2):
            raise Ogiltigt("Det finns bara två biljetter – boka 1 eller 2.")
        if typ not in ("privat", "kund"):
            raise Ogiltigt("Typ måste vara privat eller kund.")
        kund = (kund or "").strip() or None
        if typ == "kund" and not kund:
            raise Ogiltigt("Ange kundnamn för kundbokningar, t.ex. `kund: Skellefteå kommun`.")
        if typ == "privat":
            kund = None
        nu = self.nu()
        with self.db.transaktion() as c:
            m = self.db.match(match_id.upper(), c)
            if m is None:
                raise MatchSaknas(f"Hittar ingen match med id {match_id}.")
            self._krav_bokningsbar(m, nu)
            lediga = [b for b in m.biljetter if not b.bokad]
            if len(lediga) < antal:
                upptagna = m.bokade
                vem = ", ".join(f"biljett {b.nr}: {beskriv_innehavare(b)}" for b in upptagna)
                if not lediga:
                    text = f"Båda biljetterna till {m.motstandare} är redan bokade – {vem}."
                else:
                    text = (f"Bara en biljett är ledig till {m.motstandare} – {vem}. "
                            "Boka en biljett i stället?")
                raise Upptagen(text, m, upptagna)
            valda = lediga[:antal]
            for b in valda:
                cur = c.execute(
                    "UPDATE tickets SET bokad_av=?, typ=?, kund=?, besokt=NULL, besokare=NULL, "
                    "skapad_av=?, bokad_tid=? WHERE match_id=? AND nr=? AND bokad_av IS NULL",
                    (bokad_av, typ, kund, aktor, tidmod.iso(nu), m.match_id, b.nr))
                if cur.rowcount != 1:  # kan inte hända inom BEGIN IMMEDIATE, men säkert är säkert
                    raise Upptagen("Biljetten hann bokas av någon annan.", m, m.bokade)
                self.db.logga(c, nu, aktor, "boka", m.match_id, nr=b.nr, bokad_av=bokad_av,
                              typ=typ, kund=kund)
            self._touch(c, m.match_id, nu)
            m2 = self.db.match(m.match_id, c)
        return Bokning(m2, [m2.biljett(b.nr) for b in valda])

    def avboka(self, match_id: str, aktor: str, nr: int | None = None,
               for_anvandare: str | None = None, alla: bool = False) -> Avbokning:
        """Avbokar en specifik biljett (`nr`), alla biljetter (`alla`), eller annars
        de biljetter som `for_anvandare` (standard: aktören) har på matchen."""
        nu = self.nu()
        with self.db.transaktion() as c:
            m = self.db.match(match_id.upper(), c)
            if m is None:
                raise MatchSaknas(f"Hittar ingen match med id {match_id}.")
            if m.status == "spelad":
                raise EjBokningsbar(f"Matchen mot {m.motstandare} är redan spelad och kan inte avbokas. "
                                    "Använd `besök` för att registrera att en biljett inte användes.")
            if nr is not None:
                b = m.biljett(nr)
                if not b.bokad:
                    raise IngenBokning(f"Biljett {nr} till {m.motstandare} är inte bokad.")
                valda = [b]
            elif alla:
                valda = m.bokade
                if not valda:
                    raise IngenBokning(f"Inga biljetter till {m.motstandare} är bokade.")
            else:
                vem = for_anvandare or aktor
                valda = [b for b in m.bokade if b.bokad_av == vem]
                if not valda:
                    ovriga = ", ".join(beskriv_innehavare(b) for b in m.bokade) or "ingen"
                    raise IngenBokning(f"<@{vem}> har ingen biljett till {m.motstandare}. "
                                       f"Bokade: {ovriga}.")
            for b in valda:
                c.execute("UPDATE tickets SET bokad_av=NULL, typ=NULL, kund=NULL, besokt=NULL, "
                          "besokare=NULL, skapad_av=NULL, bokad_tid=NULL WHERE match_id=? AND nr=?",
                          (m.match_id, b.nr))
                self.db.logga(c, nu, aktor, "avboka", m.match_id, nr=b.nr, var_bokad_av=b.bokad_av,
                              typ=b.typ, kund=b.kund)
            self._touch(c, m.match_id, nu)
            m2 = self.db.match(m.match_id, c)
        start = m2.start(self.cfg.tidszon)
        dagar_kvar = (start.date() - nu.date()).days  # kalenderdagar
        ledig_notis = (m2.status == "kommande" and nu < start
                       and dagar_kvar <= self.cfg.avbokning_notis_dagar)
        return Avbokning(m2, valda, any(b.bokad_av != aktor for b in valda), ledig_notis)

    def _krav_bokningsbar(self, m: Match, nu: datetime) -> None:
        if m.status == "inställd":
            raise EjBokningsbar(f"Matchen mot {m.motstandare} är inställd.")
        if m.status == "flyttad":
            raise EjBokningsbar(f"Matchen mot {m.motstandare} är flyttad och har inget nytt datum än.")
        if m.status == "spelad" or m.start(self.cfg.tidszon) <= nu:
            raise EjBokningsbar(f"Matchen mot {m.motstandare} har redan börjat eller spelats.")

    # ---- besök -----------------------------------------------------------

    def registrera_besok(self, match_id: str, nr: int, aktor: str, besokt: str,
                         besokare: str | None = None, bokad_av: str | None = None,
                         typ: str | None = None, kund: str | None = None) -> Match:
        """Fyller i besökshistorik för en spelad (eller påbörjad) match. För matcher
        utan bokning (t.ex. M01–M04) kan bokningsuppgifter fyllas i i efterhand."""
        if besokt not in BESOKT_VARDEN:
            raise Ogiltigt("Besökt måste vara ja eller nej.")
        if nr not in (1, 2):
            raise Ogiltigt("Biljettnummer måste vara 1 eller 2.")
        if typ is not None and typ not in ("privat", "kund"):
            raise Ogiltigt("Typ måste vara privat eller kund.")
        kund = (kund or "").strip() or None
        if typ == "kund" and not kund:
            raise Ogiltigt("Ange kundnamn för kundbokningar.")
        besokare = (besokare or "").strip() or None
        if besokt == "nej":
            besokare = None
        nu = self.nu()
        with self.db.transaktion() as c:
            m = self.db.match(match_id.upper(), c)
            if m is None:
                raise MatchSaknas(f"Hittar ingen match med id {match_id}.")
            if m.status in ("inställd", "flyttad") or (
                    m.status == "kommande" and m.start(self.cfg.tidszon) > nu):
                raise EjBokningsbar(f"Matchen mot {m.motstandare} är inte spelad än.")
            b = m.biljett(nr)
            if besokt == "ja" and not besokare:
                besokare = f"<@{b.bokad_av}>" if b.bokad_av else (f"<@{bokad_av}>" if bokad_av else None)
            ny_bokad_av = bokad_av or b.bokad_av
            ny_typ = typ or b.typ
            ny_kund = kund if typ else b.kund
            if ny_typ != "kund":
                ny_kund = None
            c.execute("UPDATE tickets SET bokad_av=?, typ=?, kund=?, besokt=?, besokare=? "
                      "WHERE match_id=? AND nr=?",
                      (ny_bokad_av, ny_typ, ny_kund, besokt, besokare, m.match_id, nr))
            self.db.logga(c, nu, aktor, "besok", m.match_id, nr=nr, besokt=besokt, besokare=besokare,
                          bokad_av=ny_bokad_av, typ=ny_typ, kund=ny_kund)
            self._touch(c, m.match_id, nu)
            return self.db.match(m.match_id, c)

    # ---- admin: manuell schemaändring -------------------------------------

    def andra_match(self, match_id: str, aktor: str, **falt) -> Andring:
        """Manuell ändring av schemafält (admin). Bokningsfält kan inte ändras här."""
        self._krav_admin(aktor)
        tillatna = {"datum", "tid", "motstandare", "arena", "anmarkning", "status", "serie"}
        okanda = set(falt) - tillatna
        if okanda:
            raise Ogiltigt(f"Kan inte ändra: {', '.join(sorted(okanda))}. Tillåtna: {', '.join(sorted(tillatna))}.")
        self._validera_schemafalt(falt)
        nu = self.nu()
        with self.db.transaktion() as c:
            m = self.db.match(match_id.upper(), c)
            if m is None:
                raise MatchSaknas(f"Hittar ingen match med id {match_id}.")
            andringar = {k: (getattr(m, k), v) for k, v in falt.items() if getattr(m, k) != v}
            for k, (_, v) in andringar.items():
                c.execute(f"UPDATE matches SET {k}=? WHERE match_id=?", (v, m.match_id))
            if andringar:
                self.db.logga(c, nu, aktor, "andra_match", m.match_id,
                              **{k: list(v) for k, v in andringar.items()})
                self._touch(c, m.match_id, nu)
            return Andring(self.db.match(m.match_id, c), andringar)

    def ny_match(self, aktor: str, datum: str, tid: str | None, motstandare: str,
                 serie: str = "grundserie", anmarkning: str = "") -> Match:
        self._krav_admin(aktor)
        self._validera_schemafalt({"datum": datum, "tid": tid})
        if not motstandare.strip():
            raise Ogiltigt("Ange motståndare.")
        nu = self.nu()
        with self.db.transaktion() as c:
            mid = self.db.nasta_match_id(c)
            self.db.skapa_match(c, Match(mid, datum, tid, motstandare.strip(), self.cfg.arena,
                                         anmarkning, "kommande", serie, senast_uppdaterad=tidmod.iso(nu)))
            self.db.logga(c, nu, aktor, "ny_match", mid, datum=datum, tid=tid,
                          motstandare=motstandare, serie=serie)
            return self.db.match(mid, c)

    def radera_match(self, match_id: str, aktor: str, tvinga: bool = False) -> Match:
        self._krav_admin(aktor)
        nu = self.nu()
        with self.db.transaktion() as c:
            m = self.db.match(match_id.upper(), c)
            if m is None:
                raise MatchSaknas(f"Hittar ingen match med id {match_id}.")
            if (m.bokade or any(b.besokt for b in m.biljetter)) and not tvinga:
                raise Ogiltigt(f"{m.match_id} har bokningar eller besökshistorik. "
                               "Sätt hellre status `inställd`, eller lägg till `tvinga`.")
            c.execute("DELETE FROM matches WHERE match_id=?", (m.match_id,))
            self.db.logga(c, nu, aktor, "radera_match", m.match_id, datum=m.datum,
                          motstandare=m.motstandare,
                          biljetter=[b.__dict__ for b in m.biljetter if b.bokad or b.besokt])
            return m

    @staticmethod
    def _validera_schemafalt(falt: dict) -> None:
        if "datum" in falt:
            try:
                date.fromisoformat(falt["datum"])
            except (TypeError, ValueError):
                raise Ogiltigt("Datum ska vara YYYY-MM-DD.") from None
        if falt.get("tid"):
            try:
                time.fromisoformat(falt["tid"])
            except ValueError:
                raise Ogiltigt("Tid ska vara HH:MM.") from None
        if "status" in falt and falt["status"] not in STATUSAR:
            raise Ogiltigt(f"Status måste vara en av: {', '.join(STATUSAR)}.")

    def _touch(self, c, match_id: str, nu: datetime) -> None:
        c.execute("UPDATE matches SET senast_uppdaterad=? WHERE match_id=?", (tidmod.iso(nu), match_id))
