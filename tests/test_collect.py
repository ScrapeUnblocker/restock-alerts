from datetime import date

import pytest
from conftest import (
    BESTBUY_MARKETPLACE,
    BESTBUY_OUT,
    MEDIAMARKT_IN,
    MEDIAMARKT_OUT,
    SMYTHS_IN,
    SMYTHS_OUT,
    TARGET_IN,
    TARGET_OUT,
    WALMART_IN,
    WALMART_MARKETPLACE,
    FakeClient,
)
from scrapeunblocker import AuthenticationError, NotFoundError, ServerError

from restock_alerts.collect import FATAL, FetchError, parse, read
from restock_alerts.config import Item, TargetSettings

TODAY = date(2026, 11, 26)
TARGET = TargetSettings()


def item(retailer, product_id, **kw):
    if retailer == "mediamarkt":
        kw.setdefault("country", "de")
        kw.setdefault("banner", "mediamarkt")
    return Item(retailer=retailer, product_id=product_id, **kw)


def test_target_in_stock_at_store_and_online():
    r = parse(TARGET_IN, item("target", "52580270"), TARGET, TODAY)
    assert r.known and r.retailer == "Target"
    assert r.price == 18.99 and r.was_price == 27.99 and r.currency == "USD"
    assert r.ship.available and r.ship.note == "arrives Nov 30 to 55403"
    assert r.pickup.available
    assert (
        r.pickup.note == "order pickup ready Nov 27 at Mpls Nicollet Mall, 10 on hand, aisle L 53"
    )
    assert r.by_retailer is None  # Target does not name a seller


def test_target_sold_out():
    r = parse(TARGET_OUT, item("target", "1014840165"), TARGET, TODAY)
    assert r.known
    assert not r.ship.available and not r.pickup.available


def test_target_without_fulfillment_is_unknown():
    raw = {k: v for k, v in TARGET_IN.items() if k != "fulfillment"} | {"fulfillment": None}
    r = parse(raw, item("target", "52580270"), TARGET, TODAY)
    assert r.known is False
    assert r.title.startswith("Hamilton Beach")


def test_target_shelf_stock_counts_as_pickup():
    raw = {**TARGET_IN, "fulfillment": {**TARGET_IN["fulfillment"]}}
    raw["fulfillment"]["store"] = {
        "storeName": "Mpls Nicollet Mall",
        "pickupStatus": "UNAVAILABLE",
        "inStoreStatus": "LIMITED_STOCK",
        "quantity": 2,
    }
    r = parse(raw, item("target", "52580270"), TARGET, TODAY)
    assert r.pickup.available
    assert r.pickup.note == "on the shelf at Mpls Nicollet Mall (no order pickup), 2 on hand"


def test_target_preorder():
    raw = {**TARGET_IN, "fulfillment": {**TARGET_IN["fulfillment"]}}
    raw["fulfillment"]["shippingStatus"] = "PRE_ORDER_SELLABLE"
    r = parse(raw, item("target", "52580270"), TARGET, TODAY)
    assert r.preorder and r.ship.available


def test_bestbuy_empty_flags_mean_sold_out():
    r = parse(BESTBUY_OUT, item("bestbuy", "6645345"), TARGET, TODAY)
    assert r.known
    assert not r.ship.available and not r.pickup.available
    assert r.seller == "Best Buy" and r.by_retailer is True


def test_bestbuy_marketplace_seller():
    r = parse(BESTBUY_MARKETPLACE, item("bestbuy", "13138238"), TARGET, TODAY)
    assert r.ship.available and r.ship.note == "shipping"
    assert not r.pickup.available
    assert r.by_retailer is False and r.seller == "Marketplace seller 7791"


def test_bestbuy_preorder_from_release_date():
    raw = {**BESTBUY_OUT, "available": True, "availableForShipping": True}
    raw["releaseDate"] = "2026-12-12T06:00Z"
    r = parse(raw, item("bestbuy", "6645345"), TARGET, TODAY)
    assert r.preorder and r.release == "2026-12-12"


def test_walmart_unwraps_product_and_reads_seller():
    r = parse(WALMART_MARKETPLACE, item("walmart", "19402160990"), TARGET, TODAY)
    assert r.known and r.price == 72.99
    assert r.ship.available and r.ship.note.startswith("Free shipping, arrives by Tue, Dec 1")
    assert r.pickup is None  # no pickup option in the answer
    assert r.seller == "PrimeSellerHQ LLC" and r.by_retailer is False
    assert r.image == "https://i5.walmartimages.com/seo/etb.jpeg"


