"""Schemasynk mot luleabasket.com/matcher.

Flöde: hämta → parsa → validera → diff mot lagrade matcher → applicera i en
transaktion → posta diff i Slack (bara om något ändrats). Vid fel postas en
varning och inget skrivs.

Regler:
- Matchning: källans data-id (kalla_id) → datum+motståndare → motståndare
  (om unik på båda sidor). Matcher som inte matchas läggs till som nya.
- Bokningsfälten (tabellen tickets) rörs aldrig.
- Spelade matcher ändras inte (bara kopplas till kalla_id).
- Ändrat datum/tid uppdateras på plats; matchen behåller sitt match_id och
  sina bokningar, och påminnelser för det gamla datumet nollställs.
- Status från sidan (”inställd”, ”flyttad/uppskjuten”) förs över.
- En kommande match som saknas på sidan räknas upp och sätts till `inställd`
  först när den saknats SYNK_SAKNAD_GRANS synkar i rad.
- Ändringar som berör bokade biljetter postas separat med @-omnämnanden.
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable

from . import format as fmt
from .config import Config
from .db import Match
from .kalla import HamtningsFel, KallMatch, Parsning, ParserFel, parsa
from .kommandon import Notis
from .nlp import vik
from .service import BiljettService
from .tid import iso, kort_datum

log = logging.getLogger(__name__)

SLUTSPEL_ORD = ("slutspel", "kvartsfinal", "semifinal", "final", "sm-final")


@dataclass
class Andring:
    typ: str            # ny | andrad | installd | flyttad | aterinsatt | saknas | kopplad
    match_id: str
    fore: Match | None = None
    kall: KallMatch | None = None
    falt: dict[str, tuple] = field(default_factory=dict)   # synliga fältändringar
    tyst: dict[str, object] = field(default_factory=dict)  # interna fält (kalla_id m.m.)
    ny: Match | None = None

    @property
    def synlig(self) -> bool:
        return self.typ != "kopplad"

    @property
    def berorda(self) -> list[str]:
        """Slack-ID:n med bokade biljetter på matchen."""
        if self.fore is None or self.fore.status == "spelad":
            return []
        return sorted({b.bokad_av for b in self.fore.bokade})


@dataclass
class SynkPlan:
    andringar: list[Andring]
    varningar: list[str]
    hittade: int
    avbryt: str | None = None

    @property
    def synliga(self) -> list[Andring]:
        return [a for a in self.andringar if a.synlig]


@dataclass
class SynkResultat:
    utfall: str                     # ok | inga_andringar | avbruten | fel
    text: str
    notiser: list[Notis] = field(default_factory=list)
    plan: SynkPlan | None = None


def _lag(namn: str) -> str:
    return " ".join(vik(namn).split())


def planera(lagrade: list[Match], parsning: Parsning, nu: datetime, cfg: Config) -> SynkPlan:
    kalla = parsning.hemma
    tz = cfg.tidszon
    andringar: list[Andring] = []
    oparade_k = list(kalla)
    oparade_m = list(lagrade)
    par: list[tuple[Match, KallMatch]] = []

    def para(m: Match, k: KallMatch) -> None:
        par.append((m, k))
        oparade_m.remove(m)
        oparade_k.remove(k)

    # 1. kalla_id
    for k in list(oparade_k):
        m = next((m for m in oparade_m if k.kalla_id and m.kalla_id == k.kalla_id), None)
        if m:
            para(m, k)
    # 2. datum + motståndare
    for k in list(oparade_k):
        m = next((m for m in oparade_m if m.datum == k.datum and _lag(m.motstandare) == _lag(k.bortalag)), None)
        if m:
            para(m, k)
    # 3. motståndare, om unik bland de oparade på båda sidor (fångar flyttat datum)
    for k in list(oparade_k):
        km = [m for m in oparade_m if _lag(m.motstandare) == _lag(k.bortalag) and m.status != "spelad"]
        kk = [x for x in oparade_k if _lag(x.bortalag) == _lag(k.bortalag)]
        if len(km) == 1 and len(kk) == 1:
            para(km[0], k)

    grans_spelad = timedelta(hours=cfg.spelad_efter_timmar)

    for m, k in par:
        tyst: dict[str, object] = {}
        if m.kalla_id != k.kalla_id and k.kalla_id:
            tyst["kalla_id"] = k.kalla_id
        if (m.kalla_anmarkning or None) != k.anmarkning:
            tyst["kalla_anmarkning"] = k.anmarkning
        if m.saknad_i_synk:
            tyst["saknad_i_synk"] = 0
        if m.status == "spelad":
            if tyst:
                andringar.append(Andring("kopplad", m.match_id, m, k, tyst=tyst))
            continue
        falt: dict[str, tuple] = {}
        if m.datum != k.datum:
            falt["datum"] = (m.datum, k.datum)
        if k.tid and m.tid != k.tid:
            falt["tid"] = (m.tid, k.tid)
        if _lag(m.motstandare) != _lag(k.bortalag):
            falt["motstandare"] = (m.motstandare, k.bortalag)
        typ = "andrad"
        if k.status_hint == "inställd" and m.status != "inställd":
            falt["status"] = (m.status, "inställd")
            typ = "installd"
        elif k.status_hint == "flyttad" and m.status != "flyttad":
            falt["status"] = (m.status, "flyttad")
            typ = "flyttad"
        elif k.status_hint is None and (m.status == "flyttad" or
                                        (m.status == "inställd" and m.saknad_i_synk >= cfg.synk_saknad_grans)):
            falt["status"] = (m.status, "kommande")
            typ = "aterinsatt"
        if falt:
            andringar.append(Andring(typ, m.match_id, m, k, falt=falt, tyst=tyst))
        elif tyst:
            andringar.append(Andring("kopplad", m.match_id, m, k, tyst=tyst))

    # Lagrade matcher som saknas på sidan
    for m in oparade_m:
        if m.status not in ("kommande", "flyttad"):
            continue
        if m.status == "kommande" and m.start(tz) <= nu:
            continue  # redan spelad/pågår – blir `spelad` av tick
        n = m.saknad_i_synk + 1
        if n >= cfg.synk_saknad_grans:
            andringar.append(Andring("installd", m.match_id, m, falt={"status": (m.status, "inställd")},
                                     tyst={"saknad_i_synk": n}))
        else:
            andringar.append(Andring("saknas", m.match_id, m, tyst={"saknad_i_synk": n}))

    # Nya matcher
    grundserie_slut = max((m.datum for m in lagrade if m.serie == "grundserie"), default="0000")
    for k in oparade_k:
        anm = (k.anmarkning or "")
        serie = "slutspel" if (k.datum > grundserie_slut or any(o in vik(anm) for o in SLUTSPEL_ORD)) \
            else "grundserie"
        start = datetime.combine(date.fromisoformat(k.datum),
                                 datetime.strptime(k.tid or "00:00", "%H:%M").time(), tzinfo=tz)
        status = k.status_hint or ("spelad" if start + grans_spelad <= nu else "kommande")
        ny = Match("", k.datum, k.tid, k.bortalag, cfg.arena, anm, status, serie, k.kalla_id, k.anmarkning)
        andringar.append(Andring("ny", "", None, k, ny=ny))

    # Validering
    avbryt = None
    forvantade = sum(1 for m in lagrade if m.status != "inställd")
    if forvantade and len(kalla) < forvantade * (1 - cfg.synk_max_minskning):
        avbryt = (f"Hittade bara {len(kalla)} hemmamatcher på sidan men {forvantade} finns lagrade "
                  f"(mer än {cfg.synk_max_minskning:.0%} färre).")
    nya = sum(1 for a in andringar if a.typ == "ny")
    if nya > cfg.synk_max_nya:
        avbryt = f"Sidan ger {nya} nya matcher (max {cfg.synk_max_nya} per synk) – ny säsong eller ändrad sida?"
    installda = sum(1 for a in andringar if a.typ == "installd" and a.kall is None)
    if installda > 3:
        avbryt = f"{installda} matcher skulle sättas till inställda på en gång – kontrollera sidan."
    return SynkPlan(andringar, list(parsning.varningar), len(kalla), avbryt)


def applicera(svc: BiljettService, plan: SynkPlan, aktor: str) -> None:
    """Skriver planen i en transaktion. Rör aldrig tabellen tickets."""
    db = svc.db
    nu = svc.nu()
    with db.transaktion() as c:
        for a in plan.andringar:
            if a.typ == "ny":
                a.ny.match_id = db.nasta_match_id(c)
                a.ny.senast_uppdaterad = iso(nu)
                db.skapa_match(c, a.ny)
                a.match_id = a.ny.match_id
                db.logga(c, nu, aktor, "synk_ny", a.match_id, datum=a.ny.datum, tid=a.ny.tid,
                         motstandare=a.ny.motstandare, serie=a.ny.serie, kalla_id=a.ny.kalla_id)
                continue
            uppd = {k: v[1] for k, v in a.falt.items()}
            uppd.update(a.tyst)
            if not uppd:
                continue
            satt = ", ".join(f"{k}=?" for k in uppd)
            varden = list(uppd.values())
            if a.falt:
                satt += ", senast_uppdaterad=?"
                varden.append(iso(nu))
            c.execute(f"UPDATE matches SET {satt} WHERE match_id=?", (*varden, a.match_id))
            if "datum" in a.falt or "tid" in a.falt:
                # nytt datum → påminnelser ska kunna skickas igen för det nya datumet
                c.execute("DELETE FROM reminders_sent WHERE match_id=? AND typ <> 'import'", (a.match_id,))
            if a.falt or a.typ == "saknas":
                db.logga(c, nu, aktor, f"synk_{a.typ}", a.match_id,
                         **{k: list(v) for k, v in a.falt.items()}, **{k: v for k, v in a.tyst.items()})


# ---- meddelanden ----------------------------------------------------------------

def _datumtid(datum: str | None, tid: str | None) -> str:
    return f"{kort_datum(datum)} {tid or ''}".strip() if datum else "?"


def beskriv(a: Andring) -> str:
    if a.typ == "ny":
        m = a.ny
        extra = f" ({m.serie})" if m.serie != "grundserie" else ""
        anm = f" · _{m.anmarkning}_" if m.anmarkning else ""
        return f"➕ Ny match `{m.match_id}`: {_datumtid(m.datum, m.tid)} – *{m.motstandare}*{extra}{anm}"
    m = a.fore
    namn = f"`{m.match_id}` *{m.motstandare}* ({_datumtid(m.datum, m.tid)})"
    if a.typ == "saknas":
        return f"⚠️ {namn} saknas på sidan – sätts till inställd om den saknas även nästa gång."
    if a.typ == "installd":
        orsak = "markerad som inställd på sidan" if a.kall else "saknas på sidan sedan flera synkar"
        return f"❌ {namn} är nu *inställd* ({orsak})."
    delar = []
    if "datum" in a.falt or "tid" in a.falt:
        ny_datum = a.falt.get("datum", (None, m.datum))[1]
        ny_tid = a.falt.get("tid", (None, m.tid))[1]
        delar.append(f"flyttas till *{_datumtid(ny_datum, ny_tid)}*")
    if "motstandare" in a.falt:
        delar.append(f"motståndare {a.falt['motstandare'][0]} → *{a.falt['motstandare'][1]}*")
    if a.typ == "flyttad":
        delar.append("*flyttad* – nytt datum inte klart")
    if a.typ == "aterinsatt":
        delar.append("är tillbaka i schemat (*kommande*)")
    ikon = {"flyttad": "⏸️", "aterinsatt": "↩️"}.get(a.typ, "✏️")
    return f"{ikon} {namn}: " + ", ".join(delar)


def meddelanden(plan: SynkPlan, url: str) -> list[Notis]:
    synliga = plan.synliga
    if not synliga:
        return []
    rader = [beskriv(a) for a in synliga]
    text = "🔄 *Schemat uppdaterat från luleabasket.com*\n" + "\n".join(rader)
    if plan.varningar:
        text += "\n_Varningar: " + "; ".join(plan.varningar[:5]) + "_"
    blocks = [fmt.sektion(text), fmt.kontext(f"Källa: <{url}|{url}> · `/biljetter säsong` visar hela schemat")]
    ut = [Notis(text, blocks)]
    for a in synliga:
        if a.berorda and a.typ != "saknas":
            vem = " ".join(f"<@{u}>" for u in a.berorda)
            om = "Biljetterna ligger kvar." if a.typ not in ("installd",) else \
                "Matchen är inställd – bokningen ligger kvar tills vidare."
            t = (f"⚠️ *Ändring för en match där du har biljett* {vem}\n{beskriv(a)}\n{om} "
                 f"Kan du inte gå längre? `/biljetter avboka {a.match_id}`")
            ut.append(Notis(t, [fmt.sektion(t)]))
    return ut


# ---- körning -----------------------------------------------------------------------

class Synkare:
    def __init__(self, svc: BiljettService, hamta: Callable[[str], str]):
        self.svc = svc
        self.hamta = hamta
        self._las = threading.Lock()

    def kor(self, aktor: str = "synk", tvinga: bool = False) -> SynkResultat:
        if not self._las.acquire(blocking=False):
            return SynkResultat("avbruten", "En synk pågår redan – försök igen om en stund.")
        try:
            return self._kor_synk(aktor, tvinga)
        finally:
            self._las.release()

    def _kor_synk(self, aktor: str, tvinga: bool) -> SynkResultat:
        cfg = self.svc.cfg
        db = self.svc.db
        start = self.svc.nu()
        with db.transaktion() as c:
            run_id = c.execute("INSERT INTO sync_runs (start) VALUES (?)", (iso(start),)).lastrowid

        def avsluta(utfall: str, hittade: int | None, detaljer: dict) -> None:
            with db.transaktion() as c:
                c.execute("UPDATE sync_runs SET slut=?, utfall=?, hittade=?, detaljer=? WHERE id=?",
                          (iso(self.svc.nu()), utfall, hittade, json.dumps(detaljer, ensure_ascii=False), run_id))

        try:
            self.svc.uppdatera_spelade()
            parsning = parsa(self.hamta(cfg.schema_url), cfg.hemmalag)
            plan = planera(db.matcher(), parsning, self.svc.nu(), cfg)
        except (HamtningsFel, ParserFel) as e:
            avsluta("fel", None, {"fel": str(e)})
            return self._varning(str(e))
        except Exception as e:  # oväntat – varna hellre än att tyst misslyckas
            log.exception("Oväntat fel i synk")
            avsluta("fel", None, {"fel": repr(e)})
            return self._varning(f"Oväntat fel: {e!r}")

        if plan.avbryt and not tvinga:
            avsluta("avbruten", plan.hittade, {"orsak": plan.avbryt})
            return self._varning(plan.avbryt + " Kör `/biljetter admin synka tvinga` om ändringen är korrekt.",
                                 plan)

        applicera(self.svc, plan, aktor)
        notiser = meddelanden(plan, cfg.schema_url)
        utfall = "ok" if plan.synliga else "inga_andringar"
        avsluta(utfall, plan.hittade, {"andringar": [beskriv(a) for a in plan.synliga],
                                      "varningar": plan.varningar})
        if not plan.synliga:
            return SynkResultat(utfall, f"✅ Schemat är oförändrat ({plan.hittade} hemmamatcher på sidan).",
                                plan=plan)
        return SynkResultat(utfall, f"✅ Synk klar: {len(plan.synliga)} ändring(ar) – se biljettkanalen.",
                            notiser, plan)

    def _varning(self, orsak: str, plan: SynkPlan | None = None) -> SynkResultat:
        url = self.svc.cfg.schema_url
        text = (f"🚨 *Schemasynken avbröts*: {orsak}\nInga matcher har ändrats. "
                f"Kontrollera <{url}|{url}> – parsern kan behöva uppdateras om sidan har ändrats.")
        return SynkResultat("avbruten" if plan else "fel", text.replace("*", ""),
                            [Notis(text, [fmt.sektion(text)])], plan)


def synk_kommando(svc: BiljettService, synkare: Synkare, anv: str, tvinga: bool) -> "Svar":
    """Manuell synk från Slack (`/biljetter synka`, `/biljetter admin synka tvinga`)."""
    from .kommandon import Svar
    if tvinga and not svc.ar_admin(anv):
        return Svar("⚠️ Bara administratörer kan tvinga en synk.")
    r = synkare.kor(aktor=anv, tvinga=tvinga)
    return Svar(r.text, [fmt.sektion(r.text)], notiser=r.notiser)
