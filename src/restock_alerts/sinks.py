"""Destinations behind one small interface.

A sink has a ``name`` and three methods: ``send(message)`` delivers one message,
``preview(message)`` returns what ``send`` would deliver (for ``--dry-run``) and
``test()`` sends a short hello (for ``test-destinations``). ``send`` and ``test`` raise
:class:`DeliveryError` on failure. Secrets come from environment variables, never from
``config.yml``. To add a destination, write a class like the ones below and register it
in ``REGISTRY``.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from . import messages
from .messages import SOURCE, Message, Style

USER_AGENT = "restock-alerts (+https://github.com/ScrapeUnblocker/restock-alerts)"
CONNECTED = "restock-alerts is connected. Restocks of the products you watch will arrive here."
PUSHOVER_URL = "https://api.pushover.net/1/messages.json"


class DeliveryError(RuntimeError):
    """A destination did not accept the message."""


class Sink(Protocol):
    name: str

    def send(self, message: Message) -> None: ...

    def preview(self, message: Message) -> str: ...

    def test(self) -> str: ...


class Http(Protocol):
    def post(self, url: str, **kwargs: Any) -> Any: ...


def post(
    http: Http,
    url: str,
    *,
    sink: str,
    json: Any = None,
    data: Mapping[str, Any] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> Any:
    """POST once, retrying once on 429/502/503. Errors never include the URL, because
    webhook URLs and bot tokens are secrets."""
    body: dict[str, Any] = {"json": json} if data is None else {"data": dict(data)}
    for attempt in (1, 2):
        try:
            resp = http.post(url, timeout=20.0, **body)
        except httpx.HTTPError as exc:
            raise DeliveryError(f"{sink}: request failed ({type(exc).__name__})") from None
        if resp.status_code in (429, 502, 503) and attempt == 1:
            (sleep or time.sleep)(_retry_after(resp))
            continue
        if resp.status_code >= 300:
            text = (resp.text or "")[:200].replace("\n", " ")
            raise DeliveryError(f"{sink}: HTTP {resp.status_code} {text}".rstrip())
        return resp
    raise AssertionError("unreachable")


def _retry_after(resp: Any) -> float:
    try:
        return min(float(resp.headers.get("Retry-After", "2")), 10.0)
    except (TypeError, ValueError, AttributeError):
        return 2.0


class ChatSink:
    """A chat webhook or bot that shows formatted text."""

    name = "chat"
    style: Style = messages.PLAIN
    limit = 3900

    def __init__(self, url: str, http: Http) -> None:
        self.url = url
        self.http = http

    def text(self, message: Message) -> str:
        text = message.render(self.style)
        return text if len(text) <= self.limit else text[: self.limit - 1] + "…"

    def preview(self, message: Message) -> str:
        return self.text(message)

    def send(self, message: Message) -> None:
        post(self.http, self.url, json=self.payload(message), sink=self.name)

    def test(self) -> str:
        post(self.http, self.url, json=self.wrap(CONNECTED), sink=self.name)
        return "test message sent"

    def payload(self, message: Message) -> dict[str, Any]:
        return self.wrap(self.text(message))

    def wrap(self, text: str) -> dict[str, Any]:
        raise NotImplementedError


class TelegramSink(ChatSink):
    """Telegram bot message to one chat (you, a group or a channel). Only a bot token and
    a chat id are needed. Link previews are off: the message already says it all.

    ``TELEGRAM_API_BASE`` points at a self-hosted Bot API server instead of
    api.telegram.org.
    """

    name = "telegram"
    style = messages.TELEGRAM
    limit = 4000

    def __init__(
        self, bot_token: str, chat_id: str, http: Http, api_base: str = "https://api.telegram.org"
    ) -> None:
        super().__init__(f"{api_base.rstrip('/')}/bot{bot_token}/sendMessage", http)
        self.chat_id = chat_id

    def wrap(self, text: str) -> dict[str, Any]:
        return {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        }


class SlackSink(ChatSink):
    """Slack incoming webhook (mrkdwn)."""

    name = "slack"
    style = messages.SLACK
    limit = 3900

    def wrap(self, text: str) -> dict[str, Any]:
        return {"text": text, "unfurl_links": False, "unfurl_media": False}


class DiscordSink(ChatSink):
    """Discord channel webhook. An alert is an embed: the title linked to the product, the
    details below and the product photo. Mentions never ping."""

    name = "discord"
    style = messages.DISCORD
    limit = 1990
    colors = {"restock": 0x2EAD4B, "in_stock": 0x2EAD4B, "sold_out": 0xD93F3F}

    def payload(self, message: Message) -> dict[str, Any]:
        if message.kind not in self.colors:
            return self.wrap(self.text(message))
        embed: dict[str, Any] = {
            "title": message.headline[:256],
            "description": message.body(self.style)[:4000],
            "color": self.colors[message.kind],
        }
        if message.url:
            embed["url"] = message.url
        if message.image:
            embed["thumbnail"] = {"url": message.image}
        return {"embeds": [embed], "allowed_mentions": {"parse": []}}

    def wrap(self, text: str) -> dict[str, Any]:
        # flags 4 = no link embeds
        return {"content": text[: self.limit], "allowed_mentions": {"parse": []}, "flags": 4}


class PushoverSink:
    """Pushover push notification to your phone. Needs an application token and your user
    key. ``delivery.pushover_priority`` 1 (the default) skips your quiet hours."""

    name = "pushover"

    def __init__(self, token: str, user: str, http: Http, priority: int = 1) -> None:
        self.token = token
        self.user = user
        self.http = http
        self.priority = priority

    def form(self, message: Message) -> dict[str, Any]:
        title = message.subject
        body = message.body(messages.PLAIN)
        form: dict[str, Any] = {
            "token": self.token,
            "user": self.user,
            "title": title[:250],
            "message": body[:1024] or title[:1024],
            "priority": str(self.priority),
        }
        if message.url:
            form["url"] = message.url[:512]
            form["url_title"] = f"Open at {message.retailer}"[:100] if message.retailer else "Open"
        return form

    def preview(self, message: Message) -> str:
        form = self.form(message)
        text = f"{form['title']}\n{form['message']}"
        return text + (f"\n{form['url']}" if "url" in form else "")

    def send(self, message: Message) -> None:
        post(self.http, PUSHOVER_URL, data=self.form(message), sink=self.name)

    def test(self) -> str:
        form = {"token": self.token, "user": self.user, "message": CONNECTED, "priority": "0"}
        post(self.http, PUSHOVER_URL, data=form, sink=self.name)
        return "test notification sent"


class WebhookSink:
    """Any URL that accepts a JSON POST: Zapier, Make, n8n, Home Assistant, your own
    endpoint.

    One POST per message: ``source``, ``event`` (restock, in_stock or sold_out),
    ``subject``, the plain-text message as ``text``, and ``item`` and ``reading`` as JSON.
    """

    name = "webhook"

    def __init__(self, url: str, http: Http) -> None:
        self.url = url
        self.http = http

    def payload(self, message: Message) -> dict[str, Any]:
        return {
            "source": SOURCE,
            "subject": message.subject,
            "text": message.render(messages.PLAIN),
            **message.data,
        }

    def preview(self, message: Message) -> str:
        return self.payload(message)["text"]

    def send(self, message: Message) -> None:
        post(self.http, self.url, json=self.payload(message), sink=self.name)

    def test(self) -> str:
        payload = {"source": SOURCE, "event": "test", "text": CONNECTED}
        post(self.http, self.url, json=payload, sink=self.name)
        return "test message sent"


@dataclass(frozen=True)
class SinkSpec:
    env: tuple[str, ...]  # every one must be set for the destination to be used
    build: Callable[[Mapping[str, str], Http, int], Sink]


REGISTRY: dict[str, SinkSpec] = {
    "telegram": SinkSpec(
        ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"),
        lambda env, http, _: TelegramSink(
            env["TELEGRAM_BOT_TOKEN"].strip(),
            env["TELEGRAM_CHAT_ID"].strip(),
            http,
            api_base=(env.get("TELEGRAM_API_BASE") or "").strip() or "https://api.telegram.org",
        ),
    ),
    "discord": SinkSpec(
        ("DISCORD_WEBHOOK_URL",),
        lambda env, http, _: DiscordSink(env["DISCORD_WEBHOOK_URL"].strip(), http),
    ),
    "slack": SinkSpec(
        ("SLACK_WEBHOOK_URL",),
        lambda env, http, _: SlackSink(env["SLACK_WEBHOOK_URL"].strip(), http),
    ),
    "pushover": SinkSpec(
        ("PUSHOVER_APP_TOKEN", "PUSHOVER_USER_KEY"),
        lambda env, http, priority: PushoverSink(
            env["PUSHOVER_APP_TOKEN"].strip(), env["PUSHOVER_USER_KEY"].strip(), http, priority
        ),
    ),
    "webhook": SinkSpec(
        ("ALERT_WEBHOOK_URL",),
        lambda env, http, _: WebhookSink(env["ALERT_WEBHOOK_URL"].strip(), http),
    ),
}


def build_sinks(
    destinations: Sequence[str], env: Mapping[str, str], http: Http, pushover_priority: int = 1
) -> tuple[list[Sink], list[str]]:
    """Build every listed destination whose secrets are set, in config order.

    Returns ``(sinks, skipped)`` where ``skipped`` names destinations with a secret missing.
    """
    sinks: list[Sink] = []
    skipped: list[str] = []
    for name in destinations:
        spec = REGISTRY[name]
        if all((env.get(var) or "").strip() for var in spec.env):
            sinks.append(spec.build(env, http, pushover_priority))
        else:
            skipped.append(name)
    return sinks, skipped


def new_http_client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": USER_AGENT}, follow_redirects=False)