def test_walmart_sold_by_walmart():
    r = parse(WALMART_IN, item("walmart", "18638703162"), TARGET, TODAY)
    assert r.by_retailer is True and r.was_price == 279.0
    assert r.ship.available and not r.pickup.available


def test_walmart_out_of_stock_overrides_options():
    raw = {"product": {**WALMART_IN["product"], "inStock": False, "availability": "OUT_OF_STOCK"}}
    r = parse(raw, item("walmart", "18638703162"), TARGET, TODAY)
    assert not r.ship.available


def test_smyths_in_and_out():
    url = "https://www.smythstoys.com/uk/en-gb/toys/lego-star-wars-75455-boba-fett-set-for-adults/p/259087"
    r = parse(SMYTHS_IN, item("smyths", "259087", url=url), TARGET, TODAY)
    assert r.title == "Lego Star Wars 75455 Boba Fett Set for Adults"
    assert r.price == 119.99 and r.currency == "GBP"
    assert r.ship.available and r.pickup.available
    assert r.ship.note == "home delivery (FREE Standard Delivery)"
    assert r.pickup.note == "click & collect (FREE collection within 2 hours)"
    assert r.by_retailer is True and r.seller == ""
    out = parse(SMYTHS_OUT, item("smyths", "263924"), TARGET, TODAY)
    assert not out.ship.available and not out.pickup.available
    assert out.url.endswith("text=263924")


def test_smyths_preorder():
    raw = {**SMYTHS_OUT, "hdSection": {"stockStatus": "PREORDER", "isPreOrder": True}}
    raw["hdSection"]["expectedStockDate"] = "2026-12-04"
    r = parse(raw, item("smyths", "263924"), TARGET, TODAY)
    assert r.ship.available and r.preorder and r.release == "2026-12-04"


def test_mediamarkt_in_and_out():
    r = parse(MEDIAMARKT_IN, item("mediamarkt", "3071615"), TARGET, TODAY)
    assert r.price == 1599.0 and r.currency == "EUR"
    assert r.ship.note == "delivery Nov 27-Nov 28, free shipping"
    assert r.pickup.available and r.pickup.note == "store pickup (some stores)"
    assert r.by_retailer is True and r.seller == "MediaMarkt"
    out = parse(MEDIAMARKT_OUT, item("mediamarkt", "3003326"), TARGET, TODAY)
    assert not out.ship.available and not out.pickup.available


def test_mediamarkt_marketplace():
    raw = {**MEDIAMARKT_IN, "marketplace": True, "seller": {"name": "Game World"}}
    r = parse(raw, item("mediamarkt", "3071615"), TARGET, TODAY)
    assert r.by_retailer is False and r.seller == "Game World"


def test_read_passes_store_and_zip():
    client = FakeClient({"52580270": TARGET_IN})
    reading, raw, calls = read(
        client, item("target", "52580270"), TargetSettings("2088", "60601"), today=TODAY
    )
    assert client.calls == [("target_product", "52580270", {"store_id": "2088", "zip": "60601"})]
    assert calls == 1 and raw["tcin"] == "52580270"
    assert reading.ship.note == "arrives Nov 30 to 60601"


def test_read_mediamarkt_passes_shop():
    client = FakeClient({"3071615": MEDIAMARKT_IN})
    read(client, item("mediamarkt", "3071615", country="at"), TARGET, today=TODAY)
    assert client.calls[0][2] == {"country": "at", "banner": "mediamarkt"}


def test_read_retries_once():
    client = FakeClient({"6645345": [ServerError("busy", status_code=502), BESTBUY_OUT]})
    slept = []
    reading, _, calls = read(
        client, item("bestbuy", "6645345"), TARGET, today=TODAY, sleep=slept.append
    )
    assert calls == 2 and slept == [5.0] and reading.known


def test_read_gives_up_after_two_tries():
    client = FakeClient({"6645345": ServerError("busy", status_code=502)})
    with pytest.raises(FetchError) as info:
        read(client, item("bestbuy", "6645345"), TARGET, today=TODAY, sleep=lambda s: None)
    assert info.value.calls == 2 and not info.value.permanent


def test_read_unknown_product_is_permanent():
    client = FakeClient({"1": NotFoundError("no such item", status_code=404)})
    with pytest.raises(FetchError) as info:
        read(client, item("walmart", "1"), TARGET, today=TODAY)
    assert info.value.permanent and info.value.calls == 1
    assert "Walmart has no product 1" in str(info.value)


def test_read_fatal_errors_pass_through():
    client = FakeClient({"1": AuthenticationError("bad key", status_code=401)})
    with pytest.raises(FATAL):
        read(client, item("walmart", "1"), TARGET, today=TODAY)
