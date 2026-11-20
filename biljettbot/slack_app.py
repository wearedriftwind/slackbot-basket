"""Slack Bolt-koppling (Socket Mode). All logik ligger i kommandon.py/service.py."""
from __future__ import annotations

import json
import logging
import threading
import time

from slack_bolt import App
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from . import format as fmt
from .config import Config
from .kommandon import Kommandon, Notis, Svar
from .service import BiljettService, Fel

log = logging.getLogger(__name__)


class Behorighetskontroll:
    """Alla i biljettkanalen får använda boten. Medlemslistan cachas i 10 minuter."""

    def __init__(self, client: WebClient, cfg: Config, ttl: int = 600):
        self.client = client
        self.cfg = cfg
        self.ttl = ttl
        self._medlemmar: set[str] = set()
        self._hamtad = 0.0
        self._las = threading.Lock()

    def far_anvanda(self, anv: str) -> bool:
        if not self.cfg.krav_kanalmedlemskap or not self.cfg.kanal or anv in self.cfg.admins:
            return True
        with self._las:
            if time.monotonic() - self._hamtad > self.ttl or anv not in self._medlemmar:
                try:
                    medlemmar: set[str] = set()
                    cursor = None
                    while True:
                        r = self.client.conversations_members(channel=self.cfg.kanal, cursor=cursor, limit=1000)
                        medlemmar.update(r["members"])
                        cursor = r.get("response_metadata", {}).get("next_cursor")
                        if not cursor:
                            break
                    self._medlemmar, self._hamtad = medlemmar, time.monotonic()
                except SlackApiError:
                    log.exception("Kunde inte hämta kanalmedlemmar – släpper igenom")
                    return True
            return anv in self._medlemmar


class Leverans:
    """Skickar ett Svar till rätt ställe."""

    def __init__(self, client: WebClient, cfg: Config):
        self.client = client
        self.cfg = cfg

    def notiser(self, notiser: list[Notis]) -> None:
        for n in notiser:
            kanal = n.kanal or self.cfg.kanal
            try:
                self.client.chat_postMessage(channel=kanal, text=n.text, blocks=n.blocks or [fmt.sektion(n.text)],
                                             unfurl_links=False)
            except SlackApiError:
                log.exception("Kunde inte posta notis i %s", kanal)

    def fil(self, anv: str, fil: tuple[str, str]) -> None:
        namn, data = fil
        dm = self.client.conversations_open(users=anv)["channel"]["id"]
        self.client.files_upload_v2(channel=dm, filename=namn, content=data.encode("utf-8"),
                                    title=namn, initial_comment="Här är exporten.")


