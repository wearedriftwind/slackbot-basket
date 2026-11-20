"""Konfiguration från miljövariabler (eller .env lokalt). Inga hemligheter i repot."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import time
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Paminnelseregel:
    """En påminnelse `dagar_fore` dagar före matchdagen, klockan `klockslag`."""

    dagar_fore: int
    klockslag: time

    @property
    def nyckel(self) -> str:
        return f"d{self.dagar_fore}@{self.klockslag:%H:%M}"


def parse_paminnelser(text: str) -> list[Paminnelseregel]:
    """Tolka t.ex. "0@10:00" eller "3@09:00,0@10:00" (dagar före @ klockslag)."""
    regler = []
    for del_ in filter(None, (d.strip() for d in text.split(","))):
        m = re.fullmatch(r"(\d+)@(\d{1,2}):(\d{2})", del_)
        if not m:
            raise ValueError(f"Ogiltig påminnelseregel: {del_!r} (format: dagar@HH:MM)")
        regler.append(Paminnelseregel(int(m[1]), time(int(m[2]), int(m[3]))))
    return sorted(regler, key=lambda r: (-r.dagar_fore, r.klockslag))


def parse_klockslag(text: str) -> list[time]:
    tider = []
    for del_ in filter(None, (d.strip() for d in text.split(","))):
        m = re.fullmatch(r"(\d{1,2}):(\d{2})", del_)
        if not m:
            raise ValueError(f"Ogiltigt klockslag: {del_!r} (format: HH:MM)")
        tider.append(time(int(m[1]), int(m[2])))
    return tider


def _lista(text: str) -> frozenset[str]:
    return frozenset(filter(None, (d.strip() for d in text.split(","))))


def _bool(text: str) -> bool:
    return text.strip().lower() in {"1", "true", "ja", "yes", "on"}


@dataclass(frozen=True)
class Config:
    slack_bot_token: str = ""
    slack_app_token: str = ""
    kanal: str = ""  # kanal-ID för påminnelser, notiser och synk-diffar
    tidszon: ZoneInfo = field(default_factory=lambda: ZoneInfo("Europe/Stockholm"))
    schema_url: str = "https://luleabasket.com/matcher/"
    hemmalag: str = "Luleå Basket"
    arena: str = "Luleå Energi Arena"
    databas: str = "data/biljetter.db"
    csv_import: str = "docs/lulea_basket_hemmamatcher_2026-27.csv"
    paminnelser: tuple[Paminnelseregel, ...] = (Paminnelseregel(0, time(10, 0)),)
    avbokning_notis_dagar: int = 7
    spelad_efter_timmar: float = 3.0
    synk_tider: tuple[time, ...] = (time(6, 30),)
    synk_max_minskning: float = 0.3
    synk_max_nya: int = 8
    synk_saknad_grans: int = 2
    admins: frozenset[str] = frozenset()
    krav_kanalmedlemskap: bool = True
    loggniva: str = "INFO"

    @classmethod
    def fran_miljo(cls) -> "Config":
        try:
            from dotenv import load_dotenv

            load_dotenv()
        except ImportError:  # pragma: no cover
            pass
        e = os.environ.get
        return cls(
            slack_bot_token=e("SLACK_BOT_TOKEN", ""),
            slack_app_token=e("SLACK_APP_TOKEN", ""),
            kanal=e("BILJETT_KANAL", ""),
            tidszon=ZoneInfo(e("TIDSZON", "Europe/Stockholm")),
            schema_url=e("SCHEMA_URL", cls.schema_url),
            hemmalag=e("HEMMALAG", cls.hemmalag),
            arena=e("ARENA", cls.arena),
            databas=e("DATABAS_SOKVAG", cls.databas),
            csv_import=e("CSV_IMPORT_SOKVAG", cls.csv_import),
            paminnelser=tuple(parse_paminnelser(e("PAMINNELSER", "0@10:00"))),
            avbokning_notis_dagar=int(e("AVBOKNING_NOTIS_DAGAR", "7")),
            spelad_efter_timmar=float(e("SPELAD_EFTER_TIMMAR", "3")),
            synk_tider=tuple(parse_klockslag(e("SYNK_TIDER", "06:30"))),
            synk_max_minskning=float(e("SYNK_MAX_MINSKNING", "0.3")),
            synk_max_nya=int(e("SYNK_MAX_NYA", "8")),
            synk_saknad_grans=int(e("SYNK_SAKNAD_GRANS", "2")),
            admins=_lista(e("ADMINS", "")),
            krav_kanalmedlemskap=_bool(e("KRAV_KANALMEDLEMSKAP", "true")),
            loggniva=e("LOGGNIVA", "INFO"),
        )
