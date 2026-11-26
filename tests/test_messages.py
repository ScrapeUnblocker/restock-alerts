from datetime import timedelta

from conftest import NOW, TARGET_IN

from restock_alerts import messages
from restock_alerts.collect import parse
from restock_alerts.config import Item, Rules, TargetSettings
from restock_alerts.process import Event, judge, step
from restock_alerts.state import Restock, Watch, stamp

ITEM = Item(retailer="target", product_id="52580270", max_price=25)


def restock_event(before=None, raw=TARGET_IN, item=ITEM):
    reading = parse(raw, item, TargetSettings(), NOW.date())
    verdict = judge(reading, item, Rules())
    after, kind = step(before, verdict, reading, NOW, Rules())
    return Event(kind, item, reading, verdict, before, after)


SOLD_OUT_BEFORE = Watch(
    since=stamp(NOW - timedelta(days=20)),
    out_since=stamp(NOW - timedelta(days=12)),
    restocks=(
        Restock(
            at=stamp(NOW - timedelta(days=12, minutes=25)), until=stamp(NOW - timedelta(days=12))
        ),
    ),
)


def test_restock_message_plain():
    message = messages.event_message(restock_event(SOLD_OUT_BEFORE), NOW)
    assert message.kind == "restock"
    assert message.subject.startswith("Back in stock at Target: Hamilton Beach 12 Cup")
    assert message.render(messages.PLAIN) == (
        "🟢 Back in stock at Target: Hamilton Beach 12 Cup Programmable Coffee Maker Black 46290\n"
        "$18.99 (was $27.99) · your cap $25\n"
        "Ship: arrives Nov 30 to 55403\n"
        "Pickup: order pickup ready Nov 27 at Mpls Nicollet Mall, 10 on hand, aisle L 53\n"
        "Sold out for 12 days (since 14 Nov) · 1 earlier restock in the last 30 days, "
        "the last one gone after about 25 min\n"
        "Checked 26 Nov 14:05 UTC\n"
        "https://www.target.com/p/hamilton-beach-12-cup-programmable-coffee-maker/-/A-52580270"
    )


def test_first_check_message_and_data():
    event = restock_event(None)
    message = messages.event_message(event, NOW)
    text = message.render(messages.PLAIN)
    assert text.startswith("🟢 In stock at Target: ")
    assert "Sold out" not in text and "earlier restock" not in text
    assert message.data["event"] == "in_stock"
    assert message.data["item"]["in_stock_channels"] == ["ship", "pickup"]
    assert message.data["reading"]["pickup"]["available"] is True
    assert "status" not in message.data["reading"]
    assert message.image.startswith("https://")


def test_sold_out_channel_and_name_shown():
    item = Item(retailer="target", product_id="52580270", name="Coffee maker", channels=("ship",))
    raw = {**TARGET_IN, "fulfillment": {**TARGET_IN["fulfillment"], "shippingStatus": "IN_STOCK"}}
    message = messages.event_message(restock_event(None, raw, item), NOW)
    text = message.render(messages.PLAIN)
    assert text.splitlines()[0] == "🟢 In stock at Target: Coffee maker"
    assert "Pickup:" not in text  # only the channels you watch are listed


def test_styles_escape_and_link():
    item = Item(retailer="target", product_id="52580270", name="R&D <Box> *1*")
    message = messages.event_message(restock_event(None, item=item), NOW)
    telegram = message.render(messages.TELEGRAM)
    assert telegram.startswith('<b><a href="https://www.target.com/p/')
    assert "R&amp;D &lt;Box&gt; *1*" in telegram
    slack = message.render(messages.SLACK)
    assert slack.startswith("*<https://www.target.com/p/") and "R&amp;D &lt;Box&gt;" in slack
    discord = message.render(messages.DISCORD)
    assert "R&D <Box\\> \\*1\\*" in discord and "](<https://www.target.com/p/" in discord


def test_sold_out_message():
    before = Watch(
        since=stamp(NOW - timedelta(days=1)),
        in_stock=True,
        restocks=(Restock(at=stamp(NOW - timedelta(minutes=40))),),
    )
    raw = {**TARGET_IN, "fulfillment": {**TARGET_IN["fulfillment"], "soldOut": True}}
    reading = parse(raw, ITEM, TargetSettings(), NOW.date())
    rules = Rules(confirm_sold_out=1, alert_sold_out=True)
    verdict = judge(reading, ITEM, rules)
    after, kind = step(before, verdict, reading, NOW, rules)
    message = messages.event_message(Event(kind, ITEM, reading, verdict, before, after), NOW)
    assert message.render(messages.PLAIN).splitlines()[:3] == [
        "🔴 Sold out again at Target: Hamilton Beach 12 Cup Programmable Coffee Maker Black 46290",
        "In stock for about 40 min (since 26 Nov 13:25 UTC)",
        "Checked 26 Nov 14:05 UTC",
    ]
