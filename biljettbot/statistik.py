"""Besökshistorik och statistik."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from .db import Match


@dataclass
class Statistik:
    spelade: int
    besokta_matcher: list[tuple[Match, int]]       # (match, antal använda biljetter)
    anvanda: int
    oanvanda: int
    ej_ifyllda: int                                # bokade biljetter på spelade matcher utan besöksuppgift
    besokare: list[tuple[str, int]]
    bokare: list[tuple[str, int]]
    privat: int
    kund: int
    kunder: list[tuple[str, int]]
    saknar_historik: list[Match]                   # spelade matcher där någon biljett saknar uppgift


def _gruppera(namn: list[str]) -> list[tuple[str, int]]:
    """Räknar skiftlägesokänsligt och visar den vanligaste stavningen."""
    per_nyckel: dict[str, Counter] = {}
    for n in namn:
        per_nyckel.setdefault(n.strip().casefold(), Counter())[n.strip()] += 1
    ut = [(c.most_common(1)[0][0], sum(c.values())) for c in per_nyckel.values()]
    return sorted(ut, key=lambda x: (-x[1], x[0].casefold()))


def berakna(matcher: list[Match]) -> Statistik:
    spelade = [m for m in matcher if m.status == "spelad"]
    biljetter_spelade = [b for m in spelade for b in m.biljetter]
    alla_bokade = [b for m in matcher if m.status != "inställd" for b in m.biljetter if b.typ]
    return Statistik(
        spelade=len(spelade),
        besokta_matcher=[(m, n) for m in spelade
                         if (n := sum(1 for b in m.biljetter if b.besokt == "ja"))],
        anvanda=sum(1 for b in biljetter_spelade if b.besokt == "ja"),
        oanvanda=sum(1 for b in biljetter_spelade if b.besokt == "nej"),
        ej_ifyllda=sum(1 for b in biljetter_spelade if b.bokad and not b.besokt),
        besokare=_gruppera([b.besokare for b in biljetter_spelade if b.besokt == "ja" and b.besokare]),
        bokare=_gruppera([f"<@{b.bokad_av}>" for m in matcher if m.status != "inställd"
                          for b in m.biljetter if b.bokad_av]),
        privat=sum(1 for b in alla_bokade if b.typ == "privat"),
        kund=sum(1 for b in alla_bokade if b.typ == "kund"),
        kunder=_gruppera([b.kund for b in alla_bokade if b.typ == "kund" and b.kund]),
        saknar_historik=[m for m in spelade if any(not b.besokt for b in m.biljetter)],
    )


def format_statistik(s: Statistik, del_: str | None = None) -> str:
    def topp(lista: list[tuple[str, int]], n: int = 10) -> str:
        if not lista:
            return "_inga än_"
        return "\n".join(f"{i}. {namn} – {antal}" for i, (namn, antal) in enumerate(lista[:n], 1))

    delar = {
        "besok": "*✅ Besökta matcher*\n" + ("\n".join(
            f"`{m.match_id}` {m.datum} {m.motstandare} – {n}/2 biljetter använda"
            for m, n in s.besokta_matcher) or "_inga registrerade besök än_"),
        "topplista": f"*👥 Flest besök*\n{topp(s.besokare)}\n\n*🎟️ Flest bokningar*\n{topp(s.bokare)}",
        "biljetter": (f"*🎟️ Biljetter på spelade matcher ({s.spelade} matcher)*\n"
                      f"Använda: *{s.anvanda}* · Inte använda: *{s.oanvanda}* · "
                      f"Bokade men ej ifyllda: *{s.ej_ifyllda}*"),
        "kunder": (f"*🧾 Privat/kund (alla bokningar)*\nPrivat: *{s.privat}* · Kund: *{s.kund}*\n\n"
                   f"*🏢 Kunder som fått biljetter*\n{topp(s.kunder, 25)}"),
    }
    if del_ in delar:
        return delar[del_]
    text = "\n\n".join([delar["biljetter"], delar["besok"], delar["topplista"], delar["kunder"]])
    if s.saknar_historik:
        text += ("\n\n_Saknar besöksuppgifter: "
                 + ", ".join(f"{m.match_id} {m.motstandare}" for m in s.saknar_historik)
                 + " – fyll i med `/biljetter besök <match>`._")
    return text