def skapa_app(cfg: Config, svc: BiljettService, kmd: Kommandon, verifiera_token: bool = True) -> App:
    app = App(token=cfg.slack_bot_token, token_verification_enabled=verifiera_token)
    behorighet = Behorighetskontroll(app.client, cfg)
    leverans = Leverans(app.client, cfg)
    bot_id: dict[str, str | None] = {"id": None}

    def mitt_id(client: WebClient) -> str | None:
        if bot_id["id"] is None:
            try:
                bot_id["id"] = client.auth_test()["user_id"]
            except SlackApiError:
                log.exception("auth_test misslyckades")
        return bot_id["id"]

    NEKAD = "Biljetterna är till för Driftwind – gå med i biljettkanalen för att kunna boka."

    def ladda_om_meddelande(body: dict, client: WebClient) -> None:
        """Uppdatera knappmeddelandet (påminnelse/status) så att det visar aktuellt läge."""
        meddelande = body.get("message")
        kanal = (body.get("channel") or {}).get("id")
        if not meddelande or not kanal or body.get("container", {}).get("is_ephemeral"):
            return
        for block in meddelande.get("blocks", []):
            bid = block.get("block_id", "")
            if block.get("type") == "actions" and ":" in bid:
                slag, mid = bid.split(":", 1)
                try:
                    m = svc.match(mid)
                except Fel:
                    return
                if slag == "paminnelse":
                    text, blocks = fmt.paminnelse(m, fmt.paminnelse_rubrik(m, svc.nu()))
                else:
                    text, blocks = fmt.status(m)
                try:
                    client.chat_update(channel=kanal, ts=meddelande["ts"], text=text, blocks=blocks)
                except SlackApiError:
                    log.warning("Kunde inte uppdatera meddelande", exc_info=True)
                return

    def svara_action(svar: Svar, respond, body: dict, client: WebClient, ersatt: bool) -> None:
        if svar.modal:
            client.views_open(trigger_id=body["trigger_id"], view=svar.modal)
            return
        respond(text=svar.text, blocks=svar.blocks, response_type="ephemeral",
                replace_original=ersatt)
        leverans.notiser(svar.notiser)

    # ---- slash-kommando -----------------------------------------------------

    @app.command("/biljetter")
    def slash(ack, command, respond, client):
        ack()
        anv = command["user_id"]
        if not behorighet.far_anvanda(anv):
            respond(NEKAD)
            return
        svar = kmd.kommando(anv, command.get("text", ""))
        if svar.modal:
            svar.modal["private_metadata"] = json.dumps({"kanal": command.get("channel_id")})
            client.views_open(trigger_id=command["trigger_id"], view=svar.modal)
            return
        respond(text=svar.text, blocks=svar.blocks,
                response_type="in_channel" if svar.offentlig else "ephemeral")
        leverans.notiser(svar.notiser)
        if svar.fil:
            leverans.fil(anv, svar.fil)

    # ---- fritext: @-omnämnande och DM -----------------------------------------

    def hantera_fritext(event: dict, client: WebClient, say, i_kanal: bool) -> None:
        anv = event.get("user")
        if not anv or event.get("bot_id"):
            return
        if not behorighet.far_anvanda(anv):
            say(text=NEKAD, thread_ts=event.get("ts") if i_kanal else None)
            return
        svar = kmd.fritext(anv, event.get("text", ""), bot_id=mitt_id(client))
        if svar.personlig and i_kanal:
            client.chat_postEphemeral(channel=event["channel"], user=anv, text=svar.text, blocks=svar.blocks,
                                      thread_ts=event.get("thread_ts"))
        else:
            say(text=svar.text, blocks=svar.blocks,
                thread_ts=(event.get("thread_ts") or event.get("ts")) if i_kanal else None)
        leverans.notiser(svar.notiser)
        if svar.fil:
            leverans.fil(anv, svar.fil)

    @app.event("app_mention")
    def mention(event, client, say):
        hantera_fritext(event, client, say, i_kanal=True)

    @app.event("message")
    def dm(event, client, say):
        if event.get("channel_type") != "im" or event.get("subtype"):
            return
        hantera_fritext(event, client, say, i_kanal=False)

    # ---- knappar -------------------------------------------------------------------

    @app.action("boka_privat")
    def a_boka_privat(ack, body, action, respond, client):
        ack()
        anv = body["user"]["id"]
        if not behorighet.far_anvanda(anv):
            respond(text=NEKAD, response_type="ephemeral", replace_original=False)
            return
        svar = kmd.knapp_boka_privat(anv, action["value"])
        svara_action(svar, respond, body, client, ersatt=False)
        ladda_om_meddelande(body, client)

    @app.action("avboka_min")
    def a_avboka_min(ack, body, action, respond, client):
        ack()
        anv = body["user"]["id"]
        svar = kmd.knapp_avboka_min(anv, action["value"])
        svara_action(svar, respond, body, client, ersatt=False)
        ladda_om_meddelande(body, client)

    @app.action("boka_modal")
    def a_boka_modal(ack, body, action, respond, client):
        ack()
        anv = body["user"]["id"]
        if not behorighet.far_anvanda(anv):
            respond(text=NEKAD, response_type="ephemeral", replace_original=False)
            return
        vy = kmd.boka_modal(anv, action["value"], kanal=(body.get("channel") or {}).get("id"))
        if vy is None:
            respond(text="Det finns inga lediga biljetter just nu.", response_type="ephemeral",
                    replace_original=False)
            return
        client.views_open(trigger_id=body["trigger_id"], view=vy)

    @app.action("bekrafta")
    def a_bekrafta(ack, body, action, respond, client):
        ack()
        anv = body["user"]["id"]
        svar = kmd.utfor(anv, json.loads(action["value"]))
        svara_action(svar, respond, body, client, ersatt=True)

    @app.action("avbryt")
    def a_avbryt(ack, respond):
        ack()
        respond(text="Avbrutet – inget ändrades.", replace_original=True)

    # ---- modal -----------------------------------------------------------------------

    @app.view("boka_vy")
    def v_boka(ack, body, view, client):
        anv = body["user"]["id"]
        if not behorighet.far_anvanda(anv):
            ack(response_action="errors", errors={"match": NEKAD[:150]})
            return
        fel_, svar = kmd.boka_fran_modal(anv, view["state"]["values"])
        if fel_:
            ack(response_action="errors", errors=fel_)
            return
        ack()
        meta = json.loads(view.get("private_metadata") or "{}")
        kanal = meta.get("kanal")
        try:
            if kanal:
                client.chat_postEphemeral(channel=kanal, user=anv, text=svar.text, blocks=svar.blocks)
            else:
                raise SlackApiError("ingen kanal", None)
        except SlackApiError:
            dm = client.conversations_open(users=anv)["channel"]["id"]
            client.chat_postMessage(channel=dm, text=svar.text, blocks=svar.blocks)
        if svar.offentlig and kanal:
            client.chat_postMessage(channel=kanal, text=svar.text, blocks=svar.blocks)
        leverans.notiser(svar.notiser)

    @app.error
    def fel_hanterare(error, body, logger):
        logger.exception("Fel i Slack-hanterare: %s", error)

    app.leverans = leverans  # används av schemalagda jobb
    return app
