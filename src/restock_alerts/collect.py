"""Read stock and price through the ScrapeUnblocker retailer APIs.

One SDK call per watched product and run, one API unit each:

- Target: ``client.target_product(tcin, store_id=, zip=)`` (``POST /marketplace/target-product``)
  shipping to your ZIP code, order pickup and shelf stock at your store.
- Best Buy: ``client.bestbuy_product(sku)`` (``POST /marketplace/bestbuy-product``)
  shipping, delivery and pickup, nationwide, plus the seller.
- Walmart: ``client.walmart_product(item_id=)`` (``POST /marketplace/walmart-product``)
  shipping, delivery and pickup for the location Walmart assigns, plus the seller.
- Smyths Toys (UK): ``client.smyths_inventory(code)`` (``POST /marketplace/smyths-inventory``)
  home delivery and click & collect.
- MediaMarkt / Saturn: ``client.mediamarkt_product(id, country=, banner=)``
  (``POST /marketplace/mediamarkt-product``) delivery and store pickup, plus the seller.

Every answer becomes one :class:`Reading`. When a retailer answers without its stock
fields, the reading is ``known=False`` and the product's state is left as it was.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from typing import Any, Protocol

from scrapeunblocker import (
    AuthenticationError,
    BlockedError,
    BrowserTimeoutError,
    InvalidRequestError,
    NotFoundError,
    PaymentRequiredError,
    RateLimitError,
    ScrapeTimeoutError,
    ServerError,
    UpstreamOutageError,
    ValidationError,
)
from scrapeunblocker import ConnectionError as SUConnectionError

from .config import Item, TargetSettings, name_from_link

log = logging.getLogger(__name__)

# Errors that end the whole run: moving on to the next product cannot help.
FATAL = (AuthenticationError, PaymentRequiredError)
# Errors worth one more try after a pause.
RETRYABLE = (
    BlockedError,
    ServerError,
    UpstreamOutageError,
    RateLimitError,
    BrowserTimeoutError,
    ScrapeTimeoutError,
    SUConnectionError,
)
RETRY_DELAY = 5.0

_IN = ("IN_STOCK", "LIMITED_STOCK")  # Target availability codes that can be bought
_SMYTHS_IN = ("INSTOCK", "LOWSTOCK", "LIMITEDSTOCK")
_SMYTHS_OUT = ("OUTOFSTOCK", "NOSTOCK")


class ShopClient(Protocol):
    """The part of ``scrapeunblocker.Client`` this package uses."""

    def target_product(self, tcin: str | None = None, **kwargs: Any) -> Any: ...

    def bestbuy_product(self, sku: str | None = None, **kwargs: Any) -> Any: ...

    def walmart_product(self, **kwargs: Any) -> Any: ...

    def smyths_inventory(self, code: str, **kwargs: Any) -> Any: ...

    def mediamarkt_product(self, product_id: str | None = None, **kwargs: Any) -> Any: ...


class FetchError(RuntimeError):
    """A product could not be read this run. The run goes on with the others.

    ``permanent`` means retrying will not help (the product id does not exist): fix
    config.yml.
    """

    def __init__(self, message: str, *, permanent: bool = False, calls: int = 1) -> None:
        super().__init__(message)
        self.permanent = permanent
        self.calls = calls  # API calls made for it, retries included


@dataclass(frozen=True)
class Channel:
    """One way to get the product: shipping/delivery, or pickup/in store."""

    available: bool
    note: str = ""  # what the retailer says, e.g. "arrives Nov 30 to 55403"

    def to_dict(self) -> dict[str, Any]:
        return {"available": self.available, "note": self.note}


@dataclass(frozen=True)
class Reading:
    """What one call said about one product."""

    key: str
    retailer: str  # retailer display name
    title: str
    url: str
    known: bool  # False: the answer had no stock fields
    price: float | None = None
    currency: str = ""
    was_price: float | None = None
    ship: Channel | None = None  # None: this retailer does not report it
    pickup: Channel | None = None
    preorder: bool = False
    release: str = ""  # release date of a pre-order, YYYY-MM-DD
    seller: str = ""  # who sells it, when the retailer says
    by_retailer: bool | None = None  # True: sold by the retailer itself
    image: str = ""
    status: str = ""  # the retailer's own stock codes, for `check`

    def channel(self, name: str) -> Channel | None:
        return self.ship if name == "ship" else self.pickup

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["ship"] = self.ship.to_dict() if self.ship else None
        data["pickup"] = self.pickup.to_dict() if self.pickup else None
        return data


# -- small helpers --------------------------------------------------------------------------


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        match = re.search(r"\d[\d,]*(?:\.\d+)?", value)
        if match:
            try:
                return float(match.group(0).replace(",", ""))
            except ValueError:
                return None
    return None


def _text(value: Any) -> str:
    return " ".join(str(value).split()) if value is not None else ""


def _code(value: Any) -> str:
    return _text(value).upper()


def _day(value: Any) -> date | None:
    text = _text(value)[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def short_date(value: Any) -> str:
    """``2026-11-30`` or ``2026-11-30T22:59:00Z`` -> ``Nov 30``."""
    day = _day(value)
    return f"{day:%b} {day.day}" if day else ""


def _span(first: Any, last: Any) -> str:
    a, b = short_date(first), short_date(last)
    return a if not b or a == b else f"{a}-{b}"


def _future(value: Any, today: date) -> bool:
    day = _day(value)
    return day is not None and day > today


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


# -- per retailer ---------------------------------------------------------------------------


def parse_target(raw: Any, item: Item, target: TargetSettings, today: date) -> Reading:
    raw = _dict(raw)
    title = _text(raw.get("title"))
    base = {
        "key": item.key,
        "retailer": "Target",
        "title": title,
        "url": _text(raw.get("url"))
        or item.url
        or f"https://www.target.com/p/-/A-{item.product_id}",
        "price": _num(raw.get("price")),
        "currency": _text(raw.get("currency")) or "USD",
        "was_price": _num(raw.get("originalPrice")),
        "image": _text(raw.get("image")),
    }
    f = raw.get("fulfillment")
    if not isinstance(f, dict):
        return Reading(known=False, status="no fulfillment in the answer", **base)
    sold_out = f.get("soldOut") is True
    ship_code = _code(f.get("shippingStatus"))
    preorder = ship_code.startswith("PRE_ORDER")
    ship_ok = not sold_out and (ship_code in _IN or ship_code == "PRE_ORDER_SELLABLE")
    when = _span(f.get("shippingMinDate"), f.get("shippingMaxDate"))
    ship_note = f"arrives {when} to {target.zip}" if ship_ok and when else ""
    if ship_ok and f.get("freeShipping") is True:
        ship_note = f"{ship_note}, free shipping" if ship_note else "free shipping"

    store = _dict(f.get("store"))
    store_name = _text(store.get("storeName")) or f"store {target.store_id}"
    pickup_code = _code(store.get("pickupStatus"))
    shelf_code = _code(store.get("inStoreStatus"))
    pickup_ok = not sold_out and pickup_code in _IN
    shelf_ok = not sold_out and shelf_code in _IN
    parts = []
    if pickup_ok:
        ready = short_date(store.get("pickupDate"))
        parts.append(f"order pickup {'ready ' + ready + ' ' if ready else ''}at {store_name}")
    elif shelf_ok:
        parts.append(f"on the shelf at {store_name} (no order pickup)")
    quantity = store.get("quantity")
    if (pickup_ok or shelf_ok) and isinstance(quantity, int) and quantity > 0:
        parts.append(f"{quantity} on hand")
    if (pickup_ok or shelf_ok) and _text(store.get("aisle")):
        parts.append(f"aisle {_text(store.get('aisle'))}")
    return Reading(
        known=True,
        ship=Channel(ship_ok, ship_note),
        pickup=Channel(pickup_ok or shelf_ok, ", ".join(parts)),
        preorder=preorder,
        status=(
            f"soldOut={str(sold_out).lower()} shipping={ship_code or '-'} "
            f"pickup={pickup_code or '-'} inStore={shelf_code or '-'}"
        ),
        **base,
    )


def parse_bestbuy(raw: Any, item: Item, today: date) -> Reading:
    raw = _dict(raw)
    seller = _dict(raw.get("seller"))
    seller_type = _text(seller.get("type"))
    by_retailer = None
    if seller:
        by_retailer = seller.get("id") == "bby" or seller_type.casefold() == "best buy"
    base = {
        "key": item.key,
        "retailer": "Best Buy",
        "title": _text(raw.get("title")),
        "url": _text(raw.get("url"))
        or item.url
        or f"https://www.bestbuy.com/site/-/{item.product_id}.p",
        "price": _num(raw.get("price")),
        "currency": _text(raw.get("currency")) or "USD",
        "was_price": _num(raw.get("originalPrice")),
        "seller": "Best Buy"
        if by_retailer
        else (f"Marketplace seller {seller.get('id')}" if seller else ""),
        "by_retailer": by_retailer,
        "image": _text(raw.get("image")),
    }
    if not raw.get("sku") and not base["title"]:
        return Reading(known=False, status="empty answer", **base)
    # Best Buy leaves the availability flags empty while a product is sold out.
    shipping = raw.get("availableForShipping") is True
    delivery = raw.get("availableForDelivery") is True
    pickup = raw.get("availableForPickup") is True
    ship_parts = [p for p, on in (("shipping", shipping), ("delivery", delivery)) if on]
    release = _text(raw.get("releaseDate"))[:10]
    preorder = (shipping or delivery or pickup) and _future(release, today)
    return Reading(
        known=True,
        ship=Channel(shipping or delivery, " and ".join(ship_parts)),
        pickup=Channel(
            pickup, "store pickup (at some stores; Best Buy does not say which)" if pickup else ""
        ),
        preorder=preorder,
        release=release if preorder else "",
        status=(
            f"available={raw.get('available')} shipping={raw.get('availableForShipping')} "
            f"delivery={raw.get('availableForDelivery')} pickup={raw.get('availableForPickup')}"
        ),
        **base,
    )


def parse_walmart(raw: Any, item: Item, today: date) -> Reading:
    raw = _dict(raw)
    product = _dict(raw.get("product")) if "product" in raw else raw
    seller = _dict(product.get("seller"))
    by_retailer = None
    if seller:
        by_retailer = _text(seller.get("type")).casefold() == "walmart"
    base = {
        "key": item.key,
        "retailer": "Walmart",
        "title": _text(product.get("title")),
        "url": _text(product.get("url"))
        or item.url
        or f"https://www.walmart.com/ip/{item.product_id}",
        "price": _num(product.get("price")),
        "currency": _text(product.get("currency")) or "USD",
        "was_price": _num(product.get("wasPrice")),
        "seller": _text(seller.get("name")),
        "by_retailer": by_retailer,
        "image": _first_image(product.get("images")),
    }
    in_stock = product.get("inStock")
    availability = _code(product.get("availability"))
    if in_stock is None and not availability:
        return Reading(known=False, status="no availability in the answer", **base)
    ship = pickup = None
    for option in product.get("fulfillment") or []:
        option = _dict(option)
        kind = _text(option.get("type")).lower()
        ok = option.get("available") is True and in_stock is not False
        note = _text(option.get("message")) if ok else ""
        if kind in ("shipping", "delivery"):
            if ship is None or (ok and not ship.available):
                ship = Channel(ok, note)
        elif kind == "pickup":
            pickup = Channel(ok, note)
    if ship is None:
        ship = Channel(in_stock is True)
    preorder = availability.replace("_", "").startswith("PREORDER")
    return Reading(
        known=True,
        ship=ship,
        pickup=pickup,
        preorder=preorder,
        status=f"inStock={in_stock} availability={availability or '-'}",
        **base,
    )


def _first_image(value: Any) -> str:
    if isinstance(value, list) and value:
        first = value[0]
        if isinstance(first, dict):
            return _text(first.get("url") or first.get("src"))
        return _text(first)
    return ""


def parse_smyths(raw: Any, item: Item, today: date) -> Reading:
    raw = _dict(raw)
    prices = _dict(raw.get("prices"))
    payment = _dict(raw.get("paymentMessage"))
    base = {
        "key": item.key,
        "retailer": "Smyths Toys",
        # The stock answer has no product name; the link's slug is the next best thing.
        "title": name_from_link(item.url) if item.url else "",
        "url": item.url or f"https://www.smythstoys.com/uk/en-gb/search/?text={item.product_id}",
        "price": _num(prices.get("value")) or _num(prices.get("price")),
        "currency": _text(payment.get("currency")) or "GBP",
        "was_price": _num(prices.get("wasPrice")),
        "by_retailer": True,  # Smyths sells its own stock only
    }
    home, collect = _dict(raw.get("hdSection")), _dict(raw.get("ccSection"))
    if not home and not collect:
        return Reading(known=False, status="no stock sections in the answer", **base)

    def section(sec: dict[str, Any]) -> tuple[bool, bool, str]:
        code = _code(sec.get("stockStatus")).replace("_", "")
        pre = sec.get("isPreOrder") is True or sec.get("preOrder") is True
        ok = code in _SMYTHS_IN or (pre and bool(code) and code not in _SMYTHS_OUT)
        return ok, pre, code

    home_ok, home_pre, home_code = section(home)
    collect_ok, collect_pre, collect_code = section(collect)
    store = _text(collect.get("displayStoreName") or collect.get("storeName"))
    collect_note = ""
    if collect_ok:
        collect_note = "click & collect" + (f" at {store}" if store else "")
        when = _text(collect.get("deliveryMessage"))
        collect_note += f" ({when})" if when else ""
    home_note = ""
    if home_ok:
        terms = _text(home.get("guestDeliveryMessage"))
        home_note = "home delivery" + (f" ({terms})" if terms else "")
    release = ""
    if home_pre or collect_pre:
        release = _text(home.get("expectedStockDate") or collect.get("expectedStockDate"))
    return Reading(
        known=True,
        ship=Channel(home_ok, home_note),
        pickup=Channel(collect_ok and not collect.get("storeClosed"), collect_note),
        preorder=(home_ok and home_pre) or (collect_ok and collect_pre),
        release=release,
        status=f"delivery={home_code or '-'} collect={collect_code or '-'}",
        **base,
    )


def parse_mediamarkt(raw: Any, item: Item, today: date) -> Reading:
    raw = _dict(raw)
    seller = raw.get("seller")
    marketplace = raw.get("marketplace")
    seller_name = _text(seller.get("name")) if isinstance(seller, dict) else _text(seller)
    if marketplace is False:
        seller_name = item.retailer_name
    shop = item.retailer_name
    base = {
        "key": item.key,
        "retailer": shop,
        "title": _text(raw.get("title")),
        "url": _text(raw.get("url")) or item.url,
        "price": _num(raw.get("price")),
        "currency": _text(raw.get("currency")) or "EUR",
        "was_price": _num(raw.get("strikePrice")),
        "seller": seller_name,
        "by_retailer": None if marketplace is None else marketplace is False,
        "image": _text(raw.get("image")),
    }
    availability = raw.get("availability")
    if not isinstance(availability, dict):
        return Reading(known=False, status="no availability in the answer", **base)
    online = _code(availability.get("online"))
    buyable = availability.get("buyable") is True
    ship_ok = buyable and availability.get("deliverable") is True
    delivery = _dict(availability.get("delivery"))
    ship_note = ""
    if ship_ok:
        when = _span(delivery.get("earliest"), delivery.get("latest"))
        ship_note = f"delivery {when}" if when else "delivery"
        cost = _num(delivery.get("shippingCost"))
        if cost == 0:
            ship_note += ", free shipping"
    pickup_code = _code(availability.get("pickupStatus"))
    pickup_ok = (
        buyable
        and availability.get("storePickup") is True
        and pickup_code in ("AVAILABLE", "PARTIALLY_AVAILABLE")
    )
    pickup_note = ""
    if pickup_ok:
        pickup_note = "store pickup" + (
            " (some stores)" if pickup_code.startswith("PARTIAL") else ""
        )
    release = _text(availability.get("releaseDate"))[:10]
    preorder = buyable and ("PRE" in online or _future(release, today))
    return Reading(
        known=True,
        ship=Channel(ship_ok, ship_note),
        pickup=Channel(pickup_ok, pickup_note),
        preorder=preorder,
        release=release if preorder else "",
        status=f"online={online or '-'} buyable={buyable} pickup={pickup_code or '-'}",
        **base,
    )


# -- the call -------------------------------------------------------------------------------


def _call(client: ShopClient, item: Item, target: TargetSettings) -> Any:
    if item.retailer == "target":
        return client.target_product(item.product_id, store_id=target.store_id, zip=target.zip)
    if item.retailer == "bestbuy":
        return client.bestbuy_product(item.product_id)
    if item.retailer == "walmart":
        return client.walmart_product(item_id=item.product_id)
    if item.retailer == "smyths":
        return client.smyths_inventory(item.product_id)
    if item.retailer == "mediamarkt":
        return client.mediamarkt_product(item.product_id, country=item.country, banner=item.banner)
    raise ValueError(f"unknown retailer {item.retailer!r}")


def parse(raw: Any, item: Item, target: TargetSettings, today: date) -> Reading:
    if item.retailer == "target":
        return parse_target(raw, item, target, today)
    if item.retailer == "bestbuy":
        return parse_bestbuy(raw, item, today)
    if item.retailer == "walmart":
        return parse_walmart(raw, item, today)
    if item.retailer == "smyths":
        return parse_smyths(raw, item, today)
    return parse_mediamarkt(raw, item, today)


def read(
    client: ShopClient,
    item: Item,
    target: TargetSettings,
    *,
    today: date | None = None,
    sleep: Callable[[float], None] | None = None,
) -> tuple[Reading, Any, int]:
    """Read one product: ``(reading, raw answer, API calls made)``. One retry on a passing
    error.

    Raises :class:`FetchError` when it cannot be read; ``FATAL`` errors pass through.
    """
    today = today or datetime.now(UTC).date()
    for attempt in (1, 2):
        try:
            log.info("reading %s", item.key)
            raw = _call(client, item, target)
            return parse(raw, item, target, today), raw, attempt
        except FATAL:
            raise
        except NotFoundError as exc:
            raise FetchError(
                f"{item.retailer_name} has no product {item.product_id} ({exc})",
                permanent=True,
                calls=attempt,
            ) from None
        except (InvalidRequestError, ValidationError) as exc:
            raise FetchError(f"request refused: {exc}", permanent=True, calls=attempt) from None
        except RETRYABLE as exc:
            if attempt == 2:
                raise FetchError(f"{type(exc).__name__}: {exc}", calls=attempt) from None
            log.info("%s: %s, trying once more", item.key, type(exc).__name__)
            (sleep or time.sleep)(RETRY_DELAY)
    raise AssertionError("unreachable")
