"""Tidshjälpare. All lokal tid är Europe/Stockholm (konfigurerbar); lagring sker som ISO 8601."""
from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

VECKODAGAR = ["måndag", "tisdag", "onsdag", "torsdag", "fredag", "lördag", "söndag"]
MANADER = [
    "januari", "februari", "mars", "april", "maj", "juni",
    "juli", "augusti", "september", "oktober", "november", "december",
]


def iso(dt: datetime) -> str:
    """ISO 8601 med offset, sekundupplösning."""
    if dt.tzinfo is None:
        raise ValueError("Naiv datetime – ange tidszon")
    return dt.isoformat(timespec="seconds")


def veckodag(d: date) -> str:
    return VECKODAGAR[d.weekday()]


def matchstart(datum: str, tid: str | None, tz: ZoneInfo) -> datetime:
    """Uppkast som tidszonsmedveten datetime. Saknas tid räknas matchen från 00:00."""
    d = date.fromisoformat(datum)
    t = time.fromisoformat(tid) if tid else time(0, 0)
    # zoneinfo hanterar sommar-/vintertid via fold-regler
    return datetime.combine(d, t, tzinfo=tz)


def kort_datum(datum: str) -> str:
    """"2026-12-18" -> "fre 18 dec"."""
    d = date.fromisoformat(datum)
    return f"{VECKODAGAR[d.weekday()][:3]} {d.day} {MANADER[d.month - 1][:3]}"


def langt_datum(datum: str) -> str:
    """"2026-12-18" -> "fredag 18 december 2026"."""
    d = date.fromisoformat(datum)
    return f"{VECKODAGAR[d.weekday()]} {d.day} {MANADER[d.month - 1]} {d.year}"
