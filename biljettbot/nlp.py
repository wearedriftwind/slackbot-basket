"""Regelbaserad tolkning av fritext ("boka en biljett till Alvik, kund: Skellefteå kommun").

Tolkningen känner igen ett fast antal avsikter och plockar ut matchreferens
(match-id, datum, veckodag, motståndare, "nästa match"), antal, typ, kundnamn
och "åt @namn". Allt som skriver data bekräftas med knappar innan det utförs,
så tolkningen får vara förlåtande utan att det blir farligt.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .db import Match

MANADER = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "maj": 5, "jun": 6, "jul": 7, "aug": 8,
           "sep": 9, "okt": 10, "nov": 11, "dec": 12}
VECKODAGAR = {"mandag": 0, "tisdag": 1, "onsdag": 2, "torsdag": 3, "fredag": 4, "lordag": 5,
              "sondag": 6}
# Ord i lagnamn som inte identifierar ett lag
LAG_STOPPORD = {"basket", "bbk", "bk", "ik", "if", "ladies", "lulea", "dam", "damer", "basketball"}

MENTION = re.compile(r"<@([UW][A-Z0-9]+)(?:\|[^>]*)?>")


def vik(text: str) -> str:
    """Gemener och å/ä/ö -> a/a/o för tolerant jämförelse."""
    return (text.lower().replace("å", "a").replace("ä", "a").replace("ö", "o")
            .replace("é", "e").replace("ü", "u"))


@dataclass
class MatchRef:
    match_id: str | None = None
    datum: tuple[int | None, int, int] | None = None  # (år eller None, månad, dag)
    relativ_dag: int | None = None                     # 0 = idag, 1 = imorgon
    veckodag: int | None = None
    nasta: bool = False
    ord: list[str] = field(default_factory=list)        # vikta ord att matcha mot motståndare

    @property
    def tom(self) -> bool:
        return not (self.match_id or self.datum or self.relativ_dag is not None
                    or self.veckodag is not None or self.nasta or self.ord)


@dataclass
class Avsikt:
    typ: str  # boka avboka status nasta kommande sasong besok statistik synka export hjalp okand
    ref: MatchRef = field(default_factory=MatchRef)
    antal: int | None = None
    nr: int | None = None
    bokningstyp: str | None = None
    kund: str | None = None
    for_anvandare: str | None = None
    alla: bool = False
    besokt: str | None = None
    besokare: str | None = None
    statistik_typ: str | None = None


# ---- avsikt --------------------------------------------------------------

AVSIKTER: list[tuple[str, re.Pattern]] = [
    ("hjalp", re.compile(r"^\s*(hjalp|help|\?)\s*$|\bvad kan du\b|\bhur (anvander|funkar)\b")),
    ("synka", re.compile(r"\bsynka\b|\bsynkronisera\b|\buppdatera schemat\b")),
    ("export", re.compile(r"\bexport(era)?\b")),
    ("statistik", re.compile(r"\bstatistik\b|\bstats\b|\btopplista\b|\bflest (ganger|matcher|besok)\b"
                             r"|\bvilka kunder\b|\bfordelning\b|\bhur manga biljetter\b"
                             r"|\bvilka matcher har (vi )?besokt\b")),
    ("avboka", re.compile(r"\bavboka|\bavbest[a]ll|\bslapp\b|\bslapper\b|\bkan inte ga\b"
                          r"|\bta bort (min|mina|bokning)")),
    ("besok", re.compile(r"\bbesok\b|\bbesokte?\b|\bvar pa\b|\bgick (pa|till|pa matchen)\b"
                         r"|\b(anvandes|anvant|anvandas) inte\b|\binte anvand|\boanvand"
                         r"|\bingen gick\b|\bgick inte\b")),
    ("boka", re.compile(r"\bbokar?\b|\breserver|\bvill ha (en |tva |bada )?biljett|\bta (en|bada) biljett")),
    ("status", re.compile(r"\bvem har\b|\bvilka har\b|\bstatus\b|\blediga?\b|\bfinns det\b"
                          r"|\bar (biljetterna|nagon|de|dom) \b|\bvem (ska|gar)\b|\bbokade?\b")),
    ("nasta", re.compile(r"\bnasta( hemma)?match|\bnar ar nasta\b|\bnasta\b")),
    ("kommande", re.compile(r"\bkommande\b")),
    ("sasong", re.compile(r"\bschemat?\b|\bsasong(en)?\b|\balla matcher(na)?\b|\bspelschema")),
]


SKRIVANDE = {"boka", "avboka", "besok", "synka", "export"}
FRAGA = re.compile(r"^\s*(vem|vilka|vilken|nar|hur|finns|ar)\b")


def _avsikt(v: str) -> str:
    fraga = bool(FRAGA.search(v))
    for namn, monster in AVSIKTER:
        if fraga and namn in SKRIVANDE:
            continue  # "vem gick på Alvik?" är en fråga, inte en registrering
        if monster.search(v):
            return namn
    if fraga:
        return "status"
    return "okand"


# ---- extrahering ---------------------------------------------------------

KUND_MONSTER = [
    re.compile(r"\bkund(?:en)?\s*[:=]\s*(?P<namn>.+)", re.I),
    re.compile(r"\b(?:för|till|åt)\s+kund(?:en)?\s+(?P<namn>.+)", re.I),
]
KUND_SLUT = re.compile(r"\s*(?:,|;|\.\s|\.$|\s(?:till|på|den|åt|för)\s+(?=\S)|<@)", re.I)


def _plocka_kund(text: str) -> tuple[str | None, str]:
    """Returnerar (kundnamn, text utan kunddelen)."""
    for monster in KUND_MONSTER:
        m = monster.search(text)
        if m:
            rest = m["namn"]
            slut = KUND_SLUT.search(rest)
            namn = (rest[:slut.start()] if slut else rest).strip().strip("\"'”“")
            if namn:
                kvar = text[:m.start()] + " " + (rest[slut.start():] if slut else "")
                return namn, kvar
    return None, text


def _plocka_datum(v: str) -> tuple[tuple[int | None, int, int] | None, str]:
    m = re.search(r"\b(20\d\d)-(\d{2})-(\d{2})\b", v)
    if m:
        return (int(m[1]), int(m[2]), int(m[3])), v.replace(m[0], " ")
    m = re.search(r"\b(\d{1,2})(?::?e|:a)?\s+(" + "|".join(MANADER) + r")[a-z]*\.?(?:\s+(20\d\d))?", v)
    if m:
        return ((int(m[3]) if m[3] else None), MANADER[m[2]], int(m[1])), v.replace(m[0], " ")
    m = re.search(r"\b(\d{1,2})/(\d{1,2})\b", v)
    if m and 1 <= int(m[2]) <= 12:
        return (None, int(m[2]), int(m[1])), v.replace(m[0], " ")
    return None, v


def tolka(text: str, bot_id: str | None = None, tvingad_typ: str | None = None) -> Avsikt:
    original = text
    if bot_id:
        original = re.sub(rf"<@{bot_id}(?:\|[^>]*)?>", " ", original)
    besokare = None
    mb = re.search(r"\bbesökare\s*[:=]\s*(.+)$", original, re.I)
    if mb:
        besokare = mb[1].strip().strip("\"'”“")
        original = original[:mb.start()]
    kund, utan_kund = _plocka_kund(original)
    mentions = MENTION.findall(utan_kund)
    utan_mentions = MENTION.sub(" ", utan_kund)
    v = vik(utan_mentions)

    a = Avsikt(typ=tvingad_typ or _avsikt(v))
    a.kund = kund
    if kund or re.search(r"\bkund(en)?\b", v):
        a.bokningstyp = "kund"
    elif re.search(r"\bprivat\b", v):
        a.bokningstyp = "privat"

    if mentions:
        a.for_anvandare = mentions[0]

    # antal och biljettnummer
    m = re.search(r"\bbiljett\s*(?:nr\.?\s*|nummer\s*)?([12])\b|\b#([12])\b", v)
    if m:
        a.nr = int(m[1] or m[2])
        v = v.replace(m[0], " ")
    if re.search(r"\b(bada|tva|alla|2)\s*(biljetter(na)?|biljett|st)\b|\bbada\b|\balla\b|\bbiljetterna\b", v):
        a.antal = 2
        a.alla = bool(re.search(r"\b(bada|alla)\b", v))
    elif re.search(r"\b(en|1|ena)\s*(biljett|st)\b", v):
        a.antal = 1

    # besök: ja/nej, och vem som gick
    if a.typ == "besok":
        if re.search(r"\b(nej|inte|ingen|oanvand)", v):
            a.besokt = "nej"
        elif tvingad_typ is None or re.search(r"\bja\b", v):
            a.besokt = "ja"
        a.besokare = besokare or (f"<@{mentions[0]}>" if mentions else None)

    a.ref = _matchref(v)
    return a


def _matchref(v: str) -> MatchRef:
    ref = MatchRef()
    m = re.search(r"\bm\s?(\d{1,3})\b", v)
    if m:
        ref.match_id = f"M{int(m[1]):02d}"
        v = v.replace(m[0], " ")
    ref.datum, v = _plocka_datum(v)
    if re.search(r"\b(idag|ikvall|i kvall)\b", v):
        ref.relativ_dag = 0
    elif re.search(r"\b(imorgon|i morgon)\b", v):
        ref.relativ_dag = 1
    for namn, nr in VECKODAGAR.items():
        if re.search(rf"\b{namn}(en|ens)?\b", v):
            ref.veckodag = nr
    if re.search(r"\bnasta\b", v):
        ref.nasta = True
    ref.ord = [o for o in re.findall(r"[a-z0-9]+", v) if len(o) >= 3]
    return ref


# ---- matchning mot lagrade matcher -----------------------------------------

def _lagord(m: Match) -> list[str]:
    return [o for o in re.findall(r"[a-z0-9]+", vik(m.motstandare))
            if o not in LAG_STOPPORD and len(o) >= 3]


def _ord_matchar_lag(ord_: str, lagord: str) -> bool:
    if ord_ == lagord or ord_.startswith(lagord):           # "alvikmatchen", "lunds"
        return True
    if len(ord_) >= 4 and lagord.startswith(ord_):            # "norrk", "soder"
        return True
    return len(ord_) >= 5 and difflib.SequenceMatcher(None, ord_, lagord).ratio() >= 0.85


def matcher_for_lag(ord_lista: list[str], matcher: list[Match]) -> list[Match]:
    return [m for m in matcher
            if any(_ord_matchar_lag(o, lo) for o in ord_lista for lo in _lagord(m))]


@dataclass
class Traff:
    match: Match | None
    fel: str | None = None
    kandidater: list[Match] = field(default_factory=list)


def hitta_match(ref: MatchRef, matcher: list[Match], nu: datetime, foredra: str = "kommande") -> Traff:
    """Löser en matchreferens. `foredra` = "kommande" (boka/status) eller "spelad" (besök)."""
    tz = nu.tzinfo
    if ref.match_id:
        m = next((m for m in matcher if m.match_id == ref.match_id), None)
        return Traff(m, None if m else f"Hittar ingen match med id {ref.match_id}.")

    def ar_kommande(m: Match) -> bool:
        return m.status == "flyttad" or (m.status == "kommande" and m.start(tz) > nu)

    kand = list(matcher)
    filtrerat = False
    if ref.datum:
        ar, man, dag = ref.datum
        kand = [m for m in kand if (date.fromisoformat(m.datum).month, date.fromisoformat(m.datum).day)
                == (man, dag) and (ar is None or date.fromisoformat(m.datum).year == ar)]
        filtrerat = True
    if ref.relativ_dag is not None:
        dag = (nu + timedelta(days=ref.relativ_dag)).date().isoformat()
        kand = [m for m in kand if m.datum == dag]
        filtrerat = True
    lag = matcher_for_lag(ref.ord, kand) if ref.ord else []
    if lag:
        kand = lag
        filtrerat = True
    if ref.veckodag is not None and not ref.datum and ref.relativ_dag is None:
        kand = [m for m in kand if date.fromisoformat(m.datum).weekday() == ref.veckodag]
        if foredra == "kommande":
            kand = [m for m in kand if ar_kommande(m)][:1]
        filtrerat = True
    if ref.nasta:
        kand = [m for m in kand if m.status == "kommande" and m.start(tz) > nu][:1]
        filtrerat = True
    if not filtrerat:
        return Traff(None, "Vilken match menar du? Ange motståndare, datum (t.ex. 18 dec) eller match-id (M05).")
    if not kand:
        return Traff(None, "Hittar ingen match som passar. Skriv `/biljetter schema` för att se alla matcher.")
    if len(kand) == 1:
        return Traff(kand[0])
    if foredra == "kommande":
        kom = [m for m in kand if ar_kommande(m)]
        if kom:
            return Traff(kom[0], kandidater=kand)
    else:
        spelade = [m for m in kand if not ar_kommande(m)]
        if spelade:
            return Traff(spelade[-1], kandidater=kand)
    return Traff(None, "Flera matcher passar: " + ", ".join(f"{m.match_id} {m.motstandare} {m.datum}"
                                                           for m in kand), kand)
