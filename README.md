# Biljettboten – Driftwind × Luleå Basket

Slack-bot för Driftwinds två biljetter per hemmamatch till Luleå Basket (SBL Dam, Luleå Energi Arena).
Allt sköts i Slack: schema, bokning, påminnelser, besökshistorik, statistik och uppdatering av spelschemat.

- **Schema:** nästa match, kommande matcher, hela säsongen. Status `kommande`/`spelad`/`inställd`/`flyttad`; `spelad` sätts automatiskt 3 h efter uppkast.
- **Bokning:** en eller båda biljetterna, `privat` eller `kund` (med kundnamn), åt sig själv eller åt någon annan. Dubbelbokning är omöjlig – boten säger vem som har biljetterna och vad bokningen gäller.
- **Påminnelser:** på matchdagen (konfigurerbart) om minst en biljett är ledig, med knappar för att boka direkt. Avbokning nära match ger en "biljett ledig"-post.
- **Besökshistorik och statistik:** vem som faktiskt gick, oanvända biljetter, topplistor, privat/kund, kunder.
- **Schemasynk:** daglig hämtning av [luleabasket.com/matcher](https://luleabasket.com/matcher/); nya matcher, ändrade tider och inställda matcher förs in automatiskt och sammanfattas i kanalen. Bokade biljetter rörs aldrig.

## Innehåll

1. [Använda boten](#använda-boten)
2. [Sätta upp Slack-appen](#sätta-upp-slack-appen)
3. [Konfiguration](#konfiguration)
4. [Drift på Railway](#drift-på-railway)
5. [Utveckling och tester](#utveckling-och-tester)
6. [Arkitektur och teknikval](#arkitektur-och-teknikval)
7. [Datamodell](#datamodell)
8. [Schemasynken och när sidan ändras](#schemasynken-och-när-sidan-ändras)
9. [Rättigheter](#rättigheter)
10. [Backup, export och audit-logg](#backup-export-och-audit-logg)

---

## Använda boten

### Slash-kommandon

| Kommando | Gör |
|---|---|
| `/biljetter` eller `/biljetter nästa` | Nästa match och vem som har biljetterna |
| `/biljetter kommande` · `/biljetter säsong` | Kommande matcher · hela säsongen |
| `/biljetter status <match>` | Vem har biljetterna (och besök för spelade matcher) |
| `/biljetter boka <match> [1\|båda] [privat\|kund: Namn] [åt @person]` | Boka. Utan match öppnas ett formulär |
| `/biljetter avboka <match> [biljett 1\|2] [@person]` | Avboka din (eller angiven) biljett |
| `/biljetter besök` | Lista spelade matcher med knapp för att fylla i besök |
| `/biljetter besök <match> [1\|2] ja\|nej [besökare]` | Registrera vem som gick / att biljetten inte användes |
| `/biljetter statistik` | Besök, topplista, använda/oanvända, privat/kund, kunder |
| `/biljetter synka` | Hämta schemat från luleabasket.com nu |
| `/biljetter hjälp` | Hjälp |
| `/biljetter export [utökad]` | *Admin:* CSV i startunderlagets format, skickas som DM |
| `/biljetter admin …` | *Admin:* `ändra`, `ny`, `radera`, `logg`, `synka tvinga`, `återställ` |

**Match** kan anges som motståndare (`Alvik`, `östersund`, `norrköpingsmatchen` – stavfel tolereras), datum (`18 dec`, `18/12`, `2026-12-18`), veckodag (`fredag`), `nästa`, `idag`/`imorgon` eller match-id (`M05`).

### Fritext

Skriv till boten i DM eller nämn den i kanalen (`@biljetter …`):

> boka en biljett till Alvik, kund: Skellefteå kommun
> boka båda till Östersund åt @anna
> vem har biljetterna 18 dec?
> avboka min biljett på fredag
> jag gick på Visby-matchen
> biljett 2 till Brahe användes inte
> vem har gått flest gånger?

Tolkningen är **regelbaserad** (ingen AI-tjänst; texten lämnar inte Slack). Bokning, avbokning och besöksregistrering från fritext visas först som en fråga med knapparna **Bekräfta / Ändra… / Avbryt**, och bara den som frågade kan bekräfta. Frågor ("vem…", "när…", "finns det…") tolkas aldrig som skrivåtgärder. Förstår boten inte, eller är matchen oklar, svarar den med ett förslag.

### Knappar och formulär

- Påminnelser och statuskort har **Boka (privat)** och **Boka för kund / åt någon…** (formulär med match, antal, typ, kund och "boka åt").
- Statuskort har **Avboka min biljett**; spelade matcher har **Registrera besök** (formulär där även bokare, typ och kund kan fyllas i i efterhand – t.ex. för M01–M04).
- Bokningar och avbokningar meddelas i biljettkanalen. Avbokar någon en annans biljett @-nämns den som hade den.

---

## Sätta upp Slack-appen

1. Gå till <https://api.slack.com/apps> → **Create New App** → **From a manifest**, välj arbetsytan och klistra in [`slack_manifest.yml`](slack_manifest.yml).
   Manifestet ger:
   - **Socket Mode** (ingen publik URL behövs) och interaktivitet (knappar, formulär).
   - **Slash-kommando** `/biljetter` med *Escape channels, users, and links* påslaget (krävs för `åt @person`).
   - **Events:** `app_mention`, `message.im`. **Messages Tab** i App Home är påslagen så att man kan DM:a boten.
   - **Bot-scopes:**

     | Scope | Varför |
     |---|---|
     | `commands` | `/biljetter` |
     | `chat:write` | svar, påminnelser, notiser |
     | `app_mentions:read` | fritext när boten nämns |
     | `im:history`, `im:read`, `im:write` | fritext i DM, svar efter formulär, export som DM |
     | `channels:read`, `groups:read` | kontrollera att användaren är med i biljettkanalen (publik resp. privat kanal) |
     | `files:write` | export av CSV |
2. **Basic Information → App-Level Tokens → Generate:** scope `connections:write`. Detta är `SLACK_APP_TOKEN` (`xapp-…`).
3. **Install App** till arbetsytan. *Bot User OAuth Token* är `SLACK_BOT_TOKEN` (`xoxb-…`).
4. Bjud in boten till biljettkanalen: `/invite @biljetter`. Kanalens ID (`C…`) finns längst ned i kanalinformationen → `BILJETT_KANAL`.
5. Hämta Slack-ID för administratörer (profil → ⋮ → *Copy member ID*) → `ADMINS`.

---

## Konfiguration

Allt ställs in med miljövariabler (lokalt via `.env`, se [`.env.example`](.env.example)). Inga hemligheter finns i repot.

| Variabel | Standard | Beskrivning |
|---|---|---|
| `SLACK_BOT_TOKEN` | – | **Obligatorisk.** `xoxb-…` |
| `SLACK_APP_TOKEN` | – | **Obligatorisk.** `xapp-…` (Socket Mode) |
| `BILJETT_KANAL` | – | **Obligatorisk.** Kanal-ID för påminnelser, notiser och synk-diffar |
| `ADMINS` | tom | Kommaseparerade Slack-ID:n med adminrätt |
| `KRAV_KANALMEDLEMSKAP` | `true` | Bara medlemmar i biljettkanalen får använda boten |
| `PAMINNELSER` | `0@10:00` | `dagar_före@HH:MM`, kommaseparerat, t.ex. `3@09:00,0@10:00` |
| `AVBOKNING_NOTIS_DAGAR` | `7` | Avbokning så nära match ger "biljett ledig"-post med bokningsknappar |
| `SPELAD_EFTER_TIMMAR` | `3` | När `spelad` sätts efter uppkast |
| `SCHEMA_URL` | `https://luleabasket.com/matcher/` | Schemakälla |
| `HEMMALAG` / `ARENA` | `Luleå Basket` / `Luleå Energi Arena` | |
| `SYNK_TIDER` | `06:30` | Klockslag för daglig synk, kommaseparerat |
| `SYNK_MAX_MINSKNING` | `0.3` | Avbryt synk om antalet hemmamatcher minskar mer än 30 % |
| `SYNK_MAX_NYA` | `8` | Avbryt synk om fler nya matcher än så dyker upp |
| `SYNK_SAKNAD_GRANS` | `2` | Synkar i rad en match får saknas innan den sätts `inställd` |
| `TIDSZON` | `Europe/Stockholm` | All visning och alla klockslag; sommar-/vintertid hanteras |
| `DATABAS_SOKVAG` | `data/biljetter.db` (container: `/data/biljetter.db`) | SQLite-fil |
| `CSV_IMPORT_SOKVAG` | `docs/lulea_basket_hemmamatcher_2026-27.csv` | Importeras vid första start om databasen är tom |
| `LOGGNIVA` | `INFO` | |

---

## Drift på Railway

Boten är en enda långlivad process (Socket Mode + schemaläggare). Den behöver ingen publik port eller domän.

1. **New Project → Deploy from GitHub repo** och välj repot. Railway bygger med [`Dockerfile`](Dockerfile) enligt [`railway.json`](railway.json) (1 replik, omstart vid fel).
2. **Lägg till en volym** på tjänsten (*Settings → Volumes* eller högerklick → *Attach volume*) med mount path **`/data`**. Databasen `/data/biljetter.db` överlever då omstarter och omdeployer.
3. **Variables:** lägg in `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `BILJETT_KANAL`, `ADMINS` och ev. övriga från tabellen ovan. Sätt **inte** `DATABAS_SOKVAG` till en relativ sökväg – Dockerfile pekar redan på volymen.
4. Deploya. Loggen ska visa `Importerade 11 matcher…` (bara första gången), `Schemaläggare startad…` och `Startar biljettboten (Socket Mode)`.
5. Testa med `/biljetter` i Slack och kör `/biljetter synka` en gång – första synken kopplar matcherna till sidan tyst ("Schemat är oförändrat").

**Viktigt:** kör bara **en** instans. Påminnelser är ändå skyddade mot dubbelutskick i databasen, men två processer mot samma SQLite-fil ska undvikas. Railway stoppar den gamla instansen innan den nya startar när en volym är monterad.

**Tålighet vid omstart:** bokningar skrivs transaktionellt direkt i SQLite (WAL). Påminnelser reserveras i databasen innan de skickas och skickas därför aldrig två gånger; missade påminnelser (boten nere kl. 10) skickas när boten startar igen om matchen inte har börjat. `spelad` och synk är idempotenta.

### Testa innan lansering – och återställ

Boka, avboka, registrera besök och kör synk fritt under testperioden (gärna med `BILJETT_KANAL` satt till en testkanal). När ni ska gå live:

1. Kör `/biljetter admin återställ` och tryck **Bekräfta**.
   - Alla bokningar, besök, påminnelser, synkhistorik och audit-loggen raderas och [startunderlaget](docs/lulea_basket_hemmamatcher_2026-27.csv) importeras på nytt – i en transaktion. Startunderlaget valideras först; är det trasigt ändras ingenting.
   - Du får en export av läget före återställningen som DM (backup), och kanalen får en kort notis.
   - `spelad` sätts direkt för matcher som hunnit spelas. Kopplingen till luleabasket.com görs om vid nästa synk.
2. Byt ev. `BILJETT_KANAL` till den riktiga kanalen och starta om tjänsten.
3. Kör `/biljetter synka` en gång så att schemat är aktuellt.

Vill du ändra startpunkten (t.ex. lägga in bokningar som redan gjorts utanför Slack) redigerar du CSV-filen och deployar innan du återställer. Från ett skal i containern fungerar även `python -m biljettbot.cli aterstall --ja`.

**Annan värd:** `cp .env.example .env`, fyll i och kör `docker compose up -d` ([`docker-compose.yml`](docker-compose.yml) har en namngiven volym för `/data`).

---

## Utveckling och tester

```bash
python -m venv .venv
.venv/Scripts/activate            # Windows (bash: source .venv/Scripts/activate) – macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env              # fyll i tokens för en test-arbetsyta
python -m biljettbot              # startar boten
pytest                            # alla tester, inget nätverk krävs
```

Testerna täcker bokningslogik (inkl. samtidiga bokningar från flera trådar), tolkning av fritext, kommandon, påminnelseregler och idempotens, besök/statistik, parsern (mot en sparad kopia av källsidan i [`tests/fixtures/`](tests/fixtures/)) och schemadiffen (ändrad tid/datum, inställd, saknad, ny slutspelsmatch, avbruten synk vid stora avvikelser, trasig sida).

Kommandorad för drift/felsökning:

```bash
python -m biljettbot.cli import [fil.csv]       # importera till tom databas
python -m biljettbot.cli export [ut.csv] [--utokad]
```

---

## Arkitektur och teknikval

```
biljettbot/
  __main__.py     start: import vid första start, schemaläggare, Socket Mode
  config.py       miljövariabler
  db.py           SQLite-schema, modeller (Match, Biljett), transaktioner, audit-logg
  csvio.py        import/export i startunderlagets CSV-format
  service.py      schema- och bokningslogik (ingen Slack)
  nlp.py          regelbaserad tolkning av fritext och matchreferenser
  kommandon.py    kommandon/fritext → Svar (ingen Slack, testbar)
  format.py       Block Kit-meddelanden
  slack_app.py    Bolt-handlers: /biljetter, DM, @-omnämnanden, knappar, formulär
  paminnelser.py  påminnelseregler och idempotent utskick
  statistik.py    besökshistorik och statistik
  kalla.py        hämtning och parsning av luleabasket.com/matcher
  synk.py         diff, validering, applicering och diff-meddelanden
  jobb.py         APScheduler: tick var 5:e minut, daglig synk
```

- **Python + Slack Bolt (Socket Mode):** Bolt för Python är Slacks officiella ramverk och är moget. Python passar bra för HTML-parsning och tester, och Socket Mode gör att ingen publik URL, TLS eller webbserver behövs – lätt att köra på Railway.
- **SQLite** (WAL, `BEGIN IMMEDIATE`): en fil, ingen separat databastjänst, riktiga transaktioner. Bokning läser och skriver i samma skrivlåsta transaktion och uppdateringen är dessutom villkorad (`… WHERE bokad_av IS NULL`), så två samtidiga bokningar kan aldrig få samma biljett.
- **APScheduler i samma process:** inga extra tjänster. Jobben är idempotenta mot databasen.
- **Regelbaserad fritext:** förutsägbar, gratis och utan att meddelanden lämnar Slack. Skrivåtgärder bekräftas med knapp, så en feltolkning kan inte ändra något i tysthet.

---

## Datamodell

Samma begrepp och kolumner som [`docs/README_biljettunderlag.md`](docs/README_biljettunderlag.md). CSV-filen är startunderlag; exporten skriver tillbaka exakt samma format.

| CSV | Databas |
|---|---|
| `match_id`, `datum`, `tid`, `motstandare`, `arena`, `anmarkning`, `status`, `senast_uppdaterad` | `matches` (samma kolumnnamn) |
| `veckodag` | härleds från `datum` |
| `biljettN_bokad_av`, `_typ`, `_kund`, `_besokt`, `_besokare` | `tickets` (en rad per match och biljett `nr` 1/2) + `skapad_av`, `bokad_tid` |
| `paminnelse_skickad` | `reminders_sent` (en rad per match och påminnelseregel); exporten visar senaste tidpunkten |
| – | `matches.serie` (`grundserie`/`slutspel`), `kalla_id` (sidans data-id), `kalla_anmarkning`, `saknad_i_synk` |
| – | `audit_log` (vem gjorde vad och när), `sync_runs` (varje synk och dess utfall), `meta` |

Datum lagras som `YYYY-MM-DD`, klockslag som `HH:MM` lokal tid och tidsstämplar som ISO 8601 med offset (`2026-11-27T10:00:00+01:00`).

Tolkningar som gjorts:
- **Bokad av** är den biljetten gäller (vid "boka åt @anna" är det Anna); den som utförde bokningen sparas i `skapad_av` och i audit-loggen.
- **Besökare** är fritext (kan vara en gäst) eller ett @-omnämnande. Registreras "ja" utan namn används den som bokade.
- **Ändrat datum/tid** uppdaterar matchen på plats (`kommande`, samma `match_id`, bokningarna ligger kvar, bokarna @-nämns). `flyttad` = uppskjuten utan nytt datum. `inställd` = inställd på sidan eller saknad i två synkar i rad.
- Bokning är möjlig för kommande matcher fram till uppkast; avbokning fram till att matchen är spelad. Spelade matcher hanteras via besöksregistrering.

---

## Schemasynken och när sidan ändras

Synken körs dagligen (`SYNK_TIDER`) och med `/biljetter synka`:

1. Hämtar sidan och parsar hemmamatcher (`Luleå Basket - Motståndare`; bortamatcher hoppas över).
2. Matchar mot lagrade matcher på sidans data-id, annars datum + motståndare, annars unik motståndare.
3. **Validerar** – avbryter och varnar i kanalen utan att ändra något om:
   - sidan inte kan hämtas eller strukturen inte känns igen (`.matchContainer`, månadsrubriker, datum, lagnamn),
   - inga hemmamatcher hittas,
   - antalet hemmamatcher minskar mer än `SYNK_MAX_MINSKNING`,
   - fler än `SYNK_MAX_NYA` nya matcher eller fler än 3 nya inställda dyker upp på en gång.
4. Applicerar allt i en transaktion (aldrig bokningsfälten) och postar ett diff-meddelande. Berörs en match med bokade biljetter postas en separat notis med @-omnämnande. Inga ändringar → inget meddelande.

**Om synken varnar för att sidan ändrats:**
1. Öppna sidan och jämför med [`biljettbot/kalla.py`](biljettbot/kalla.py) (strukturen är dokumenterad överst i filen).
2. Spara den nya sidan som ny fixtur i `tests/fixtures/`, uppdatera parsern tills `pytest tests/test_kalla.py` går igenom, och deploya.
3. Under tiden fungerar bokning som vanligt; admins kan ändra schemat manuellt med `/biljetter admin ändra|ny`.

Är varningen en falsklarm (t.ex. klubben har faktiskt strukit många matcher) kör en admin `/biljetter admin synka tvinga`.

---

## Rättigheter

| Vem | Får |
|---|---|
| Alla i biljettkanalen | se schema/status/statistik, boka (även åt andra), avboka (även andras – meddelas öppet och personen @-nämns), registrera besök, köra synk |
| Admins (`ADMINS`) | dessutom manuell schemaändring (`admin ändra`, `admin ny`), radering (`admin radera`, kräver `tvinga` om matchen har bokningar eller historik), `admin logg`, `admin synka tvinga`, export |

Förslaget bygger på att gruppen är liten och att allt är spårbart: varje ändring loggas i `audit_log` och bokningar/avbokningar syns i kanalen. Vill ni strama åt kan avbokning av andras biljetter enkelt begränsas till admins i `service.avboka`.

---

## Backup, export och audit-logg

- `/biljetter export` (admin) skickar en CSV i startunderlagets format som DM; `export utökad` tar även med `serie` och `kalla_id` så att filen kan återimporteras utan att något går förlorat.
- `/biljetter admin logg [antal]` visar de senaste händelserna i audit-loggen.
- Databasen är en enda fil på volymen (`/data/biljetter.db`). Ta gärna en export före säsongsstart och efter slutspel.
- Återställning: lägg en *utökad* export på volymen (t.ex. via ett skal i containern, `railway ssh`), peka `DATABAS_SOKVAG` på en ny tom fil och sätt `CSV_IMPORT_SOKVAG` till exporten – den importeras vid nästa start. Alternativt `python -m biljettbot.cli import <fil>` mot en tom databas.

## Kända begränsningar

- Schemakällan är en vanlig webbsida utan API – se avsnittet om synken ovan.
- Säsongsbyte: den nya säsongens schema ger många nya matcher på en gång och stoppas av valideringen. Kör `admin synka tvinga` när ni vill ta in den.
- Fritexttolkningen täcker formuleringarna ovan och varianter av dem; för ovanliga formuleringar finns slash-kommandona och formulären.
