"""restock-alerts: restock alerts for sold-out products, delivered where you look.

Reads stock and price of the products in ``config.yml`` at Target, Best Buy, Walmart,
Smyths Toys and MediaMarkt / Saturn through the ScrapeUnblocker retailer APIs, decides
whether each one is in stock the way you want it (channel, seller, price cap), and sends
each restock once to Telegram, Discord, Slack, Pushover or a webhook.

Library use::

    from scrapeunblocker import Client
    from restock_alerts import load_config, read, judge

    config = load_config("config.yml")
    client = Client()  # reads SCRAPEUNBLOCKER_KEY
    for item in config.items:
        reading, _, _ = read(client, item, config.target)
        print(item.label(reading.title), judge(reading, item, config.rules))
"""

from .collect import Channel, FetchError, Reading, read
from .config import Config, ConfigError, Item, load_config, parse_link
from .process import Verdict, judge, step
from .state import State, Watch

__version__ = "0.1.0"

__all__ = [
    "Channel",
    "Config",
    "ConfigError",
    "FetchError",
    "Item",
    "Reading",
    "State",
    "Verdict",
    "Watch",
    "__version__",
    "judge",
    "load_config",
    "parse_link",
    "read",
    "step",
]
