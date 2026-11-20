"""Kommandon och fritext → svar. Slack-oberoende så att logiken kan testas utan Slack.

Slack-lagret (slack_app.py) levererar `Svar`: text/blocks till användaren,
`notiser` till biljettkanalen, eventuell modal och eventuell fil.
"""
from __future__ import annotations

import json
import shlex
from dataclasses import dataclass, field
from typing import Callable

from . import format as fmt
from . import nlp
from .config import Config
from .db import Match
from .nlp import Avsikt, vik
from .service import BiljettService, Fel, Upptagen, beskriv_innehavare


@dataclass
class Notis:
    text: str
    blocks: list | None = None
    kanal: str | None = None  # None = biljettkanalen


@dataclass
class Svar:
    text: str
    blocks: list | None = None
    offentlig: bool = False           # synligt för alla i kanalen där frågan ställdes
    personlig: bool = False           # t.ex. bekräftelsefråga: visa bara för användaren
    notiser: list[Notis] = field(default_factory=list)
    modal: dict | None = None         # öppna denna modal (kräver trigger_id)
    fil: tuple[str, str] | None = None  # (filnamn, innehåll)


ALIAS = {
    "boka": "boka", "avboka": "avboka", "status": "status", "vem": "status",
    "nasta": "nasta", "kommande": "kommande", "schema": "sasong", "sasong": "sasong",
    "besok": "besok", "besokt": "besok", "statistik": "statistik", "stats": "statistik",
    "synka": "synka", "synk": "synka", "export": "export", "exportera": "export",
    "admin": "admin", "hjalp": "hjalp", "help": "hjalp",
}


def fel(text: str) -> Svar:
    return Svar(f"⚠️ {text}", [fmt.sektion(f"⚠️ {text}")])


