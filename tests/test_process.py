from datetime import timedelta

import pytest
from conftest import NOW

from restock_alerts.collect import Channel, Reading
from restock_alerts.config import Item, Rules
from restock_alerts.process import (
    duration,
    judge,
    lasted,
    money,
    recent_restocks,
    sold_out_spell,
    step,
)
from restock_alerts.state import Restock, Watch, stamp

ITEM = Item(retailer="bestbuy", product_id="6645345", name="ETB", max_price=60)
RULES = Rules()


def reading(*, ship=True, pickup=False, price=49.99, by_retailer=True, known=True, **kw):
    return Reading(
        key=ITEM.key,
        retailer="Best Buy",
        title="Elite Trainer Box",
        url="https://www.bestbuy.com/site/-/6645345.p",
        known=known,
        price=price,
        currency="USD",
        ship=Channel(ship, "shipping" if ship else ""),
        pickup=Channel(pickup),
        seller="Best Buy" if by_retailer else "Marketplace seller 7791",
        by_retailer=by_retailer,
        **kw,
    )


IN = reading()
OUT = reading(ship=False)


def test_money():
    assert money(49.99) == "$49.99"
    assert money(60) == "$60"
    assert money(1599, "EUR") == "€1,599"
    assert money(119.99, "GBP") == "£119.99"
    assert money(5, "PLN") == "5 PLN"


@pytest.mark.parametrize(
    ("kwargs", "item", "reason"),
    [
        ({"ship": False}, ITEM, "sold out"),
        ({"by_retailer": False}, ITEM, "sold by Marketplace seller 7791, not Best Buy"),
        ({"price": 72.99}, ITEM, "$72.99 is above your $60 cap"),
        ({"price": None}, ITEM, "no price in the answer"),
        (
            {"ship": True, "pickup": False},
            Item(retailer="bestbuy", product_id="1", channels=("pickup",)),
            "only shipping is open; you asked for pickup",
        ),
    ],
)
def test_judge_reasons(kwargs, item, reason):
    verdict = judge(reading(**kwargs), item, RULES)
    assert verdict is not None and not verdict.match
    assert verdict.reason.startswith(reason)


def test_judge_match_lists_open_channels():
    verdict = judge(reading(ship=True, pickup=True), ITEM, RULES)
    assert verdict.match and verdict.channels == ("ship", "pickup")


def test_judge_unknown_and_overrides():
    assert judge(reading(known=False), ITEM, RULES) is None
    anyone = Item(retailer="bestbuy", product_id="1", retailer_only=False)
    assert judge(reading(by_retailer=False), anyone, RULES).match
    assert judge(reading(price=60.0), ITEM, RULES).match  # at the cap is fine


def test_judge_preorders_rule():
    pre = reading(preorder=True)
    assert judge(pre, ITEM, RULES).match
    assert not judge(pre, ITEM, Rules(preorders=False)).match


def run(watch, r, minutes=0, rules=RULES):
    verdict = judge(r, ITEM, rules)
    return step(watch, verdict, r, NOW + timedelta(minutes=minutes), rules)


def test_first_check_in_stock_alerts_once():
    watch, kind = run(None, IN)
    assert kind == "in_stock" and watch.in_stock
    assert watch.restocks[-1].first and watch.restocks[-1].price == 49.99
    watch, kind = run(watch, IN, 15)
    assert kind == "" and watch.in_stock


def test_first_check_sold_out_then_restock():
    watch, kind = run(None, OUT)
    assert kind == "" and not watch.in_stock and watch.out_since == stamp(NOW)
    same, kind = run(watch, OUT, 15)
    assert kind == "" and same == watch  # nothing changes while it stays sold out
    watch, kind = run(watch, IN, 30)
    assert kind == "restock"
    assert watch.out_since == "" and not watch.restocks[-1].first


def test_one_sold_out_blip_does_not_realert():
    watch, _ = run(None, IN)
    watch, kind = run(watch, OUT, 15)
    assert kind == "" and watch.in_stock and watch.out_reads == 1
    assert watch.pending_out == stamp(NOW + timedelta(minutes=15))
    watch, kind = run(watch, IN, 30)
    assert kind == "" and watch.in_stock and watch.out_reads == 0 and watch.pending_out == ""


def test_confirmed_sell_out_then_next_restock_alerts():
    watch, _ = run(None, OUT)
    watch, _ = run(watch, IN, 15)
    watch, _ = run(watch, OUT, 40)
    watch, kind = run(watch, OUT, 55)
    assert kind == "" and not watch.in_stock
    assert watch.out_since == stamp(NOW + timedelta(minutes=40))  # the first sold-out read
    assert watch.restocks[-1].until == stamp(NOW + timedelta(minutes=40))
    watch, kind = run(watch, IN, 24 * 60)
    assert kind == "restock" and len(watch.restocks) == 2


def test_alert_sold_out_rule():
    rules = Rules(confirm_sold_out=1, alert_sold_out=True)
    watch, _ = run(None, IN, rules=rules)
    watch, kind = run(watch, OUT, 25, rules=rules)
    assert kind == "sold_out" and not watch.in_stock
    assert lasted(watch) == "In stock for about 25 min (since 26 Nov 14:05 UTC)"


def test_price_above_cap_counts_as_not_in_stock():
    watch, _ = run(None, IN)
    watch, _ = run(watch, reading(price=99.99), 15)
    watch, kind = run(watch, reading(price=99.99), 30)
    assert kind == "" and not watch.in_stock
    watch, kind = run(watch, IN, 45)
    assert kind == "restock"


def test_unknown_reading_changes_nothing():
    unknown = reading(known=False)
    assert step(None, judge(unknown, ITEM, RULES), unknown, NOW, RULES) == (None, "")
    watch, _ = run(None, IN)
    assert step(watch, None, unknown, NOW, RULES) == (watch, "")


def test_duration():
    assert duration(timedelta(seconds=20)) == "1 min"
    assert duration(timedelta(minutes=25)) == "25 min"
    assert duration(timedelta(minutes=90)) == "1.5 h"
    assert duration(timedelta(hours=3)) == "3 h"
    assert duration(timedelta(hours=30)) == "30 h"
    assert duration(timedelta(days=12, hours=5)) == "12 days"


def test_history_texts():
    watch = Watch(
        since=stamp(NOW - timedelta(days=40)),
        out_since=stamp(NOW - timedelta(days=12)),
        restocks=(
            Restock(at=stamp(NOW - timedelta(days=39)), until=stamp(NOW - timedelta(days=38))),
            Restock(
                at=stamp(NOW - timedelta(days=20)),
                until=stamp(NOW - timedelta(days=20) + timedelta(minutes=25)),
            ),
            Restock(
                at=stamp(NOW - timedelta(days=12, minutes=40)),
                until=stamp(NOW - timedelta(days=12)),
            ),
        ),
    )
    assert sold_out_spell(watch, NOW) == "Sold out for 12 days (since 14 Nov)"
    assert recent_restocks(watch, NOW) == (
        "2 earlier restocks in the last 30 days, the last one gone after about 40 min"
    )
    never = Watch(since=stamp(NOW - timedelta(days=3)), out_since=stamp(NOW - timedelta(days=3)))
    assert sold_out_spell(never, NOW) == "Sold out at every check since 23 Nov"
    assert recent_restocks(never, NOW) == ""
    assert sold_out_spell(None, NOW) == "" and recent_restocks(None, NOW) == ""
