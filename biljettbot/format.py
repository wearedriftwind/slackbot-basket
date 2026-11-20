"""Text och Block Kit för Slack-svar."""
from __future__ import annotations

from datetime import date, datetime

from .db import Biljett, Match
from .tid import VECKODAGAR, kort_datum, langt_datum

STATUS_IKON = {"spelad": "✅", "inställd": "❌", "flyttad": "⏸️"}


def sektion(text: str, accessory: dict | None = None) -> dict:
    b = {"type": "section", "text": {"type": "mrkdwn", "text": text[:3000]}}
    if accessory:
        b["accessory"] = accessory
    return b


def kontext(text: str) -> dict:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": text[:3000]}]}


def knapp(text: str, action_id: str, value: str, style: str | None = None) -> dict:
    b = {"type": "button", "text": {"type": "plain_text", "text": text, "emoji": True},
         "action_id": action_id, "value": value}
    if style:
        b["style"] = style
    return b


def handlingar(block_id: str, *knappar: dict) -> dict:
    return {"type": "actions", "block_id": block_id, "elements": list(knappar)}


def tid_text(m: Match) -> str:
    return f"{kort_datum(m.datum)} {m.tid or '(tid ej satt)'}"


def matchnamn(m: Match) -> str:
    return f"*{m.motstandare}* · {tid_text(m)}"


def ledighet(m: Match) -> str:
    if m.status in STATUS_IKON:
        return f"{STATUS_IKON[m.status]} {m.status}"
    n = len(m.lediga)
    return {2: "🟢 2 lediga", 1: "🟡 1 ledig", 0: "🔴 fullbokad"}[n]


def biljett_text(b: Biljett, m: Match) -> str:
    if not b.bokad and not b.besokt:
        rad = f"Biljett {b.nr}: _ledig_" if m.status == "kommande" else f"Biljett {b.nr}: –"
    else:
        vem = f"<@{b.bokad_av}>" if b.bokad_av else "okänd"
        rad = f"Biljett {b.nr}: {vem} – {b.beskrivning()}" if b.typ else f"Biljett {b.nr}: {vem}"
    if b.besokt == "ja":
        rad += f" · besökt av {b.besokare or 'okänd'}"
    elif b.besokt == "nej":
        rad += " · användes inte"
    return "🎟️ " + rad


def matchrad(m: Match) -> str:
    serie = f" · {m.serie}" if m.serie != "grundserie" else ""
    anm = f" · _{m.anmarkning}_" if m.anmarkning else ""
    return f"`{m.match_id}` {tid_text(m)} – {m.motstandare}{anm}{serie} · {ledighet(m)}"


def lista(rubrik: str, matcher: list[Match], tom: str = "Inga matcher.") -> tuple[str, list[dict]]:
    if not matcher:
        return tom, [sektion(f"*{rubrik}*\n{tom}")]
    rader = [matchrad(m) for m in matcher]
    blocks = [{"type": "header", "text": {"type": "plain_text", "text": rubrik[:150]}}]
    # dela upp så att varje sektion håller sig under 3000 tecken
    bit: list[str] = []
    for r in rader:
        if sum(len(x) + 1 for x in bit) + len(r) > 2800:
            blocks.append(sektion("\n".join(bit)))
            bit = []
        bit.append(r)
    blocks.append(sektion("\n".join(bit)))
    blocks.append(kontext("Boka: `/biljetter boka <motståndare|datum|Mxx>` eller skriv till mig, "
                          "t.ex. _boka en biljett till Alvik_."))
    return f"{rubrik}: " + "; ".join(f"{m.match_id} {m.motstandare}" for m in matcher), blocks


def bokningsknappar(m: Match, block_id: str) -> dict | None:
    if not m.lediga:
        return None
    return handlingar(block_id,
                      knapp("🎟️ Boka (privat)", "boka_privat", m.match_id, "primary"),
                      knapp("Boka för kund / åt någon…", "boka_modal", m.match_id))