class Kommandon:
    def __init__(self, svc: BiljettService, cfg: Config,
                 synka: Callable[[str, bool], Svar] | None = None):
        self.svc = svc
        self.cfg = cfg
        self.synka = synka  # sätts av schemasynken

    # ---- ingångar ----------------------------------------------------------

    def kommando(self, anv: str, text: str) -> Svar:
        """`/biljetter <underkommando> ...`. Okända underkommandon tolkas som fritext."""
        self.svc.uppdatera_spelade()
        text = (text or "").strip()
        forsta, _, rest = text.partition(" ")
        typ = ALIAS.get(vik(forsta))
        if not text:
            typ = "nasta"
        if typ is None:
            return self.fritext(anv, text)
        try:
            if typ == "admin":
                return self.admin(anv, rest)
            if typ == "export":
                return self.export(anv, rest)
            if typ == "besok":
                a = self._tolka_slash_besok(rest)
            else:
                a = nlp.tolka(rest, tvingad_typ=typ)
            return self._utfor_avsikt(anv, a, bekrafta=False)
        except Fel as e:
            return fel(e.meddelande)

    def fritext(self, anv: str, text: str, bot_id: str | None = None) -> Svar:
        """Naturligt språk från DM eller @-omnämnande. Skrivande avsikter bekräftas."""
        self.svc.uppdatera_spelade()
        a = nlp.tolka(text, bot_id=bot_id)
        if a.typ == "okand":
            return Svar("Jag förstod inte riktigt. Prova t.ex. _boka en biljett till Alvik_, "
                        "_vem har biljetterna 18 dec?_ eller `/biljetter hjälp`.")
        try:
            return self._utfor_avsikt(anv, a, bekrafta=True)
        except Fel as e:
            return fel(e.meddelande)

    def _utfor_avsikt(self, anv: str, a: Avsikt, bekrafta: bool) -> Svar:
        if a.typ == "hjalp":
            return Svar(*fmt.hjalp())
        if a.typ == "nasta" and a.ref.tom:
            m = self.svc.nasta()
            if not m:
                return Svar("Det finns inga fler inplanerade hemmamatcher just nu.")
            return Svar(*fmt.status(m))
        if a.typ == "kommande":
            return Svar(*fmt.lista("Kommande hemmamatcher", self.svc.kommande(),
                                   "Inga kommande matcher just nu."))
        if a.typ == "sasong":
            return Svar(*fmt.lista("Säsongen – Luleå Basket hemma", self.svc.matcher()))
        if a.typ in ("status", "nasta"):
            return self._status(a)
        if a.typ == "boka":
            return self._boka(anv, a, bekrafta)
        if a.typ == "avboka":
            return self._avboka(anv, a, bekrafta)
        if a.typ == "besok":
            return self._besok(anv, a, bekrafta)
        if a.typ == "statistik":
            return self._statistik(anv, a)
        if a.typ == "synka":
            if self.synka is None:
                return fel("Schemasynk är inte aktiverad.")
            return self.synka(anv, False)
        if a.typ == "export":
            return self.export(anv, "")
        return Svar("Jag förstod inte riktigt. Skriv `/biljetter hjälp`.")

    # ---- läsning -------------------------------------------------------------

    def _hitta(self, a: Avsikt, foredra: str = "kommande") -> Match:
        traff = nlp.hitta_match(a.ref, self.svc.matcher(), self.svc.nu(), foredra)
        if traff.match is None:
            raise Fel(traff.fel or "Hittar ingen sådan match.")
        return traff.match

    def _status(self, a: Avsikt) -> Svar:
        if a.ref.tom:
            m = self.svc.nasta()
            if not m:
                return Svar("Det finns inga fler inplanerade hemmamatcher just nu.")
        else:
            m = self._hitta(a)
        return Svar(*fmt.status(m))

    # ---- bokning -------------------------------------------------------------

    def _boka(self, anv: str, a: Avsikt, bekrafta: bool) -> Svar:
        if a.ref.tom:
            if bekrafta:
                return fel("Vilken match vill du boka? T.ex. _boka en biljett till Alvik_.")
            return self._modal_svar(anv, None, a)
        m = self._hitta(a)
        if a.bokningstyp == "kund" and not a.kund:
            if bekrafta:
                return Svar("Vilken kund gäller det? Skriv t.ex. _kund: Skellefteå kommun_, eller öppna formuläret.",
                            [fmt.sektion("Vilken kund gäller det? Skriv t.ex. _kund: Skellefteå kommun_ – "
                                         "eller fyll i formuläret."),
                             fmt.handlingar(f"status:{m.match_id}",
                                            fmt.knapp("Boka för kund…", "boka_modal", m.match_id))],
                            personlig=True)
            return self._modal_svar(anv, m.match_id, a)
        antal = a.antal or 1
        typ = a.bokningstyp or "privat"
        bokad_av = a.for_anvandare or anv
        if bekrafta:
            if len(m.lediga) < antal or m.status != "kommande":
                # kör riktiga bokningen för att få samma felmeddelande; den misslyckas utan att skriva
                return self._gor_bokning(anv, m.match_id, bokad_av, antal, typ, a.kund)
            beskr = f"kund: {a.kund}" if typ == "kund" else "privat"
            at = f", åt <@{bokad_av}>" if bokad_av != anv else ""
            fraga = (f"Boka *{antal} biljett{'er' if antal == 2 else ''}* till *{m.motstandare}* "
                     f"({fmt.tid_text(m)}) – {beskr}{at}?")
            payload = {"h": "boka", "m": m.match_id, "n": antal, "t": typ, "k": a.kund,
                       "f": bokad_av, "a": anv}
            return self._bekraftelse(fraga, payload, andra=m.match_id)
        return self._gor_bokning(anv, m.match_id, bokad_av, antal, typ, a.kund)

    def _gor_bokning(self, anv: str, mid: str, bokad_av: str, antal: int, typ: str,
                     kund: str | None) -> Svar:
        try:
            r = self.svc.boka(mid, bokad_av, anv, antal=antal, typ=typ, kund=kund)
        except Upptagen as e:
            text, blocks = fmt.status(e.match)
            return Svar(f"⛔ {e.meddelande}", [fmt.sektion(f"⛔ {e.meddelande}")] + blocks)
        m = r.match
        beskr = r.biljetter[0].beskrivning()
        n = len(r.biljetter)
        vem = f"<@{bokad_av}>" if bokad_av == anv else f"<@{anv}> bokade åt <@{bokad_av}>:"
        kvar = {0: "Båda biljetterna är nu bokade.", 1: "1 biljett kvar.", 2: "2 biljetter kvar."}[len(m.lediga)]
        if bokad_av == anv:
            notis = (f"🎟️ <@{bokad_av}> bokade {n} biljett{'er' if n == 2 else ''} till "
                     f"*{m.motstandare}* ({fmt.tid_text(m)}) – {beskr}. {kvar}")
        else:
            notis = (f"🎟️ {vem} {n} biljett{'er' if n == 2 else ''} till "
                     f"*{m.motstandare}* ({fmt.tid_text(m)}) – {beskr}. {kvar}")
        bekr = f"✅ Bokat! {n} biljett{'er' if n == 2 else ''} till {m.motstandare} ({fmt.tid_text(m)}) – {beskr}."
        return self._med_notis(bekr, notis)

    def _avboka(self, anv: str, a: Avsikt, bekrafta: bool) -> Svar:
        if a.ref.tom:
            mina = [m for m in self.svc.kommande() if any(b.bokad_av == (a.for_anvandare or anv)
                                                          for b in m.bokade)]
            if len(mina) != 1:
                return fel("Vilken match vill du avboka? T.ex. `/biljetter avboka Alvik`.")
            m = mina[0]
        else:
            m = self._hitta(a)
        if bekrafta:
            if a.nr:
                valda = [b for b in m.bokade if b.nr == a.nr]
            elif a.alla:
                valda = m.bokade
            else:
                valda = [b for b in m.bokade if b.bokad_av == (a.for_anvandare or anv)]
            if not valda:
                return self._gor_avbokning(anv, m.match_id, a)  # ger rätt felmeddelande
            vad = ", ".join(f"biljett {b.nr} ({beskriv_innehavare(b)})" for b in valda)
            fraga = f"Avboka {vad} till *{m.motstandare}* ({fmt.tid_text(m)})?"
            payload = {"h": "avboka", "m": m.match_id, "nr": a.nr, "alla": a.alla,
                       "f": a.for_anvandare, "a": anv}
            return self._bekraftelse(fraga, payload)
        return self._gor_avbokning(anv, m.match_id, a)

    def _gor_avbokning(self, anv: str, mid: str, a: Avsikt) -> Svar:
        r = self.svc.avboka(mid, anv, nr=a.nr, for_anvandare=a.for_anvandare, alla=a.alla)
        m = r.match
        vad = ", ".join(f"biljett {b.nr}" + (f" (<@{b.bokad_av}>)" if b.bokad_av != anv else "")
                        for b in r.avbokade)
        bekr = f"↩️ Avbokat {vad} till {m.motstandare} ({fmt.tid_text(m)})."
        notis = f"↩️ <@{anv}> avbokade {vad} till *{m.motstandare}* ({fmt.tid_text(m)})."
        if r.andras:
            andra = sorted({b.bokad_av for b in r.avbokade if b.bokad_av != anv})
            notis += " " + " ".join(f"<@{u}>" for u in andra) + " – din bokning är borttagen."
        blocks = None
        if r.ledig_notis and m.lediga:
            _, blocks = fmt.paminnelse(m, "Biljett ledig!")
            blocks = [fmt.sektion(notis)] + blocks
        return self._med_notis(bekr, notis, blocks, tvinga_notis=r.andras)

    def _med_notis(self, bekr: str, notis: str, notis_blocks: list | None = None,
                   tvinga_notis: bool = False) -> Svar:
        """Bokningar och avbokningar meddelas i biljettkanalen så att alla ser läget."""
        if self.cfg.kanal:
            return Svar(bekr, [fmt.sektion(bekr)], notiser=[Notis(notis, notis_blocks)])
        return Svar(notis, notis_blocks or [fmt.sektion(notis)], offentlig=True)

    def _bekraftelse(self, fraga: str, payload: dict, andra: str | None = None) -> Svar:
        varde = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        knappar = [fmt.knapp("Bekräfta", "bekrafta", varde, "primary")]
        if andra:
            knappar.append(fmt.knapp("Ändra…", "boka_modal", andra))
        knappar.append(fmt.knapp("Avbryt", "avbryt", "x"))
        return Svar(fraga.replace("*", ""), [fmt.sektion(fraga), fmt.handlingar("bekrafta", *knappar)],
                    personlig=True)

    def utfor(self, anv: str, payload: dict) -> Svar:
        """Utför en bekräftad handling (från knappen Bekräfta)."""
        if payload.get("a") != anv:
            return fel(f"Bara <@{payload.get('a')}> kan bekräfta den här åtgärden.")
        try:
            h = payload.get("h")
            if h == "boka":
                return self._gor_bokning(anv, payload["m"], payload["f"], payload["n"], payload["t"],
                                         payload.get("k"))
            if h == "avboka":
                a = Avsikt("avboka", nr=payload.get("nr"), alla=bool(payload.get("alla")),
                           for_anvandare=payload.get("f"))
                return self._gor_avbokning(anv, payload["m"], a)
            if h == "besok":
                return self._gor_besok(anv, payload)
        except Fel as e:
            return fel(e.meddelande)
        return fel("Okänd åtgärd.")

    # ---- knappar ---------------------------------------------------------------

    def knapp_boka_privat(self, anv: str, mid: str) -> Svar:
        try:
            return self._gor_bokning(anv, mid, anv, 1, "privat", None)
        except Fel as e:
            return fel(e.meddelande)

    def knapp_avboka_min(self, anv: str, mid: str) -> Svar:
        try:
            return self._gor_avbokning(anv, mid, Avsikt("avboka"))
        except Fel as e:
            return fel(e.meddelande)

    # ---- modal -------------------------------------------------------------------

    def _modal_svar(self, anv: str, mid: str | None, a: Avsikt | None = None) -> Svar:
        vy = self.boka_modal(anv, mid, a)
        if vy is None:
            return Svar("Det finns inga lediga biljetter till kommande matcher just nu.")
        return Svar("Öppnar bokningsformuläret…", modal=vy)

    def boka_modal(self, anv: str, mid: str | None, a: Avsikt | None = None,
                   kanal: str | None = None) -> dict | None:
        matcher = [m for m in self.svc.kommande() if m.lediga]
        if not matcher:
            return None
        def opt(m: Match) -> dict:
            t = f"{m.motstandare} – {fmt.tid_text(m)} ({len(m.lediga)} ledig{'a' if len(m.lediga) == 2 else ''})"
            return {"text": {"type": "plain_text", "text": t[:75]}, "value": m.match_id}
        def radio(val: str, text: str) -> dict:
            return {"text": {"type": "plain_text", "text": text}, "value": val}
        val_match = {"type": "static_select", "action_id": "v",
                     "placeholder": {"type": "plain_text", "text": "Välj match"},
                     "options": [opt(m) for m in matcher]}
        forvald = next((m for m in matcher if m.match_id == mid), None)
        if forvald:
            val_match["initial_option"] = opt(forvald)
        typ = (a.bokningstyp if a else None) or "privat"
        antal = str((a.antal if a else None) or 1)
        kund_input = {"type": "plain_text_input", "action_id": "v",
                      "placeholder": {"type": "plain_text", "text": "T.ex. Skellefteå kommun"}}
        if a and a.kund:
            kund_input["initial_value"] = a.kund
        return {
            "type": "modal", "callback_id": "boka_vy",
            "private_metadata": json.dumps({"kanal": kanal}),
            "title": {"type": "plain_text", "text": "Boka biljett"},
            "submit": {"type": "plain_text", "text": "Boka"},
            "close": {"type": "plain_text", "text": "Avbryt"},
            "blocks": [
                {"type": "input", "block_id": "match", "label": {"type": "plain_text", "text": "Match"},
                 "element": val_match},
                {"type": "input", "block_id": "antal", "label": {"type": "plain_text", "text": "Antal"},
                 "element": {"type": "radio_buttons", "action_id": "v",
                             "options": [radio("1", "1 biljett"), radio("2", "2 biljetter")],
                             "initial_option": radio(antal, "1 biljett" if antal == "1" else "2 biljetter")}},
                {"type": "input", "block_id": "typ", "label": {"type": "plain_text", "text": "Typ"},
                 "element": {"type": "radio_buttons", "action_id": "v",
                             "options": [radio("privat", "Privat"), radio("kund", "Kund")],
                             "initial_option": radio(typ, typ.capitalize())}},
                {"type": "input", "block_id": "kund", "optional": True,
                 "label": {"type": "plain_text", "text": "Kundnamn (om kund)"}, "element": kund_input},
                {"type": "input", "block_id": "for", "label": {"type": "plain_text", "text": "Boka åt"},
                 "element": {"type": "users_select", "action_id": "v",
                             "initial_user": (a.for_anvandare if a and a.for_anvandare else anv)}},
            ],
        }

    def boka_fran_modal(self, anv: str, values: dict) -> tuple[dict | None, Svar | None]:
        """Returnerar (valideringsfel per block, svar)."""
        def v(block: str):
            el = values.get(block, {}).get("v", {})
            if "selected_option" in el:
                return (el["selected_option"] or {}).get("value")
            if "selected_user" in el:
                return el["selected_user"]
            return el.get("value")
        mid, typ, kund, for_anv = v("match"), v("typ"), (v("kund") or "").strip(), v("for") or anv
        antal = int(v("antal") or 1)
        if typ == "kund" and not kund:
            return {"kund": "Ange kundnamn för kundbokningar."}, None
        try:
            return None, self._gor_bokning(anv, mid, for_anv, antal, typ, kund if typ == "kund" else None)
        except Fel as e:
            return {"match": e.meddelande[:150]}, None

    # ---- besök och statistik -----------------------------------------------------

    @staticmethod
    def _tolka_slash_besok(rest: str) -> Avsikt:
        """`besök <match> [biljett] 1|2 ja|nej [besökare...]`"""
        ord_ = rest.split()
        idx = next((i for i, o in enumerate(ord_) if vik(o) in ("ja", "nej")), None)
        fore = " ".join(ord_[:idx] if idx is not None else ord_)
        a = nlp.tolka(fore, tvingad_typ="besok")
        a.besokt = None
        a.besokare = None
        a.for_anvandare = None
        if idx is not None:
            a.besokt = vik(ord_[idx])
            a.besokare = " ".join(ord_[idx + 1:]).strip() or None
        if a.nr is None:
            siffra = [o for o in fore.split() if o in ("1", "2")]
            if siffra:
                a.nr = int(siffra[0])
        return a

    def _besok(self, anv: str, a: Avsikt, bekrafta: bool) -> Svar:
        if a.ref.tom:
            return self._besok_lista()
        m = self._hitta(a, foredra="spelad")
        if a.besokt is None:
            if bekrafta:
                return fel("Gick någon? Skriv t.ex. _jag gick på Visby-matchen_ eller "
                           "_biljett 2 till Brahe användes inte_.")
            return Svar("Öppnar formuläret…", modal=self.besok_modal(m.match_id))
        if a.nr:
            nrs = [a.nr]
        elif a.alla:
            nrs = [1, 2]
        else:
            vem = a.for_anvandare or anv
            egna = [b.nr for b in m.biljetter if b.bokad_av == vem]
            ofyllda = [b.nr for b in m.biljetter if not b.besokt]
            if egna:
                nrs = egna
            elif a.besokt == "ja" and ofyllda:
                nrs = ofyllda[:1]
            else:
                return fel(f"Ange vilken biljett det gäller (1 eller 2) för {m.motstandare}, "
                           f"t.ex. `/biljetter besök {m.match_id} 1 ja Anna`.")
        besokare = a.besokare
        if a.besokt == "ja" and not besokare and bekrafta:
            besokare = f"<@{anv}>"  # "jag gick på ..."
        payload = {"h": "besok", "m": m.match_id, "nrs": nrs, "b": a.besokt, "bes": besokare, "a": anv}
        if bekrafta:
            vad = f"besökt av {besokare}" if a.besokt == "ja" else "användes inte"
            fraga = (f"Registrera biljett {' och '.join(map(str, nrs))} till *{m.motstandare}* "
                     f"({fmt.tid_text(m)}) – {vad}?")
            return self._bekraftelse(fraga, payload)
        return self._gor_besok(anv, payload)

    def _gor_besok(self, anv: str, payload: dict) -> Svar:
        m = None
        for nr in payload["nrs"]:
            m = self.svc.registrera_besok(payload["m"], nr, anv, payload["b"], besokare=payload.get("bes"))
        rader = "\n".join(fmt.biljett_text(m.biljett(nr), m) for nr in payload["nrs"])
        text = f"📝 Registrerat för {m.motstandare} ({fmt.tid_text(m)}):\n{rader}"
        return Svar(text, [fmt.sektion(text)])

    def _besok_lista(self) -> Svar:
        spelade = self.svc.spelade()
        if not spelade:
            return Svar("Inga matcher är spelade än.")
        blocks = [fmt.sektion("*Spelade matcher* – välj en match för att registrera vem som gick:")]
        for m in spelade:
            saknas = sum(1 for b in m.biljetter if not b.besokt)
            info = "✅ ifyllt" if not saknas else f"⚠️ {saknas} biljett{'er' if saknas == 2 else ''} saknar uppgift"
            blocks.append(fmt.sektion(f"`{m.match_id}` {fmt.tid_text(m)} – {m.motstandare} · {info}",
                                      fmt.knapp("Registrera", "besok_modal", m.match_id)))
        return Svar("Spelade matcher – registrera besök", blocks[:50])

    def besok_modal(self, mid: str) -> dict:
        m = self.svc.match(mid)

        def opt(v: str, t: str) -> dict:
            return {"text": {"type": "plain_text", "text": t}, "value": v}

        ja, nej = "Ja, biljetten användes", "Nej, användes inte"
        blocks: list[dict] = [fmt.sektion(f"*{m.motstandare}* · {fmt.tid_text(m)}\n"
                                          "Fyll i vem som bokade och vem som faktiskt gick. "
                                          "Välj _Nej_ om biljetten inte användes.")]
        for b in m.biljetter:
            bokad = {"type": "users_select", "action_id": "v",
                     "placeholder": {"type": "plain_text", "text": "Vem bokade?"}}
            if b.bokad_av:
                bokad["initial_user"] = b.bokad_av
            typ = {"type": "static_select", "action_id": "v",
                   "placeholder": {"type": "plain_text", "text": "Privat/kund"},
                   "options": [opt("privat", "Privat"), opt("kund", "Kund")]}
            if b.typ:
                typ["initial_option"] = opt(b.typ, b.typ.capitalize())
            kund = {"type": "plain_text_input", "action_id": "v"}
            if b.kund:
                kund["initial_value"] = b.kund
            besokt = {"type": "radio_buttons", "action_id": "v", "options": [opt("ja", ja), opt("nej", nej)]}
            if b.besokt:
                besokt["initial_option"] = opt(b.besokt, ja if b.besokt == "ja" else nej)
            besokare = {"type": "plain_text_input", "action_id": "v",
                        "placeholder": {"type": "plain_text", "text": "Namn – kan vara en gäst"}}
            if b.besokare:
                besokare["initial_value"] = b.besokare
            n = b.nr
            blocks += [
                {"type": "header", "text": {"type": "plain_text", "text": f"Biljett {n}"}},
                {"type": "input", "block_id": f"bokad_av{n}", "optional": True,
                 "label": {"type": "plain_text", "text": "Bokad av"}, "element": bokad},
                {"type": "input", "block_id": f"typ{n}", "optional": True,
                 "label": {"type": "plain_text", "text": "Typ"}, "element": typ},
                {"type": "input", "block_id": f"kund{n}", "optional": True,
                 "label": {"type": "plain_text", "text": "Kund (om kund)"}, "element": kund},
                {"type": "input", "block_id": f"besokt{n}", "optional": True,
                 "label": {"type": "plain_text", "text": "Användes biljetten?"}, "element": besokt},
                {"type": "input", "block_id": f"besokare{n}", "optional": True,
                 "label": {"type": "plain_text", "text": "Vem gick?"}, "element": besokare},
            ]
        return {"type": "modal", "callback_id": "besok_vy", "private_metadata": json.dumps({"m": m.match_id}),
                "title": {"type": "plain_text", "text": "Registrera besök"},
                "submit": {"type": "plain_text", "text": "Spara"},
                "close": {"type": "plain_text", "text": "Avbryt"}, "blocks": blocks}

    def besok_fran_modal(self, anv: str, mid: str, values: dict) -> tuple[dict | None, Svar | None]:
        def v(block: str):
            el = values.get(block, {}).get("v", {})
            if "selected_option" in el:
                return (el["selected_option"] or {}).get("value")
            if "selected_user" in el:
                return el["selected_user"]
            return (el.get("value") or "").strip() or None

        m = self.svc.match(mid)
        fel_: dict[str, str] = {}
        andringar = []
        for b in m.biljetter:
            n = b.nr
            besokt, bokad_av, typ, kund, besokare = (v(f"besokt{n}"), v(f"bokad_av{n}"), v(f"typ{n}"),
                                                     v(f"kund{n}"), v(f"besokare{n}"))
            if not besokt:
                if (bokad_av, typ, kund, besokare) != (b.bokad_av, b.typ, b.kund, b.besokare) and \
                        any((bokad_av, typ, kund, besokare)):
                    fel_[f"besokt{n}"] = "Ange om biljetten användes."
                continue
            if typ == "kund" and not kund:
                fel_[f"kund{n}"] = "Ange kundnamn."
                continue
            andringar.append((n, besokt, besokare, bokad_av, typ, kund))
        if fel_:
            return fel_, None
        try:
            for n, besokt, besokare, bokad_av, typ, kund in andringar:
                m = self.svc.registrera_besok(mid, n, anv, besokt, besokare=besokare, bokad_av=bokad_av,
                                              typ=typ, kund=kund)
        except Fel as e:
            return {"besokt1": e.meddelande[:150]}, None
        rader = "\n".join(fmt.biljett_text(b, m) for b in m.biljetter)
        text = f"📝 Besök registrerat för {m.motstandare} ({fmt.tid_text(m)}):\n{rader}"
        return None, Svar(text, [fmt.sektion(text)])

    def _statistik(self, anv: str, a: Avsikt) -> Svar:
        from .statistik import berakna, format_statistik
        text = format_statistik(berakna(self.svc.matcher()))
        return Svar(text, [{"type": "header", "text": {"type": "plain_text", "text": "Biljettstatistik"}},
                           *[fmt.sektion(d) for d in text.split("\n\n")]])

    # ---- admin --------------------------------------------------------------------

    def export(self, anv: str, rest: str) -> Svar:
        if not self.svc.ar_admin(anv):
            return fel("Export får bara göras av administratörer.")
        from . import csvio
        data = csvio.exportera(self.svc.db, utokad="utökad" in rest or "utokad" in rest)
        namn = f"lulea_basket_hemmamatcher_{self.svc.nu():%Y%m%d_%H%M}.csv"
        return Svar(f"📄 Export klar: {namn}", fil=(namn, data))

    def admin(self, anv: str, rest: str) -> Svar:
        if not self.svc.ar_admin(anv):
            return fel("Adminkommandon får bara användas av administratörer (se ADMINS i konfigurationen).")
        try:
            delar = shlex.split(rest)
        except ValueError as e:
            return fel(f"Kunde inte tolka kommandot: {e}")
        if not delar:
            return Svar(ADMIN_HJALP, [fmt.sektion(ADMIN_HJALP)])
        kmd, args = vik(delar[0]), delar[1:]
        if kmd == "andra" and len(args) >= 2:
            falt = {}
            for x in args[1:]:
                k, sep, v = x.partition("=")
                if not sep:
                    return fel(f"Ange fält som namn=värde, t.ex. tid=18:00 (fick {x!r}).")
                nyckel = vik(k)
                falt[nyckel] = v if v else ("" if nyckel == "anmarkning" else None)
            r = self.svc.andra_match(args[0], anv, **falt)
            if not r.falt:
                return Svar("Inga ändringar.")
            diff = ", ".join(f"{k}: {a} → {b}" for k, (a, b) in r.falt.items())
            text = f"✏️ <@{anv}> ändrade {r.match.match_id} {r.match.motstandare}: {diff}"
            if r.match.bokade:
                text += " · " + " ".join(f"<@{b.bokad_av}>" for b in r.match.bokade) + " – du har biljett till matchen."
            return self._med_notis(f"✅ {r.match.match_id} uppdaterad: {diff}", text)
        if kmd == "ny" and len(args) >= 3:
            nyckelvarden = {k: v for k, _, v in (x.partition("=") for x in args if "=" in x)}
            fria = [x for x in args if "=" not in x]
            datum, tid_ = fria[0], fria[1]
            motst = " ".join(fria[2:])
            m = self.svc.ny_match(anv, datum, tid_, motst, serie=nyckelvarden.get("serie", "grundserie"),
                                  anmarkning=nyckelvarden.get("anmarkning", ""))
            return self._med_notis(f"✅ Lade till {m.match_id}.",
                                   f"➕ <@{anv}> lade till {fmt.matchrad(m)}")
        if kmd == "radera" and args:
            m = self.svc.radera_match(args[0], anv, tvinga="tvinga" in args[1:])
            return self._med_notis(f"🗑️ Raderade {m.match_id}.",
                                   f"🗑️ <@{anv}> raderade {m.match_id} {m.motstandare} {m.datum}.")
        if kmd == "logg":
            n = int(args[0]) if args and args[0].isdigit() else 15
            rader = [f"`{r['tid'][:16]}` <@{r['aktor']}> {r['handling']} {r['match_id'] or ''} {r['detaljer'] or ''}"
                     if r["aktor"].startswith("U") else
                     f"`{r['tid'][:16]}` {r['aktor']} {r['handling']} {r['match_id'] or ''} {r['detaljer'] or ''}"
                     for r in self.svc.db.audit(n)]
            text = "*Senaste ändringar*\n" + "\n".join(rader)
            return Svar(text, [fmt.sektion(text)])
        if kmd == "synka":
            if self.synka is None:
                return fel("Schemasynk är inte aktiverad.")
            return self.synka(anv, "tvinga" in args)
        return Svar(ADMIN_HJALP, [fmt.sektion(ADMIN_HJALP)])


ADMIN_HJALP = (
    "*Adminkommandon*\n"
    "• `/biljetter admin ändra M05 tid=18:00 datum=2026-11-28 status=inställd anmarkning=\"Kidzday\"`\n"
    "• `/biljetter admin ny 2027-03-20 18:00 Alvik Basket serie=slutspel`\n"
    "• `/biljetter admin radera M12 [tvinga]`\n"
    "• `/biljetter admin logg [antal]` – senaste händelserna i audit-loggen\n"
    "• `/biljetter admin synka tvinga` – synka även om valideringen varnar\n"
    "• `/biljetter export [utökad]` – CSV i startunderlagets format"
)

