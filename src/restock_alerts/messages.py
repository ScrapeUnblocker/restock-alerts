"""Message text: one message per stock event.

Every message renders in four styles: ``PLAIN`` (webhooks, Pushover and the terminal),
``SLACK`` (mrkdwn), ``DISCORD`` (Markdown) and ``TELEGRAM`` (HTML).
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .collect import Reading, short_date
from .process import Event, day_text, lasted, money, recent_restocks, sold_out_spell

SOURCE = "restock-alerts"
HEADLINE = {
    "restock": ("🟢", "Back in stock"),
    "in_stock": ("🟢", "In stock"),
    "sold_out": ("🔴", "Sold out again"),
}
CHANNEL_NAME = {"ship": "Ship", "pickup": "Pickup"}


@dataclass(frozen=True)
class Style:
    name: str
    escape: Callable[[str], str]
    bold: Callable[[str], str]
    italic: Callable[[str], str]
    link: Callable[[str, str], str]


def _slack_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _discord_escape(text: str) -> str:
    return re.sub(r"([\\*_~`|>#\[\]])", r"\\\1", text)


PLAIN = Style(
    name="plain",
    escape=lambda s: s,
    bold=lambda s: s,
    italic=lambda s: s,
    link=lambda text, url: text,
)
SLACK = Style(
    name="slack",
    escape=_slack_escape,
    bold=lambda s: f"*{s}*",
    italic=lambda s: f"_{s}_",
    link=lambda text, url: f"<{url}|{text.replace('|', '/')}>" if url else text,
)
DISCORD = Style(
    name="discord",
    escape=_discord_escape,
    bold=lambda s: f"**{s}**",
    italic=lambda s: f"*{s}*",
    link=lambda text, url: f"[{text}](<{url}>)" if url else text,
)
TELEGRAM = Style(
    name="telegram",
    escape=lambda s: html.escape(s, quote=False),
    bold=lambda s: f"<b>{s}</b>",
    italic=lambda s: f"<i>{s}</i>",
    link=lambda text, url: f'<a href="{html.escape(url, quote=True)}">{text}</a>' if url else text,
)


@dataclass(frozen=True)
class Message:
    """One thing to deliver."""

    kind: str  # restock, in_stock, sold_out or test
    subject: str  # one-line summary (webhook ``subject``, Pushover title)
    headline: str  # the first line, plain text (Discord embed title)
    url: str  # the product page
    body: Callable[[Style], str]  # everything below the headline, in a style
    image: str = ""
    retailer: str = ""
    data: dict[str, Any] = field(default_factory=dict)  # structured fields for webhooks

    def render(self, style: Style) -> str:
        """The whole message: headline (linked to the product) and body."""
        if style is PLAIN:
            head = self.headline
        else:
            head = style.bold(style.link(style.escape(self.headline), self.url))
        body = self.body(style)
        text = f"{head}\n{body}" if body else head
        if style is PLAIN and self.url:
            text += f"\n{self.url}"
        return text


def short_text(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0] if " " in text[:limit] else text[:limit]
    return cut.rstrip(" ,.;:-*/|+(") + "…"


def price_line(event: Event) -> str:
    r, item = event.reading, event.item
    parts = []
    if r.price is not None:
        price = money(r.price, r.currency)
        if r.was_price is not None and r.was_price > r.price:
            price += f" (was {money(r.was_price, r.currency)})"
        parts.append(price)
    else:
        parts.append("price not shown")
    if r.seller:
        parts.append(f"sold by {r.seller}")
    if item.max_price is not None:
        parts.append(f"your cap {money(item.max_price, r.currency or 'USD')}")
    if r.preorder:
        release = short_date(r.release) or r.release
        parts.append(f"pre-order, release {release}" if release else "pre-order")
    return " · ".join(parts)


def channel_lines(event: Event) -> list[str]:
    lines = []
    for name in event.item.channels:
        channel = event.reading.channel(name)
        if channel is None:
            continue
        if channel.available:
            lines.append(f"{CHANNEL_NAME[name]}: {channel.note or 'in stock'}")
        else:
            lines.append(f"{CHANNEL_NAME[name]}: sold out")
    return lines


def history_line(event: Event, now: datetime) -> str:
    parts = [sold_out_spell(event.before, now), recent_restocks(event.before, now)]
    return " · ".join(p for p in parts if p)


def checked(now: datetime) -> str:
    return f"Checked {day_text(now)} {now:%H:%M} UTC"


def render_body(event: Event, now: datetime, style: Style) -> str:
    if event.kind == "sold_out":
        lines = [style.escape(lasted(event.after))]
    else:
        lines = [style.bold(style.escape(price_line(event)))]
        lines += [style.escape(line) for line in channel_lines(event)]
        lines.append(style.escape(history_line(event, now)))
    lines.append(style.italic(style.escape(checked(now))))
    return "\n".join(line for line in lines if line)


def event_message(event: Event, now: datetime) -> Message:
    icon, words = HEADLINE[event.kind]
    r = event.reading
    label = short_text(event.item.label(r.title), 150)
    item = event.item
    return Message(
        kind=event.kind,
        subject=f"{words} at {r.retailer}: {short_text(label, 90)}",
        headline=f"{icon} {words} at {r.retailer}: {label}",
        url=r.url,
        body=lambda style: render_body(event, now, style),
        image=r.image,
        retailer=r.retailer,
        data={
            "event": event.kind,
            "checked_at": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "item": {
                "key": item.key,
                "retailer": item.retailer,
                "product_id": item.product_id,
                "name": label,
                "max_price": item.max_price,
                "channels": list(item.channels),
                "in_stock_channels": list(event.verdict.channels),
            },
            "reading": _reading_data(r),
        },
    )


def _reading_data(r: Reading) -> dict[str, Any]:
    data = r.to_dict()
    data.pop("status", None)
    return data
