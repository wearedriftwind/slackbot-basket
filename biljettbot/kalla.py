"""Hämtning och parsning av luleabasket.com/matcher.

Sidan har inget API. Strukturen (2026-11) är:

    <div class="matchContainer">
      <h4 class="matchHeadline">Oktober 2026</h4>
      <div class="matchesLine" data-id="4503">
        <div class="matchesDateDiv">03 Lör 18:00</div>          (ibland 18.00)
        <div class="matchesDateTeams">Luleå Basket - Visby Ladies | PREMIÄREN</div>
        <div class="matchesDateLink">105 - 74</div>
      ...

Parsern är strikt: hittar den inte strukturen kastas `ParserFel` med en
förklaring, så att synken avbryts och varnar i Slack i stället för att
skriva över data med skräp.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

import httpx
from bs4 import BeautifulSoup

from .nlp import vik

MANADER = {"januari": 1, "februari": 2, "mars": 3, "april": 4, "maj": 5, "juni": 6, "juli": 7,
           "augusti": 8, "september": 9, "oktober": 10, "november": 11, "december": 12}
VECKODAG_KORT = ["man", "tis", "ons", "tor", "fre", "lor", "son"]
INSTALLD = re.compile(r"inst[aä]lld|avbruten|struken", re.I)
FLYTTAD = re.compile(r"flyttad|uppskjuten|ny tid|nytt datum|\btba\b|\bprel", re.I)


class ParserFel(Exception):
    pass


class HamtningsFel(Exception):
    pass


@dataclass
class KallMatch:
    kalla_id: str | None
    datum: str
    tid: str | None
    hemmalag: str
    bortalag: str
    anmarkning: str | None
    resultat: str | None
    status_hint: str | None  # "inställd" | "flyttad" | None
    hemma: bool


@dataclass
class Parsning:
    matcher: list[KallMatch]
    varningar: list[str] = field(default_factory=list)

    @property
    def hemma(self) -> list[KallMatch]:
        return [m for m in self.matcher if m.hemma]


def hamta(url: str, timeout: float = 20.0) -> str:
    try:
        r = httpx.get(url, timeout=timeout, follow_redirects=True,
                      headers={"User-Agent": "Mozilla/5.0 (compatible; biljettbot/1.0)"})
    except httpx.HTTPError as e:
        raise HamtningsFel(f"Kunde inte hämta {url}: {e.__class__.__name__}: {e}") from e
    if r.status_code != 200:
        raise HamtningsFel(f"{url} svarade med HTTP {r.status_code}")
    if len(r.text) < 1000:
        raise HamtningsFel(f"{url} returnerade nästan tom sida ({len(r.text)} tecken)")
    return r.text


def _normalisera_lag(namn: str) -> str:
    return re.sub(r"\s+", " ", vik(namn)).strip()


def parsa(html: str, hemmalag: str = "Luleå Basket") -> Parsning:
    soup = BeautifulSoup(html, "html.parser")
    containers = soup.select(".matchContainer")
    if not containers:
        raise ParserFel("Hittar inte `.matchContainer` på sidan – layouten har troligen ändrats.")
    hem = _normalisera_lag(hemmalag)
    ut: list[KallMatch] = []
    varningar: list[str] = []
    for container in containers:
        manad = ar = None
        for el in container.find_all(["h4", "div"]):
            klasser = el.get("class") or []
            if el.name == "h4" and "matchHeadline" in klasser:
                rubrik = el.get_text(" ", strip=True)
                m = re.fullmatch(r"([A-Za-zÅÄÖåäö]+)\s+(\d{4})", rubrik)
                if not m or m[1].lower() not in MANADER:
                    raise ParserFel(f"Okänd månadsrubrik: {rubrik!r}")
                manad, ar = MANADER[m[1].lower()], int(m[2])
            elif el.name == "div" and "matchesLine" in klasser:
                if manad is None:
                    raise ParserFel("Matchrad före första månadsrubriken – layouten har ändrats.")
                ut.append(_parsa_rad(el, manad, ar, hem, varningar))
    if not ut:
        raise ParserFel("Inga matchrader (`.matchesLine`) hittades på sidan.")
    p = Parsning(ut, varningar)
    if not p.hemma:
        raise ParserFel(f"Hittade {len(ut)} matcher men ingen hemmamatch för {hemmalag}.")
    return p


def _parsa_rad(el, manad: int, ar: int, hem: str, varningar: list[str]) -> KallMatch:
    def text(klass: str) -> str | None:
        x = el.select_one(f".{klass}")
        return x.get_text(" ", strip=True) if x else None

    datumtext = text("matchesDateDiv")
    lagtext = text("matchesDateTeams")
    if not datumtext or not lagtext:
        raise ParserFel(f"Matchrad saknar datum eller lag: {el.get_text(' ', strip=True)[:80]!r}")
    m = re.match(r"^(\d{1,2})\s+([A-Za-zÅÄÖåäö]{2,4})\.?(?:\s+(\d{1,2})[:.](\d{2}))?", datumtext)
    if not m:
        raise ParserFel(f"Kan inte tolka datum/tid {datumtext!r}")
    try:
        d = date(ar, manad, int(m[1]))
    except ValueError:
        raise ParserFel(f"Ogiltigt datum {datumtext!r} under {manad}/{ar}") from None
    tid = f"{int(m[3]):02d}:{m[4]}" if m[3] else None
    vd = vik(m[2])[:3]
    if vd in VECKODAG_KORT and VECKODAG_KORT.index(vd) != d.weekday():
        varningar.append(f"Veckodagen stämmer inte för {datumtext!r} ({d.isoformat()})")

    lag, _, anm = lagtext.partition("|")
    delar = re.split(r"\s+[-–]\s+", lag.strip(), maxsplit=1)
    if len(delar) != 2:
        raise ParserFel(f"Kan inte dela lagnamn {lagtext!r}")
    hemmalag, bortalag = (x.strip() for x in delar)
    hemma = _normalisera_lag(hemmalag) == hem
    if not hemma and _normalisera_lag(bortalag) != hem:
        varningar.append(f"Rad utan hemmalaget: {lagtext!r}")

    heltext = el.get_text(" ", strip=True)
    hint = "inställd" if INSTALLD.search(heltext) else "flyttad" if FLYTTAD.search(heltext) else None
    if tid is None and hint is None:
        varningar.append(f"Ingen tid för {d.isoformat()} {lagtext!r}")
    return KallMatch(
        kalla_id=el.get("data-id"),
        datum=d.isoformat(), tid=tid,
        hemmalag=hemmalag, bortalag=bortalag,
        anmarkning=anm.strip() or None,
        resultat=text("matchesDateLink") or None,
        status_hint=hint, hemma=hemma,
    )
