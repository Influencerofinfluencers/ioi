# IOI – Influencer of Influencers (v0.2)

Volgt 50 invloedrijke crypto-accounts op X, YouTube, Telegram en Instagram (Bluesky werkt ook). Het systeem ziet **wie een onderwerp als eerste noemt**, **wie volgt** en **waar de top-accounts het over eens zijn**. Daarmee maakt het:

- elke 10 minuten: nieuwe posts ophalen, labelen met AI en **Pro-alerts** op Telegram bij een aandachtspiek;
- elke ochtend: een **dagelijkse digest** (Claude), het gratis Telegram-kanaal, een **website + RSS-feed** en het **IOI Top 50-leaderboard**;
- elke maandag: een **Weekly Playbook** dat uitzoekt *waarom* de best scorende posts van de week werkten (format, openingszin, tijdstip, onderwerp). Dat is de "reverse engineering" uit het plan, en een los te verkopen rapport;
- statusberichten en foutmeldingen naar jouw eigen Telegram, inclusief een waarschuwing als er 24 uur geen nieuwe posts binnenkomen of als runs worden overgeslagen.

Alles draait in de cloud (GitHub Actions + Supabase + GitHub Pages). Je hebt geen eigen server, pc of vast internet nodig: alles is in te stellen via de browser op je telefoon.

---

## Kosten

| Onderdeel | Per maand |
|---|---|
| X-API (pay-per-use, alleen eigen posts en alleen nieuwe) | ~€30–50 |
| Claude (Haiku voor labelen, Sonnet voor de digest) | ~€10–20 |
| GitHub Actions, Supabase, GitHub Pages, Telegram, YouTube-RSS, Instagram | €0 |
| **Totaal** | **~€40–70** |

Lege controles kosten niets, want X rekent alleen voor posts die je echt ontvangt. Stel in de X-developer-console een **bestedingslimiet** in.

---

## Installatie (±45 minuten, kan op je telefoon)

### 1. GitHub
1. Maak een account op github.com en een **nieuwe repository** `ioi`. Privé mag; zie de opmerking over minuten onderaan.
2. Upload alle bestanden uit deze map (*Add file → Upload files*). Let op dat de map `.github/workflows` mee komt.

### 2. Database (Supabase, gratis)
1. supabase.com → *New project* → regio **Frankfurt** (EU).
2. *Project Settings → Database → Connection string → URI* (kies *Session pooler*) en kopieer deze. Vul je wachtwoord in.
3. De tabellen worden bij de eerste run automatisch aangemaakt.

### 3. API-sleutels
| Wat | Waar | Secret-naam |
|---|---|---|
| X Bearer Token | developer.x.com → app → *Keys and tokens* | `X_BEARER_TOKEN` |
| Claude API-key | console.anthropic.com | `ANTHROPIC_API_KEY` |
| YouTube API-key (eenmalig nodig) | console.cloud.google.com → *YouTube Data API v3* | `YOUTUBE_API_KEY` |
| Telegram-bot | @BotFather in Telegram → `/newbot` | `TELEGRAM_BOT_TOKEN` |
| Jouw chat-id | stuur je bot een bericht, open `https://api.telegram.org/bot<TOKEN>/getUpdates` | `TELEGRAM_STATUS_CHAT_ID` |
| Gratis kanaal | maak een kanaal, voeg de bot toe als beheerder | `TELEGRAM_PUBLIC_CHAT_ID` (bv. `@ioi_daily`) |
| Pro-kanaal (privé) | idem; id begint met `-100…` | `TELEGRAM_PRO_CHAT_ID` |
| Telegram lezen *(optioneel)* | my.telegram.org → *API development tools* | `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_SESSION` |
| Instagram *(optioneel)* | eigen zakelijk IG-account + Facebook-pagina, Graph API-token | `IG_USER_ID`, `IG_ACCESS_TOKEN` |

Zet ze in GitHub: *Settings → Secrets and variables → Actions → New repository secret*. Zet ook `DATABASE_URL` erbij.

Bij **Variables** (zelfde scherm, tab *Variables*): `SITE_URL` = `https://<jouw-github-naam>.github.io/ioi`.

> **Telegram-sessie zonder eigen pc:** open je repo → groene knop *Code → Codespaces → Create codespace*. Dat is een gratis computer in de browser. Typ daar `pip install -r requirements.txt && python -m ioi.cli telegram-login`, log in met je telefoonnummer en kopieer de getoonde tekst naar het secret `TELEGRAM_SESSION`.

### 4. Website aanzetten
*Settings → Pages → Source: GitHub Actions*.

