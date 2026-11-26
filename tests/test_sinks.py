import httpx
import pytest
from conftest import NOW, FakeHttp
from test_messages import SOLD_OUT_BEFORE, restock_event

from restock_alerts import messages
from restock_alerts.sinks import (
    PUSHOVER_URL,
    DeliveryError,
    DiscordSink,
    PushoverSink,
    SlackSink,
    TelegramSink,
    WebhookSink,
    build_sinks,
    post,
)

MESSAGE = messages.event_message(restock_event(SOLD_OUT_BEFORE), NOW)


def test_telegram_payload(fake_http):
    sink = TelegramSink("123:abc", "-100200", fake_http, api_base="https://tg.example/")
    sink.send(MESSAGE)
    sent = fake_http.posts[0]
    assert sent["url"] == "https://tg.example/bot123:abc/sendMessage"
    assert sent["json"]["chat_id"] == "-100200"
    assert sent["json"]["parse_mode"] == "HTML"
    assert sent["json"]["text"].startswith("<b><a href=")
    assert sent["json"]["link_preview_options"] == {"is_disabled": True}


def test_discord_embed(fake_http):
    DiscordSink("https://discord.example/hook", fake_http).send(MESSAGE)
    body = fake_http.posts[0]["json"]
    embed = body["embeds"][0]
    assert embed["title"].startswith("🟢 Back in stock at Target")
    assert embed["url"].startswith("https://www.target.com/")
    assert embed["color"] == 0x2EAD4B
    assert embed["thumbnail"] == {"url": MESSAGE.image}
    assert "**$18.99 (was $27.99) · your cap $25**" in embed["description"]
    assert body["allowed_mentions"] == {"parse": []}


def test_slack_payload(fake_http):
    SlackSink("https://hooks.slack.example/x", fake_http).send(MESSAGE)
    body = fake_http.posts[0]["json"]
    assert body["text"].startswith("*<https://www.target.com/")
    assert body["unfurl_links"] is False


def test_pushover_form(fake_http):
    sink = PushoverSink("apptoken", "userkey", fake_http, priority=1)
    sink.send(MESSAGE)
    sent = fake_http.posts[0]
    assert sent["url"] == PUSHOVER_URL and "json" not in sent
    form = sent["data"]
    assert form["token"] == "apptoken" and form["user"] == "userkey"
    assert form["title"].startswith("Back in stock at Target: Hamilton Beach")
    assert form["message"].startswith("$18.99 (was $27.99)")
    assert form["url"].startswith("https://www.target.com/")
    assert form["url_title"] == "Open at Target"
    assert form["priority"] == "1"
    assert len(form["message"]) <= 1024


def test_webhook_payload(fake_http):
    WebhookSink("https://hooks.example/restock", fake_http).send(MESSAGE)
    body = fake_http.posts[0]["json"]
    assert body["source"] == "restock-alerts" and body["event"] == "restock"
    assert body["item"]["key"] == "target:52580270"
    assert body["reading"]["price"] == 18.99
    assert body["text"].startswith("🟢 Back in stock at Target")


def test_errors_never_show_the_url():
    http = FakeHttp({"secret-token": [500]})
    sink = TelegramSink("secret-token", "1", http)
    with pytest.raises(DeliveryError) as info:
        sink.send(MESSAGE)
    assert "secret-token" not in str(info.value) and "HTTP 500" in str(info.value)

    class Broken:
        def post(self, url, **kwargs):
            raise httpx.ConnectError("boom")

    with pytest.raises(DeliveryError, match="request failed") as info:
        post(Broken(), "https://hooks.example/secret-path", json={}, sink="webhook")
    assert "secret-path" not in str(info.value)


def test_post_retries_once_on_429():
    http = FakeHttp({"hook": [429, 200]})
    slept = []
    post(http, "https://hook", json={}, sink="webhook", sleep=slept.append)
    assert len(http.posts) == 2 and slept == [2.0]


def test_build_sinks_skips_missing_secrets(fake_http):
    env = {
        "DISCORD_WEBHOOK_URL": "https://discord.example/hook",
        "TELEGRAM_BOT_TOKEN": "123:abc",  # chat id missing
        "PUSHOVER_APP_TOKEN": "a",
        "PUSHOVER_USER_KEY": "u",
    }
    sinks, skipped = build_sinks(
        ["telegram", "discord", "slack", "pushover", "webhook"], env, fake_http, -1
    )
    assert [s.name for s in sinks] == ["discord", "pushover"]
    assert skipped == ["telegram", "slack", "webhook"]
    assert sinks[1].priority == -1


def test_test_messages(fake_http):
    assert PushoverSink("a", "u", fake_http).test() == "test notification sent"
    assert fake_http.posts[0]["data"]["priority"] == "0"
    assert DiscordSink("https://d.example", fake_http).test() == "test message sent"
    assert fake_http.posts[1]["json"]["content"].startswith("restock-alerts is connected")
