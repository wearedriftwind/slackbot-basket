from dataclasses import replace

from biljettbot.kommandon import Kommandon
from biljettbot.service import BiljettService
from biljettbot.slack_app import skapa_app


def test_appen_kan_skapas_och_registrerar_lyssnare(db_importerad, cfg):
    cfg = replace(cfg, slack_bot_token="xoxb-test")
    svc = BiljettService(db_importerad, cfg)
    app = skapa_app(cfg, svc, Kommandon(svc, cfg), verifiera_token=False)
    assert len(app._listeners) >= 9
