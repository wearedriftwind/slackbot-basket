"""Schemalagda jobb (APScheduler i samma process).

- tick (var 5:e minut + direkt vid start): sätt `spelad`, skicka förfallna påminnelser.
- synk (dagligen enligt SYNK_TIDER): hämta och jämför schemat.

Jobben är idempotenta mot databasen, så en omstart ger varken dubbla
påminnelser eller förlorade bokningar.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .paminnelser import Paminnare
from .service import BiljettService

log = logging.getLogger(__name__)


def tick(svc: BiljettService, paminnare: Paminnare) -> None:
    try:
        spelade = svc.uppdatera_spelade()
        if spelade:
            log.info("Satte spelad: %s", ", ".join(spelade))
        skickat = paminnare.kor()
        if skickat:
            log.info("Skickade påminnelser: %s", ", ".join(skickat))
    except Exception:
        log.exception("Fel i tick")


def starta(svc: BiljettService, paminnare: Paminnare,
           synk: Callable[[], None] | None = None) -> BackgroundScheduler:
    cfg = svc.cfg
    s = BackgroundScheduler(timezone=cfg.tidszon,
                            job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 3600})
    s.add_job(tick, IntervalTrigger(minutes=5, timezone=cfg.tidszon), args=[svc, paminnare],
              id="tick", next_run_time=datetime.now(cfg.tidszon))
    if synk is not None:
        for i, t in enumerate(cfg.synk_tider):
            s.add_job(synk, CronTrigger(hour=t.hour, minute=t.minute, timezone=cfg.tidszon), id=f"synk{i}")
    s.start()
    log.info("Schemaläggare startad: tick var 5:e minut, synk %s",
             ", ".join(f"{t:%H:%M}" for t in cfg.synk_tider) if synk else "avstängd")
    return s