### 5. Starten
1. *Actions → verify accounts → Run workflow*. Dit controleert alle 50 handles en haalt de volgers op. Handles die niet kloppen krijg je op Telegram.
2. *Actions → cycle → Run workflow* om de eerste posts op te halen. Daarna loopt dit automatisch elke 30 minuten.
3. Na een paar uur: *Actions → daily → Run workflow*. Daarna loopt dit elke ochtend om 06:41.
4. Na ongeveer een week: *Actions → weekly → Run workflow*. Daarna loopt dit elke maandag vanzelf.

---

## Nieuw in v0.2
- **Elke niche, één systeem.** Alle niche-specifieke instellingen staan in `config/niches/<naam>.json`: categorieën, onderwerpregels, schrijfstijl, regels zoals "geen medische claims" en de disclaimer. Crypto staat in `crypto.json`. Voor een nieuwe niche kopieer je `_template.json`, maak je een eigen accountlijst en zet je bij *Variables* `NICHE=<naam>`. Gebruik per niche een **eigen repository en database**, zodat de data gescheiden blijft.
- **Eigen dagbudget voor X** (`X_MAX_READS_PER_DAY`, standaard 1000). Dit komt bovenop de $60-limiet bij X. Is het budget bereikt, dan pauzeert het systeem X tot de volgende dag en krijg je een melding.
- **Automatisch opruimen.** Posts ouder dan 90 dagen worden verwijderd (`RETENTION_DAYS`). Zo blijft de database binnen de gratis Supabase-ruimte en bewaar je niets langer dan nodig.
- **Gezondheidscheck.** Het dagelijkse statusbericht waarschuwt bij 0 nieuwe posts, bij overgeslagen runs en toont het X-verbruik van vandaag.
- **Rijkere labels.** Elke post krijgt nu ook een *format* (nieuws, analyse, grafiek, thread…) en een *hook* (breaking, getal, vraag…). Daar draait het Weekly Playbook op.

## Accounts aanpassen
Bewerk `config/accounts.csv` direct op GitHub (potloodje). Kolommen: `key,name,category,platform,handle,active`.
- `platform`: `x`, `youtube` (handle met `@`), `telegram`, `instagram`, `bluesky`.
- `active` op `0` zet een account uit zonder de geschiedenis te wissen.
- Na het opslaan draait *verify* automatisch.

De startlijst is samengesteld uit openbare top-lijsten van 2026. *Verify* controleert of elke handle bestaat. Volg elk account op het platform waar het het goedkoopst te volgen is: YouTube, Telegram, Bluesky en Instagram zijn gratis, X is betaald.

## Open-source modellen gebruiken
Je kunt het labelen (veel kleine taken) door een goedkoop open-source model laten doen en het schrijven door Claude. Zet bij *Variables*:
- `LABEL_PROVIDER=openai`
- `OPENAI_BASE_URL` = de API van de dienst, bv. `https://openrouter.ai/api/v1` (Groq en Together werken ook)
- `LABEL_MODEL` = de modelnaam bij die dienst, bv. een Qwen- of Llama-model

Zet daarnaast het secret `OPENAI_API_KEY`. `WRITE_PROVIDER` blijft `anthropic`, zodat Claude de digest schrijft.

## Zo werkt de score (leaderboard)
Over de afgelopen 7 dagen telt het systeem per account:
- **3 punten** voor elk onderwerp dat het account als eerste noemde en dat anderen daarna oppakten;
- **1 punt** voor elk ander gevolgd account dat binnen 24 uur volgde;
- een kleine bonus voor engagement.

Volgers tellen niet mee. Precies daarom delen accounts hun ranking graag.

## Lokaal testen (optioneel)
```
pip install -r requirements.txt
python -m pytest -q tests        # draait zonder internet of API-sleutels
cp .env.example .env              # vul in en dan:
python -m ioi.cli verify
python -m ioi.cli cycle
python -m ioi.cli daily
```

## Goed om te weten
- **GitHub-minuten:** een privérepo heeft 2.000 gratis minuten per maand. Elke 30 minuten draaien past daarbinnen. Bij een publieke repo zijn de minuten onbeperkt (je sleutels blijven geheim), dan kan elke 10 minuten.
- GitHub zet geplande taken uit in een repo zonder activiteit gedurende 60 dagen. Elke wijziging, zoals in `accounts.csv`, zet ze weer aan.
- Geplande GitHub-taken kunnen een paar minuten later starten dan gepland. Wil je echt realtime alerts, gebruik dan later een VPS (~€5/mnd) met dezelfde commando's in `cron`.
- **Geen financieel advies:** elke digest eindigt met een disclaimer. De teksten beschrijven aandacht en sentiment, geen koop- of verkoopadvies.
- Alleen officiële API's, geen scraping. Posts worden samengevat in eigen woorden, met een link naar het origineel.
