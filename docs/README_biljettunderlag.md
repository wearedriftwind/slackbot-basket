# Driftwind × Luleå Basket – biljettunderlag

Fil: `lulea_basket_hemmamatcher_2026-27.csv` (UTF-8, kommaseparerad). En rad per hemmamatch, SBL Dam grundserie 2026/27. Driftwind har två biljetter per match. Källa för schemat: luleabasket.com/matcher (hämtat 2026-11-20).

## Kolumner
| Kolumn | Format / tillåtna värden | Betydelse |
|---|---|---|
| match_id | M01–M11 | Stabilt ID, ändras aldrig |
| datum | YYYY-MM-DD | Matchdag |
| veckodag | text | Bara för läsbarhet |
| tid | HH:MM (Europe/Stockholm) | Uppkast |
| motstandare | text | Bortalag |
| arena | text | Alltid Luleå Energi Arena |
| anmarkning | text | T.ex. Premiär, Kidzday |
| status | `kommande` / `spelad` / `inställd` / `flyttad` | Boten sätter `spelad` när datum passerat |
| biljettN_bokad_av | Slack-användar-ID (U…) eller tomt | Tomt = biljetten är ledig |
| biljettN_typ | `privat` / `kund` / tomt | Typ av bokning |
| biljettN_kund | text | Kundnamn, bara om typ = kund |
| biljettN_besokt | `ja` / `nej` / tomt | Fylls i efter match. `nej` = biljetten användes inte |
| biljettN_besokare | text | Vem som faktiskt gick (kan vara gäst, inte bara den som bokade) |
| paminnelse_skickad | ISO 8601 tidsstämpel eller tomt | Sätts när ledig-biljett-påminnelse skickats |
| senast_uppdaterad | ISO 8601 tidsstämpel | Sätts vid varje ändring |

N = 1 eller 2 (de två biljetterna).

## Härledd logik (för boten)
- Ledig biljett: `biljettN_bokad_av` tom och `status = kommande`.
- Påminnelse: kommande match med minst en ledig biljett och tom `paminnelse_skickad`. Föreslagen tidpunkt: några dagar före match, upprepa ev. matchdagen.
- Besökshistorik: rader med `status = spelad`; sammanställ via `biljettN_besokt = ja` och `biljettN_besokare`.
- Kolumnerna för spelade matcher (M01–M04) är tomma. Historiken finns inte i underlaget och fylls i manuellt eller via boten.

## Att tänka på i Code
- CSV funkar för en prototyp, men två personer som bokar samtidigt kan skriva över varandra. Överväg SQLite eller Notion/Google Sheets som lagring, med samma kolumner.
- Schemat kan ändras (tider flyttas, matcher läggs om). Bygg gärna en synk mot luleabasket.com/matcher som uppdaterar datum/tid utan att röra bokningskolumnerna.
- Slutspelsmatcher är inte med ännu och behöver läggas till som nya rader (ny `match_id`, ev. kolumnen `serie`).