def status(m: Match) -> tuple[str, list[dict]]:
    rubrik = f"{matchnamn(m)}\n{m.arena}"
    if m.anmarkning:
        rubrik += f" · _{m.anmarkning}_"
    if m.status != "kommande":
        rubrik += f"\n{STATUS_IKON.get(m.status, '')} Status: *{m.status}*"
    rader = "\n".join(biljett_text(b, m) for b in m.biljetter)
    blocks = [sektion(f"{rubrik}\n{rader}")]
    knappar = []
    if m.lediga:
        knappar += [knapp("🎟️ Boka (privat)", "boka_privat", m.match_id, "primary"),
                    knapp("Boka för kund / åt någon…", "boka_modal", m.match_id)]
    if m.bokade and m.status in ("kommande", "flyttad"):
        knappar.append(knapp("Avboka min biljett", "avboka_min", m.match_id))
    if m.status == "spelad":
        knappar.append(knapp("📝 Registrera besök", "besok_modal", m.match_id))
    if knappar:
        blocks.append(handlingar(f"status:{m.match_id}", *knappar))
    blocks.append(kontext(f"`{m.match_id}`"))
    text = f"{m.motstandare} {tid_text(m)}: " + "; ".join(
        biljett_text(b, m).removeprefix("🎟️ ") for b in m.biljetter)
    return text, blocks


def paminnelse_rubrik(m: Match, nu: datetime) -> str:
    dagar = (date.fromisoformat(m.datum) - nu.date()).days
    if dagar == 0:
        return "Match idag!"
    if dagar == 1:
        return "Match imorgon!"
    if 1 < dagar < 7:
        return f"Match på {VECKODAGAR[date.fromisoformat(m.datum).weekday()]}!"
    return "Hemmamatch!"


def paminnelse(m: Match, rubrik: str) -> tuple[str, list[dict]]:
    n = len(m.lediga)
    ledigt = "Båda biljetterna är lediga" if n == 2 else "En biljett är ledig" if n == 1 else \
        "Biljetterna är bokade – tack!"
    text = f"{rubrik} Luleå Basket – {m.motstandare}, {langt_datum(m.datum)} kl. {m.tid or '?'}. {ledigt}."
    blocks = [sektion(f"🏀 *{rubrik}*\n*Luleå Basket – {m.motstandare}*\n"
                      f"📅 {langt_datum(m.datum)} kl. {m.tid or '?'}\n📍 {m.arena}"
                      + (f" · _{m.anmarkning}_" if m.anmarkning else "")
                      + f"\n🎟️ {ledigt}.")]
    k = bokningsknappar(m, f"paminnelse:{m.match_id}")
    if k:
        blocks.append(k)
    return text, blocks


def hjalp() -> tuple[str, list[dict]]:
    text = (
        "*Driftwinds biljetter till Luleå Basket* – två biljetter per hemmamatch.\n\n"
        "*Kommandon*\n"
        "• `/biljetter nästa` – nästa match och vem som har biljetterna\n"
        "• `/biljetter kommande` · `/biljetter säsong` – kommande matcher / hela säsongen\n"
        "• `/biljetter status <match>` – vem har biljetterna\n"
        "• `/biljetter boka <match> [1|båda] [privat|kund: Namn] [åt @person]`\n"
        "• `/biljetter avboka <match> [biljett 1|2] [@person]`\n"
        "• `/biljetter besök <match> [biljett 1|2] ja|nej [besökare]` – registrera vem som gick\n"
        "• `/biljetter statistik` – besök, topplista, kunder\n"
        "• `/biljetter synka` – hämta schemat från luleabasket.com nu\n\n"
        "*Match* anges med motståndare (_Alvik_), datum (_18 dec_, _2026-12-18_), "
        "veckodag (_fredag_), _nästa_ eller match-id (_M05_).\n\n"
        "*Skriv fritt* till mig i DM eller nämn mig i kanalen, t.ex.\n"
        "> boka en biljett till Alvik, kund: Skellefteå kommun\n"
        "> vem har biljetterna 18 dec?\n"
        "> jag gick på Visby-matchen\n"
        "Bokningar och avbokningar bekräftas med en knapp innan de genomförs.\n\n"
        "*Admin*: `/biljetter admin ändra|ny|radera|logg`, `/biljetter export`"
    )
    return "Hjälp för biljettboten", [sektion(text)]
