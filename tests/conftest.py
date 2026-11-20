from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from biljettbot import csvio
from biljettbot.config import Config
from biljettbot.db import Database

ROT = Path(__file__).resolve().parent.parent
CSV = ROT / "docs" / "lulea_basket_hemmamatcher_2026-27.csv"
FIXTURER = Path(__file__).resolve().parent / "fixtures"
TZ = ZoneInfo("Europe/Stockholm")


def t(s: str) -> datetime:
    """"2026-11-20 12:00" -> tidszonsmedveten Stockholmstid."""
    return datetime.fromisoformat(s).replace(tzinfo=TZ)


@pytest.fixture
def cfg() -> Config:
    return Config(kanal="CKANAL", admins=frozenset({"UADMIN"}))


@pytest.fixture
def db(tmp_path) -> Database:
    return Database(tmp_path / "test.db")


@pytest.fixture
def db_importerad(db) -> Database:
    csvio.importera(db, CSV.read_text(encoding="utf-8-sig"), t("2026-11-20 09:00"),
                    "Luleå Energi Arena")
    return db
