"""Decide what a reading means: in stock the way you want it, and is that news?

``judge`` checks one reading against the product's rules: a channel you asked for (ship,
pickup) is open, it is not a pre-order you did not want, the retailer sells it itself
(not a marketplace reseller), and the price is at or below your cap.

``step`` moves the product's saved state on and says whether to send something:

- in stock (as judged) and no restock open -> ``restock`` (or ``in_stock`` on the very
  first check), and the restock is now open;
- in stock and a restock is open -> nothing (a sold-out read in between was a blip);
- not in stock while a restock is open -> counted; after ``rules.confirm_sold_out``
  reads in a row the restock is closed (``sold_out`` when ``rules.alert_sold_out``);
- no answer about stock -> nothing changes.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from .collect import Reading
from .config import CHANNELS, Item, Rules
from .state import Restock, Watch, parse_stamp, stamp

CHANNEL_LABEL = {"ship": "shipping", "pickup": "pickup"}
SYMBOLS = {"USD": "$", "GBP": "£", "EUR": "€"}


def money(value: float, currency: str = "USD") -> str:
    text = f"{value:,.2f}".removesuffix(".00")
    symbol = SYMBOLS.get(currency)
    if symbol:
        return f"{symbol}{text}"
    return f"{text} {currency}".strip()


@dataclass(frozen=True)
class Verdict:
    match: bool
    reason: str = ""  # why it does not count, for `check` and the run log
    channels: tuple[str, ...] = ()  # the channels you asked for that are open


@dataclass(frozen=True)
class Event:
    kind: str  # in_stock, restock or sold_out
    item: Item
    reading: Reading
    verdict: Verdict
    before: Watch | None  # the saved state before this run
    after: Watch  # the state once this event is delivered


def judge(reading: Reading, item: Item, rules: Rules) -> Verdict | None:
    """Does this reading count as in stock for you? ``None`` when stock is unknown."""
    if not reading.known:
        return None
    wanted = item.channels
    open_ = tuple(c for c in wanted if (ch := reading.channel(c)) is not None and ch.available)
    if not open_:
        others = [
            c
            for c in CHANNELS
            if c not in wanted and (ch := reading.channel(c)) is not None and ch.available
        ]
        if others:
            asked = " or ".join(CHANNEL_LABEL[c] for c in wanted)
            return Verdict(False, f"only {CHANNEL_LABEL[others[0]]} is open; you asked for {asked}")
        return Verdict(False, "sold out")
    if reading.preorder and not rules.preorders:
        return Verdict(False, "pre-order only (rules.preorders is off)")
    if item.retailer_only and reading.by_retailer is False:
        who = reading.seller or "a marketplace seller"
        return Verdict(False, f"sold by {who}, not {reading.retailer}")
    if item.max_price is not None:
        if reading.price is None:
            return Verdict(False, "no price in the answer, so max_price cannot be checked")
        if reading.price > item.max_price + 1e-9:
            price = money(reading.price, reading.currency)
            cap = money(item.max_price, reading.currency)
            return Verdict(False, f"{price} is above your {cap} cap")
    return Verdict(True, channels=open_)


def step(
    before: Watch | None, verdict: Verdict | None, reading: Reading, now: datetime, rules: Rules
) -> tuple[Watch | None, str]:
    """The product's next state and the event to send (``""`` for none)."""
    if verdict is None:
        return before, ""
    now_s = stamp(now)
    watch = before or Watch(since=now_s)
    if verdict.match:
        if watch.in_stock:
            return replace(watch, out_reads=0, pending_out=""), ""
        first = before is None
        watch = replace(watch, in_stock=True, out_reads=0, pending_out="", out_since="")
        watch = watch.with_restock(Restock(at=now_s, price=reading.price, first=first))
        return watch, "in_stock" if first else "restock"
    if watch.in_stock:
        reads = watch.out_reads + 1
        pending = watch.pending_out or now_s
        if reads < rules.confirm_sold_out:
            return replace(watch, out_reads=reads, pending_out=pending), ""
        watch = replace(
            watch.close_last(pending),
            in_stock=False,
            out_reads=0,
            pending_out="",
            out_since=pending,
        )
        return watch, "sold_out" if rules.alert_sold_out else ""
    if not watch.out_since:
        watch = replace(watch, out_since=now_s)
    return watch, ""


# -- history, for the message ---------------------------------------------------------------


def duration(delta: timedelta) -> str:
    """``about 25 min``, ``3 h``, ``12 days``."""
    minutes = max(int(delta.total_seconds() // 60), 0)
    if minutes < 60:
        return f"{max(minutes, 1)} min"
    hours = minutes / 60
    if hours < 48:
        return f"{hours:.0f} h" if hours >= 10 else f"{hours:.1f} h".replace(".0 h", " h")
    return f"{int(hours // 24)} days"


def day_text(moment: datetime) -> str:
    return f"{moment.day} {moment:%b}"


def sold_out_spell(before: Watch | None, now: datetime) -> str:
    """How long it was sold out before this restock, as far as the checks saw."""
    if before is None:
        return ""
    began = parse_stamp(before.out_since)
    if began is None:
        return ""
    if before.seen_in_stock():
        return f"Sold out for {duration(now - began)} (since {day_text(began)})"
    return f"Sold out at every check since {day_text(began)}"


def recent_restocks(before: Watch | None, now: datetime, days: int = 30) -> str:
    """``2 restocks in the last 30 days, the last one gone after about 25 min``."""
    if before is None:
        return ""
    cutoff = now - timedelta(days=days)
    seen = [
        r
        for r in before.restocks
        if not r.first and (at := parse_stamp(r.at)) is not None and at >= cutoff
    ]
    if not seen:
        return ""
    text = f"{len(seen)} earlier restock{'' if len(seen) == 1 else 's'} in the last {days} days"
    last = seen[-1]
    began, ended = parse_stamp(last.at), parse_stamp(last.until)
    if began and ended:
        text += f", the last one gone after about {duration(ended - began)}"
    return text


def lasted(after: Watch) -> str:
    """For a sold-out event: how long the restock that just ended lasted."""
    if not after.restocks:
        return ""
    last = after.restocks[-1]
    began, ended = parse_stamp(last.at), parse_stamp(last.until)
    if not began or not ended:
        return ""
    since = f"{day_text(began)} {began:%H:%M} UTC"
    return f"In stock for about {duration(ended - began)} (since {since})"
