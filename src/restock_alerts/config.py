"""Load and check ``config.yml``, the one file a user edits.

Secrets never live here: the API key, bot tokens and webhook URLs come from environment
variables.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import yaml

# Retailer id -> the name people know it by.
RETAILERS = {
    "target": "Target",
    "bestbuy": "Best Buy",
    "walmart": "Walmart",
    "smyths": "Smyths Toys",
    "mediamarkt": "MediaMarkt",
}
CHANNELS = ("ship", "pickup")
DESTINATIONS = ("telegram", "discord", "slack", "pushover", "webhook")
MEDIAMARKT_COUNTRIES = ("de", "at", "ch", "nl", "be", "es", "it", "pl", "hu", "tr")
MEDIAMARKT_BANNERS = ("mediamarkt", "saturn")

# MediaMarkt shop domains -> (country, banner).
_MEDIAMARKT_HOSTS = {
    "mediamarkt.de": ("de", "mediamarkt"),
    "saturn.de": ("de", "saturn"),
    "mediamarkt.at": ("at", "mediamarkt"),
    "mediamarkt.ch": ("ch", "mediamarkt"),
    "mediamarkt.nl": ("nl", "mediamarkt"),
    "mediamarkt.be": ("be", "mediamarkt"),
    "mediamarkt.es": ("es", "mediamarkt"),
    "mediaworld.it": ("it", "mediamarkt"),
    "mediamarkt.pl": ("pl", "mediamarkt"),
    "mediamarkt.hu": ("hu", "mediamarkt"),
    "mediamarkt.com.tr": ("tr", "mediamarkt"),
}
_ID = re.compile(r"^\d{1,15}$")


class ConfigError(ValueError):
    """config.yml is missing, unreadable or has a value that cannot work."""


@dataclass(frozen=True)
class Item:
    """One product to watch at one retailer."""

    retailer: str  # a key of RETAILERS
    product_id: str  # Target TCIN, Best Buy SKU, Walmart item id, Smyths code, MediaMarkt id
    name: str = ""  # your label; empty = the retailer's product title
    url: str = ""  # the product link from config.yml, if one was given
    max_price: float | None = None  # alert only at or below this price
    channels: tuple[str, ...] = CHANNELS  # ship, pickup or both
    retailer_only: bool = True  # skip marketplace / third-party sellers
    country: str = ""  # MediaMarkt shop
    banner: str = ""  # MediaMarkt or Saturn

    @property
    def key(self) -> str:
        """Stable id in the state file, e.g. ``bestbuy:6645345`` or ``mediamarkt-de:3003326``."""
        if self.retailer == "mediamarkt":
            shop = self.country if self.banner != "saturn" else "saturn"
            return f"mediamarkt-{shop}:{self.product_id}"
        return f"{self.retailer}:{self.product_id}"

    @property
    def retailer_name(self) -> str:
        if self.retailer == "mediamarkt":
            return "Saturn" if self.banner == "saturn" else "MediaMarkt"
        return RETAILERS[self.retailer]

    def label(self, title: str = "") -> str:
        return self.name or title or f"{self.retailer_name} product {self.product_id}"


@dataclass(frozen=True)
class TargetSettings:
    """Target reports pickup and shelf stock for one store and shipping for one ZIP code."""

    store_id: str = "1375"
    zip: str = "55403"


@dataclass(frozen=True)
class Rules:
    sold_by_retailer_only: bool = True
    preorders: bool = True  # an open pre-order counts as in stock
    confirm_sold_out: int = 2  # sold-out reads in a row before the next restock can alert
    alert_sold_out: bool = False  # also post when an alerted product sells out again


@dataclass(frozen=True)
class Delivery:
    destinations: tuple[str, ...] = DESTINATIONS
    pushover_priority: int = 1  # -1 quiet, 0 normal, 1 high (skips your quiet hours)


@dataclass(frozen=True)
class Config:
    items: tuple[Item, ...]
    target: TargetSettings = field(default_factory=TargetSettings)
    mediamarkt_country: str = "de"
    rules: Rules = field(default_factory=Rules)
    delivery: Delivery = field(default_factory=Delivery)
    state: Path = Path("state/stock.json")

    def find(self, text: str) -> list[Item]:
        """Items whose name, key, product id or link matches ``text`` (case-insensitive)."""
        want = text.strip().casefold()
        return [
            item
            for item in self.items
            if want
            in (item.name.casefold(), item.key.casefold(), item.product_id, item.url.casefold())
        ]


# -- product links -------------------------------------------------------------------------


def _host(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host.removeprefix("www.")


def parse_link(url: str) -> dict[str, str]:
    """Retailer, product id (and MediaMarkt shop) from a product link.

    Raises :class:`ConfigError` when the link is not a product page of a supported
    retailer.
    """
    text = url.strip()
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ConfigError(f"{url!r} is not a product link (https://...)")
    host = _host(text)
    path = parts.path
    query = parse_qs(parts.query)

    def found(pattern: str, where: str = path) -> str:
        match = re.search(pattern, where)
        return match.group(1) if match else ""

    if host == "target.com":
        preselect = (query.get("preselect") or [""])[0]
        tcin = preselect if _ID.match(preselect) else found(r"/A-(\d+)")
        if tcin:
            return {"retailer": "target", "product_id": tcin}
    elif host == "bestbuy.com":
        sku = (query.get("skuId") or [""])[0]
        sku = sku if _ID.match(sku) else found(r"/sku/(\d+)") or found(r"/(\d+)\.p$")
        if sku:
            return {"retailer": "bestbuy", "product_id": sku}
    elif host == "walmart.com":
        item_id = found(r"/ip/(?:[^/]+/)?(\d+)/?$")
        if item_id:
            return {"retailer": "walmart", "product_id": item_id}
    elif host == "smythstoys.com":
        code = found(r"/p/(\d+)")
        if code:
            return {"retailer": "smyths", "product_id": code}
    elif host in _MEDIAMARKT_HOSTS:
        product_id = found(r"-(\d+)\.html$")
        if product_id:
            country, banner = _MEDIAMARKT_HOSTS[host]
            return {
                "retailer": "mediamarkt",
                "product_id": product_id,
                "country": country,
                "banner": banner,
            }
    else:
        known = "Target, Best Buy, Walmart, Smyths Toys (UK) and MediaMarkt / Saturn"
        raise ConfigError(f"{host}: not a supported retailer ({known})")
    raise ConfigError(f"{url!r}: no product id in this link (open the product page and copy it)")


def _slug(url: str) -> str:
    host = _host(url)
    path = urlsplit(url).path.strip("/").split("/")

    def after(marker: str) -> str:
        if marker in path and path.index(marker) + 1 < len(path):
            return path[path.index(marker) + 1]
        return ""

    if host == "smythstoys.com" and "p" in path and path.index("p") > 0:
        return path[path.index("p") - 1]  # .../<slug>/p/<code>
    if host == "target.com":
        return after("p")  # /p/<slug>/-/A-<tcin>
    if host == "bestbuy.com":
        return after("product") or after("site")  # /product/<slug>/... or /site/<slug>/<sku>.p
    if host == "walmart.com":
        slug = after("ip")  # /ip/<slug>/<id>
        return "" if slug.isdigit() else slug
    if host in _MEDIAMARKT_HOSTS:
        return re.sub(r"-\d+\.html$", "", path[-1]).lstrip("_")  # /de/product/_<slug>-<id>.html
    return ""


def name_from_link(url: str) -> str:
    """A readable name from a product link's slug, when the retailer's answer has none."""
    slug = _slug(url) if url else ""
    words = [w for w in re.split(r"[-_]+", slug) if w and w != "-"]
    small = {"a", "an", "and", "for", "of", "the", "to", "with", "in", "on", "or"}
    return " ".join(
        w if w.isupper() or w.isdigit() else w if i and w in small else w.capitalize()
        for i, w in enumerate(words)
    )


# -- helpers ------------------------------------------------------------------------------


def _section(raw: dict[str, Any], name: str) -> dict[str, Any]:
    value = raw.get(name)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"{name}: expected a mapping of settings")
    return value


def _unknown(section: dict[str, Any], allowed: set[str], where: str) -> None:
    extra = sorted(set(section) - allowed)
    if extra:
        raise ConfigError(f"{where}: unknown setting(s) {', '.join(map(str, extra))}")


def _number(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConfigError(f"{where}: expected a number, got {value!r}")
    if value <= 0:
        raise ConfigError(f"{where}: must be more than 0")
    return float(value)


def _int(value: Any, where: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{where}: expected a whole number, got {value!r}")
    if not minimum <= value <= maximum:
        raise ConfigError(f"{where}: must be {minimum} to {maximum}")
    return value


def _bool(value: Any, where: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{where}: expected true or false, got {value!r}")
    return value


def _choice(value: Any, choices: tuple[str, ...], where: str) -> str:
    text = str(value).strip().lower()
    if text not in choices:
        raise ConfigError(f"{where}: expected one of {', '.join(choices)}, got {value!r}")
    return text


def _id(value: Any, where: str) -> str:
    text = str(value).strip() if value is not None and not isinstance(value, bool) else ""
    if not _ID.match(text):
        raise ConfigError(f"{where}: expected the product number, got {value!r}")
    return text


# -- sections -----------------------------------------------------------------------------


def _channels(value: Any, where: str) -> tuple[str, ...]:
    if value is None or value == "any":
        return CHANNELS
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not value:
        raise ConfigError(f"{where}: expected ship, pickup or both, e.g. [ship, pickup]")
    picked = {_choice(v, CHANNELS, where) for v in value}
    return tuple(c for c in CHANNELS if c in picked)


def _item(raw: Any, index: int, rules: Rules, mediamarkt_country: str) -> Item:
    where = f"watch[{index}]"
    if isinstance(raw, str):
        raw = {"url": raw}
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: expected a product link or a mapping with url")
    _unknown(
        raw,
        {
            "url",
            "retailer",
            "id",
            "name",
            "max_price",
            "channels",
            "sold_by_retailer_only",
            "country",
            "banner",
        },
        where,
    )
    url = str(raw.get("url") or "").strip()
    if url:
        found = parse_link(url)
        if (
            raw.get("retailer")
            and _choice(raw["retailer"], tuple(RETAILERS), where) != found["retailer"]
        ):
            raise ConfigError(f"{where}: retailer does not match the link")
    elif raw.get("retailer"):
        found = {
            "retailer": _choice(raw["retailer"], tuple(RETAILERS), f"{where}.retailer"),
            "product_id": _id(raw.get("id"), f"{where}.id"),
        }
    else:
        raise ConfigError(f"{where}: give the product's url (or retailer and id)")
    if raw.get("id") is not None and url and _id(raw["id"], f"{where}.id") != found["product_id"]:
        raise ConfigError(f"{where}: id does not match the link")

    retailer = found["retailer"]
    country = banner = ""
    if retailer == "mediamarkt":
        country = found.get("country") or _choice(
            raw.get("country") or mediamarkt_country, MEDIAMARKT_COUNTRIES, f"{where}.country"
        )
        banner = found.get("banner") or _choice(
            raw.get("banner") or "mediamarkt", MEDIAMARKT_BANNERS, f"{where}.banner"
        )
        if banner == "saturn" and country != "de":
            raise ConfigError(f"{where}: Saturn is only supported for Germany (country de)")
    elif raw.get("country") or raw.get("banner"):
        raise ConfigError(f"{where}: country and banner are MediaMarkt settings")

    max_price = raw.get("max_price")
    retailer_only = raw.get("sold_by_retailer_only", rules.sold_by_retailer_only)
    return Item(
        retailer=retailer,
        product_id=found["product_id"],
        name=" ".join(str(raw.get("name") or "").split()),
        url=url,
        max_price=None if max_price is None else _number(max_price, f"{where}.max_price"),
        channels=_channels(raw.get("channels"), f"{where}.channels"),
        retailer_only=_bool(retailer_only, f"{where}.sold_by_retailer_only"),
        country=country,
        banner=banner,
    )


def _target(raw: dict[str, Any]) -> TargetSettings:
    _unknown(raw, {"store_id", "zip"}, "target")
    d = TargetSettings()
    store = str(raw.get("store_id") or d.store_id).strip()
    zip_code = str(raw.get("zip") or d.zip).strip()
    if not store.isdigit():
        raise ConfigError("target.store_id: expected the store number, e.g. '1375'")
    if not re.fullmatch(r"\d{5}", zip_code):
        raise ConfigError("target.zip: expected a 5-digit US ZIP code")
    return TargetSettings(store_id=store, zip=zip_code)


def _rules(raw: dict[str, Any]) -> Rules:
    where = "rules"
    _unknown(
        raw, {"sold_by_retailer_only", "preorders", "confirm_sold_out", "alert_sold_out"}, where
    )
    d = Rules()
    return Rules(
        sold_by_retailer_only=_bool(
            raw.get("sold_by_retailer_only", d.sold_by_retailer_only),
            f"{where}.sold_by_retailer_only",
        ),
        preorders=_bool(raw.get("preorders", d.preorders), f"{where}.preorders"),
        confirm_sold_out=_int(
            raw.get("confirm_sold_out", d.confirm_sold_out),
            f"{where}.confirm_sold_out",
            minimum=1,
            maximum=20,
        ),
        alert_sold_out=_bool(
            raw.get("alert_sold_out", d.alert_sold_out), f"{where}.alert_sold_out"
        ),
    )


def _delivery(raw: dict[str, Any]) -> Delivery:
    _unknown(raw, {"destinations", "pushover_priority"}, "delivery")
    value = raw.get("destinations", list(DESTINATIONS))
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not value:
        raise ConfigError("delivery.destinations: list at least one of " + ", ".join(DESTINATIONS))
    names: list[str] = []
    for item in value:
        name = _choice(item, DESTINATIONS, "delivery.destinations")
        if name not in names:
            names.append(name)
    priority = _int(
        raw.get("pushover_priority", Delivery.pushover_priority),
        "delivery.pushover_priority",
        minimum=-1,
        maximum=1,
    )
    return Delivery(destinations=tuple(names), pushover_priority=priority)


def parse_config(raw: Any, base: Path = Path(".")) -> Config:
    if not isinstance(raw, dict):
        raise ConfigError("config.yml: expected a mapping at the top level")
    _unknown(raw, {"watch", "target", "mediamarkt", "rules", "delivery", "state"}, "config.yml")
    rules = _rules(_section(raw, "rules"))
    mediamarkt = _section(raw, "mediamarkt")
    _unknown(mediamarkt, {"country"}, "mediamarkt")
    mm_country = _choice(
        mediamarkt.get("country") or "de", MEDIAMARKT_COUNTRIES, "mediamarkt.country"
    )
    watch = raw.get("watch")
    if not isinstance(watch, list) or not watch:
        raise ConfigError("watch: list at least one product link")
    items = tuple(_item(entry, i, rules, mm_country) for i, entry in enumerate(watch))
    seen: set[str] = set()
    for item in items:
        if item.key in seen:
            raise ConfigError(f"watch: {item.key} is listed twice")
        seen.add(item.key)
    state = Path(str(raw.get("state") or "state/stock.json"))
    if not state.is_absolute():
        state = base / state
    return Config(
        items=items,
        target=_target(_section(raw, "target")),
        mediamarkt_country=mm_country,
        rules=rules,
        delivery=_delivery(_section(raw, "delivery")),
        state=state,
    )


def load_config(path: str | Path) -> Config:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(f"{path} not found (copy config.yml from the repo)") from None
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from None
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from None
    return parse_config(raw, path.parent)
