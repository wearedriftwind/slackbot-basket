"""Start: `python -m biljettbot`. Kör Slack-boten i Socket Mode."""
from __future__ import annotations

import logging
import sys
from datetime import datetime

from slack_bolt.adapter.socket_mode import SocketModeHandler

from . import csvio, jobb
from .config import Config
from .db import Database
from .kommandon import Kommandon
from .paminnelser import Paminnare
from .service import BiljettService
from .slack_app import skapa_app

log = logging.getLogger("biljettbot")


def main() -> int:
    cfg = Config.fran_miljo()
    logging.basicConfig(level=cfg.loggniva, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    saknas = [n for n, v in (("SLACK_BOT_TOKEN", cfg.slack_bot_token), ("SLACK_APP_TOKEN", cfg.slack_app_token),
                             ("BILJETT_KANAL", cfg.kanal)) if not v]
    if saknas:
        log.error("Saknar miljövariabler: %s", ", ".join(saknas))
        return 2

    db = Database(cfg.databas)
    n = csvio.importera_vid_forsta_start(db, cfg.csv_import, datetime.now(cfg.tidszon), cfg.arena)
    if n:
        log.info("Importerade %d matcher från %s", n, cfg.csv_import)

    svc = BiljettService(db, cfg)
    kmd = Kommandon(svc, cfg)
    app = skapa_app(cfg, svc, kmd)

    def posta(text: str, blocks: list) -> None:
        app.client.chat_postMessage(channel=cfg.kanal, text=text, blocks=blocks, unfurl_links=False)

    paminnare = Paminnare(svc, posta)
    jobb.starta(svc, paminnare)
    log.info("Startar biljettboten (Socket Mode), databas %s", cfg.databas)
    SocketModeHandler(app, cfg.slack_app_token).start()
    return 0


if __name__ == "__main__":
    sys.exit(main())
