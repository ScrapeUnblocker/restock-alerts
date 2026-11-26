# Restock alerts

For anyone hunting sold-out products: a Telegram, Discord, Slack or Pushover alert when the console, graphics card, LEGO set or trading-card box you watch comes back in stock at Target, Best Buy, Walmart, Smyths Toys or MediaMarkt / Saturn, at your price and sold by the shop itself, never by a reseller.

[![CI](https://github.com/ScrapeUnblocker/restock-alerts/actions/workflows/ci.yml/badge.svg)](https://github.com/ScrapeUnblocker/restock-alerts/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)
[![Powered by ScrapeUnblocker](https://img.shields.io/badge/powered%20by-ScrapeUnblocker-6c47ff.svg)](https://scrapeunblocker.com/?utm_source=github&utm_medium=integration&utm_campaign=example-repos)

Hot products sell out at list price, then come back in small restocks that can sell out again within minutes. In between, the same listing is often "in stock" at two or three times the price from a marketplace seller. This repo checks the products you list in `config.yml` on a GitHub Actions schedule in your own copy, so there is nothing to host. Every 15 minutes it reads each product page through the [ScrapeUnblocker](https://scrapeunblocker.com/?utm_source=github&utm_medium=integration&utm_campaign=example-repos) retailer APIs: price, seller, shipping, and pickup at your store. It decides whether the product is buyable the way you want it, and it sends one message when it comes back. It stays quiet until the product has sold out again and has another restock.

It only reads public product pages and tells you. It never logs in, adds to cart or buys anything.

## What actually arrives

These two messages came from a real run. The five default Pokémon boxes were all sold out, above their caps or only offered by resellers at that moment, so the smoke-test config watched a coffee maker at Target and a LEGO set at Smyths that were in stock. This is the plain text a webhook receives:

```text
🟢 In stock at Target: Hamilton Beach 12 Cup Programmable Coffee Maker Black 46290: Drip Coffee Machine, Nonstick Hot Plate, Brew Pause
$18.99 (was $27.99) · your cap $25
Ship: arrives Nov 30 to 55403
Pickup: order pickup ready Nov 27 at Mpls Nicollet Mall, 10 on hand, aisle L 53
Checked 26 Nov 01:56 UTC
https://www.target.com/p/hamilton-beach-12-cup-programmable-coffee-maker-black-46290/-/A-52580270
```

```text
🟢 In stock at Smyths Toys: LEGO Star Wars 75455 Boba Fett
£119.99 · your cap £130
Ship: home delivery (FREE Standard Delivery)
Pickup: click & collect (FREE collection within 2 hours)
Checked 26 Nov 01:56 UTC
https://www.smythstoys.com/uk/en-gb/toys/lego-and-bricks/lego-star-wars/lego-star-wars-75455-boba-fett-set-for-adults/p/259087
```

The first message for a product says **In stock** when it is already buyable at the first check. After that, a product that sold out and came back says **Back in stock**, with one more line: how long it was sold out, and how many restocks it had in the last 30 days and how long the last one lasted. The title links to the product page. Telegram and Slack get formatted text. Discord gets a green embed with the product photo. Pushover gets a push with an "Open at Target" button. A webhook gets the text plus `item` and `reading` as JSON (price, seller, each channel with its note).

The run log of that run shows why the other three were not sent:

```text
Checking 5 products (1 API call each):
  Target       Hamilton Beach 12 Cup Programmable Coffee M…  IN STOCK -> in-stock alert (first check)
  Best Buy     Pokemon TCG: Phantasmal Flames Elite Traine…  sold out
  Walmart      Pokemon TCG: Perfect Order Elite Trainer Box  sold by PrimeSellerHQ LLC, not Walmart
  Smyths Toys  LEGO Star Wars 75455 Boba Fett                IN STOCK -> in-stock alert (first check)
  MediaMarkt   Pokemon TCG: Mega-Entwicklung Top-Trainer-B…  sold out
API calls: 5

Delivered 2 of 2 messages to telegram, discord, slack, webhook.
Saved stock.json.
```

The next run sends nothing and leaves `state/stock.json` unchanged, so nothing is committed:

```text
Checking 5 products (1 API call each):
  Target       Hamilton Beach 12 Cup Programmable Coffee M…  in stock (shipping, pickup), already alerted
  Best Buy     Pokemon TCG: Phantasmal Flames Elite Traine…  sold out
  Walmart      Pokemon TCG: Perfect Order Elite Trainer Box  sold by PrimeSellerHQ LLC, not Walmart
  Smyths Toys  LEGO Star Wars 75455 Boba Fett                in stock (shipping, pickup), already alerted
  MediaMarkt   Pokemon TCG: Mega-Entwicklung Top-Trainer-B…  sold out
API calls: 5

Nothing new to deliver.
```

## Setup in three steps

1. **Use this template.** Click **Use this template > Create a new repository**. Public or private both work. In a private repo each run counts against your plan's included Actions minutes, and GitHub rounds every job up to a whole minute.
2. **Add secrets.** In your copy go to **Settings > Secrets and variables > Actions** and add:
   - `SCRAPEUNBLOCKER_KEY`: your API key ([get one here](https://scrapeunblocker.com/?utm_source=github&utm_medium=integration&utm_campaign=example-repos)).
   - At least one destination:
     - Telegram: `TELEGRAM_BOT_TOKEN` (from [@BotFather](https://t.me/BotFather)) and `TELEGRAM_CHAT_ID` (you, a group or a channel).
     - Discord: `DISCORD_WEBHOOK_URL` (Server Settings > Integrations > Webhooks).
     - Slack: `SLACK_WEBHOOK_URL` ([incoming webhook](https://api.slack.com/messaging/webhooks)).
     - Pushover: `PUSHOVER_USER_KEY` and `PUSHOVER_APP_TOKEN` (create an application on [pushover.net](https://pushover.net/)).
     - Any JSON endpoint (Zapier, Make, n8n, Home Assistant): `ALERT_WEBHOOK_URL`.
3. **Edit `config.yml`.** Paste the links of the products you want under `watch:` and set a `max_price` for each. If you watch Target, set your store and ZIP code under `target:`.

Then open **Actions > Restock check > Run workflow** once (tick *Dry run* to see what it would send without sending anything). From then on it runs every 15 minutes. Without `SCRAPEUNBLOCKER_KEY` the workflow prints a notice and exits green, and with no destination secret it checks nothing.

GitHub runs scheduled workflows on a best-effort basis: a run can start late, or be dropped when Actions is busy. In a public repo GitHub also pauses schedules after 60 days without any commit. The state file is only committed when stock changes, so a quiet watchlist can hit this; GitHub emails you, and one click re-enables it. For checks you can count on to the minute, run it on your own machine.

### Run it on your own machine instead

```bash
git clone https://github.com/<you>/restock-alerts && cd restock-alerts
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install .
cp .env.example .env                               # then fill in your key and a destination
restock-alerts test-destinations                   # a hello to each destination, no API calls
restock-alerts run --dry-run                       # check now, print, send nothing
restock-alerts watch --every 10                    # check every 10 minutes until Ctrl+C
```

Or one run per cron tick (`crontab -e`):

```cron
*/10 * * * * cd /path/to/restock-alerts && .venv/bin/restock-alerts run >> restock.log 2>&1
```

Or in Docker, with the repo folder mounted so `config.yml` and `state/` stay on your disk:

```bash
docker run -d --name restock-alerts --restart unless-stopped --env-file .env \
  -v "$PWD:/app" -w /app python:3.13-slim \
  sh -c "pip install -q . && restock-alerts watch --every 10"
```

To add a product, check it first. One API call, nothing saved:

```bash
restock-alerts check https://www.walmart.com/ip/Pokemon-TCG-Mega-Evolution-Perfect-Order-Elite-Trainer-Box/19402160990
```

It prints the price, each channel, the seller and the verdict, for example `Verdict: does not count: sold by PrimeSellerHQ LLC, not Walmart.` `restock-alerts status` shows each product's state and restock count without calling the API.

## How it works

1. **Collect.** One call per product, four at a time, through the official [Python SDK](https://pypi.org/project/scrapeunblocker/):
   | Retailer | SDK call (API endpoint) | What it reports |
   |---|---|---|
   | Target | `target_product(tcin, store_id=, zip=)` (`/marketplace/target-product`) | shipping to your ZIP; order pickup, shelf stock, quantity and aisle at your store |
   | Best Buy | `bestbuy_product(sku)` (`/marketplace/bestbuy-product`) | shipping, delivery and store pickup (nationwide), the seller |
   | Walmart | `walmart_product(item_id=)` (`/marketplace/walmart-product`) | shipping, delivery and pickup for the location Walmart assigns, the seller |
   | Smyths Toys (UK) | `smyths_inventory(code)` (`/marketplace/smyths-inventory`) | home delivery and click & collect |
   | MediaMarkt / Saturn | `mediamarkt_product(id, country=, banner=)` (`/marketplace/mediamarkt-product`) | delivery and store pickup, Marktplatz seller |

   A call that fails with a passing error is tried once more after 5 seconds. An answer without its stock fields (Target sometimes answers without `fulfillment`) changes nothing for that product.
2. **Process.** A product counts as in stock when all of these hold: a channel you watch (ship or pickup) is open; it is not a pre-order, if you turned pre-orders off; the retailer sells it itself, unless you allow marketplace sellers; and the price is at or below your `max_price`. Best Buy leaves its availability flags empty when a product cannot be bought, so empty counts as not in stock.
3. **State.** `state/stock.json` remembers, per product, whether a restock was already alerted, when the current sold-out spell began, and its last 20 restocks. Once a product has been alerted, it has to read "not in stock" on `rules.confirm_sold_out` runs in a row (default 2) before the next restock alerts. One odd answer in the middle of a restock does not cause a second message. The workflow commits the file back only when it changes.
4. **Deliver.** One message per event to every destination that is set up. If no destination accepts a message, its state is not saved, so the next run tries again.

## Config reference

`config.yml` is the only file you edit. Secrets live in repository secrets (or `.env`), never here.

| Setting | Default | What it does |
|---|---|---|
| `watch[].url` | | Product page link. The retailer and product number are read from it: `target.com/p/.../A-<tcin>`, `bestbuy.com/site/...<sku>.p` or `.../sku/<sku>`, `walmart.com/ip/.../<id>`, `smythstoys.com/.../p/<code>`, `mediamarkt.<de,at,ch,nl,be,es,pl,hu,com.tr>`, `mediaworld.it`, `saturn.de` `..._<slug>-<id>.html`. |
| `watch[].retailer` + `id` | | Instead of `url`: `target`, `bestbuy`, `walmart`, `smyths` or `mediamarkt` and the product number (MediaMarkt also takes `country`, and `banner: saturn` for Saturn). |
| `watch[].name` | product title | Your label in alerts. |
| `watch[].max_price` | none | Alert only at or below this price, in the shop's currency. |
| `watch[].channels` | `[ship, pickup]` | `[ship]` for shipping or delivery only, `[pickup]` for pickup or in-store stock only. |
| `watch[].sold_by_retailer_only` | `rules.` value | Per-product override. |
| `target.store_id` / `target.zip` | `1375` / `55403` | Your Target store (number from its link on target.com/store-locator) and ZIP code for shipping dates. |
| `mediamarkt.country` | `de` | Shop for MediaMarkt products given by id: `de`, `at`, `ch`, `nl`, `be`, `es`, `it` (MediaWorld), `pl`, `hu`, `tr`. |
| `rules.sold_by_retailer_only` | `true` | Skip Best Buy Marketplace, Walmart Marketplace and MediaMarkt Marktplatz offers. Target and Smyths do not name a third-party seller. |
| `rules.preorders` | `true` | An open pre-order counts as in stock. |
| `rules.confirm_sold_out` | `2` | Not-in-stock reads in a row before a restock ends and the next one can alert (1-20). |
| `rules.alert_sold_out` | `false` | Also post "Sold out again" with how long the restock lasted. |
| `delivery.destinations` | all five | Which of `telegram`, `discord`, `slack`, `pushover`, `webhook` may be used; each one needs its secrets too. |
| `delivery.pushover_priority` | `1` | `-1` quiet, `0` normal, `1` high (skips your Pushover quiet hours). |
| `state` | `state/stock.json` | Where the state is kept. |

Remove a product from `config.yml` and the next full run forgets its history. `restock-alerts run --item <name or id>` checks only some products.

## Cost

Every run costs **1 API call per product** in `config.yml`. A product whose call fails with a passing error is tried once more (2 calls).

The default config watches 5 products, and the workflow runs every 15 minutes (96 runs a day):

| | Calls |
|---|---|
| per run | 5 |
| per day | 5 × 96 = 480 |
| per 30 days | about 14,400 |

Halve it by running every 30 minutes (`cron: "4,34 * * * *"` in `.github/workflows/run.yml`), or watch only the shops you buy from. `test-destinations` and `status` make no API calls, and `check` makes one.

## Adding another destination

Destinations live in `src/restock_alerts/sinks.py` behind one small interface: a `name`, `send(message)`, `preview(message)` and `test()`. A Google Chat space webhook, for example:

```python
class GoogleChatSink(ChatSink):
    """Google Chat incoming webhook."""

    name = "google_chat"
    style = messages.PLAIN
    limit = 4000

    def wrap(self, text: str) -> dict[str, Any]:
        return {"text": text}
```

Register it in `REGISTRY`:

```python
"google_chat": SinkSpec(
    ("GOOGLE_CHAT_WEBHOOK_URL",),
    lambda env, http, _: GoogleChatSink(env["GOOGLE_CHAT_WEBHOOK_URL"].strip(), http),
),
```

Then add `"google_chat"` to `DESTINATIONS` in `config.py`, pass `GOOGLE_CHAT_WEBHOOK_URL` to the run step in `.github/workflows/run.yml`, and list it under `delivery.destinations`.

## Want the raw data instead?

[target-scraper](https://github.com/ScrapeUnblocker/target-scraper) and [walmart-scraper](https://github.com/ScrapeUnblocker/walmart-scraper) are the plain options: product search, product details and stock as JSON or CSV, from a CLI or a Python library.

## Development

```bash
make install   # pip install -e ".[dev]"
make lint      # ruff check + ruff format --check
make test      # pytest: offline, the API and every destination are mocked
```

The tests never call the ScrapeUnblocker API or a real destination. They use retailer answers shaped like the live ones (`tests/conftest.py`). They cover link parsing, each retailer's stock reading, the in-stock rules, the restock state machine (first check, blips, confirmed sell-outs), delivery once across runs, retries after a failed delivery, the message formats and the CLI.

## Links

- [ScrapeUnblocker](https://scrapeunblocker.com/?utm_source=github&utm_medium=integration&utm_campaign=example-repos): the web scraping API behind this repo
- [ScrapeUnblocker docs](https://docs.scrapeunblocker.com/?utm_source=github&utm_medium=integration&utm_campaign=example-repos): API reference, the retailer endpoints and the SDKs
- [Python SDK on PyPI](https://pypi.org/project/scrapeunblocker/)

## License

MIT, see [LICENSE](LICENSE).
