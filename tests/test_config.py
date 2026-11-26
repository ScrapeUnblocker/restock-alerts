from pathlib import Path

import pytest

from restock_alerts.config import (
    CHANNELS,
    ConfigError,
    load_config,
    name_from_link,
    parse_config,
    parse_link,
)


@pytest.mark.parametrize(
    ("url", "retailer", "product_id"),
    [
        ("https://www.target.com/p/hamilton-beach-coffee-maker/-/A-52580270", "target", "52580270"),
        ("https://www.target.com/p/x/-/A-1?preselect=87654321#lnk=sametab", "target", "87654321"),
        ("https://www.bestbuy.com/site/-/6645345.p?skuId=6645345", "bestbuy", "6645345"),
        (
            "https://www.bestbuy.com/product/hp-omnibook/JJGQJRKGTJ/sku/6613868",
            "bestbuy",
            "6613868",
        ),
        ("https://www.bestbuy.com/site/some-product/6645345.p", "bestbuy", "6645345"),
        (
            "https://www.walmart.com/ip/HP-15-6-inch-Laptop/18638703162?athbdg=L1800",
            "walmart",
            "18638703162",
        ),
        ("https://www.walmart.com/ip/19402160990", "walmart", "19402160990"),
        (
            "https://www.smythstoys.com/uk/en-gb/toys/lego/lego-star-wars-75455-boba-fett/p/259087",
            "smyths",
            "259087",
        ),
    ],
)
def test_parse_link(url, retailer, product_id):
    found = parse_link(url)
    assert (found["retailer"], found["product_id"]) == (retailer, product_id)


@pytest.mark.parametrize(
    ("host", "country", "banner"),
    [
        ("www.mediamarkt.de", "de", "mediamarkt"),
        ("www.saturn.de", "de", "saturn"),
        ("www.mediaworld.it", "it", "mediamarkt"),
        ("www.mediamarkt.com.tr", "tr", "mediamarkt"),
    ],
)
def test_parse_link_mediamarkt_shops(host, country, banner):
    found = parse_link(f"https://{host}/de/product/_apple-iphone-18-pro-max-3071615.html")
    assert found == {
        "retailer": "mediamarkt",
        "product_id": "3071615",
        "country": country,
        "banner": banner,
    }


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("https://www.amazon.com/dp/B0ABC", "not a supported retailer"),
        ("https://www.target.com/c/toys/-/N-5xtb0", "no product id"),
        ("www.bestbuy.com/site/-/6645345.p", "not a product link"),
    ],
)
def test_parse_link_errors(url, message):
    with pytest.raises(ConfigError, match=message):
        parse_link(url)


def test_name_from_link():
    url = "https://www.smythstoys.com/uk/en-gb/toys/lego-star-wars-75455-boba-fett-set-for-adults/p/259087"
    assert name_from_link(url) == "Lego Star Wars 75455 Boba Fett Set for Adults"
    assert name_from_link("https://www.smythstoys.com/") == ""
    assert name_from_link("https://www.target.com/p/coffee-maker/-/A-52580270") == "Coffee Maker"
    assert name_from_link("https://www.target.com/p/-/A-52580270") == ""
    assert name_from_link("https://www.bestbuy.com/site/-/6645345.p?skuId=6645345") == ""
    assert name_from_link("https://www.bestbuy.com/product/hp-omnibook/JJG/sku/1") == "Hp Omnibook"
    assert name_from_link("https://www.walmart.com/ip/Pokemon-ETB/19402160990") == "Pokemon ETB"
    assert name_from_link("https://www.walmart.com/ip/19402160990") == ""
    mm = "https://www.mediamarkt.de/de/product/_apple-iphone-18-pro-max-3071615.html"
    assert name_from_link(mm) == "Apple Iphone 18 Pro Max"


def test_items_from_links_and_ids():
    config = parse_config(
        {
            "watch": [
                "https://www.bestbuy.com/site/-/6645345.p?skuId=6645345",
                {"retailer": "target", "id": 52580270, "max_price": 25, "channels": "pickup"},
                {"retailer": "mediamarkt", "id": "3003326"},
                {
                    "url": "https://www.saturn.de/de/product/_x-3071615.html",
                    "sold_by_retailer_only": False,
                },
            ],
            "mediamarkt": {"country": "at"},
        }
    )
    bestbuy, target, mm, saturn = config.items
    assert bestbuy.key == "bestbuy:6645345"
    assert bestbuy.channels == CHANNELS and bestbuy.max_price is None and bestbuy.retailer_only
    assert target.key == "target:52580270"
    assert target.max_price == 25.0 and target.channels == ("pickup",)
    assert mm.key == "mediamarkt-at:3003326" and mm.retailer_name == "MediaMarkt"
    assert saturn.key == "mediamarkt-saturn:3071615" and saturn.retailer_name == "Saturn"
    assert saturn.retailer_only is False
    assert config.find("6645345") == [bestbuy]
    assert config.find("TARGET:52580270") == [target]


def test_rules_default_flows_into_items():
    config = parse_config(
        {
            "watch": ["https://www.walmart.com/ip/19402160990"],
            "rules": {"sold_by_retailer_only": False, "confirm_sold_out": 3},
        }
    )
    assert config.items[0].retailer_only is False
    assert config.rules.confirm_sold_out == 3


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({}, "watch: list at least one"),
        ({"watch": [{"name": "x"}]}, "give the product's url"),
        ({"watch": [{"retailer": "target"}]}, "expected the product number"),
        ({"watch": [{"retailer": "amazon", "id": 1}]}, "expected one of"),
        (
            {
                "watch": [
                    "https://www.walmart.com/ip/19402160990",
                    {"retailer": "walmart", "id": 19402160990},
                ]
            },
            "listed twice",
        ),
        (
            {"watch": [{"url": "https://www.walmart.com/ip/1", "channels": ["store"]}]},
            "expected one of ship, pickup",
        ),
        ({"watch": [{"url": "https://www.walmart.com/ip/1", "max_price": 0}]}, "more than 0"),
        ({"watch": [{"url": "https://www.walmart.com/ip/1", "max_price": "60"}]}, "a number"),
        ({"watch": [{"url": "https://www.walmart.com/ip/1", "country": "de"}]}, "MediaMarkt"),
        (
            {"watch": [{"retailer": "mediamarkt", "id": 1, "banner": "saturn", "country": "at"}]},
            "only supported for Germany",
        ),
        ({"watch": ["https://www.walmart.com/ip/1"], "target": {"zip": "1234"}}, "5-digit"),
        ({"watch": ["https://www.walmart.com/ip/1"], "rules": {"confirm_sold_out": 0}}, "1 to 20"),
        (
            {"watch": ["https://www.walmart.com/ip/1"], "delivery": {"destinations": ["sms"]}},
            "one of",
        ),
        (
            {"watch": ["https://www.walmart.com/ip/1"], "delivery": {"pushover_priority": 2}},
            "-1 to 1",
        ),
        ({"watch": ["https://www.walmart.com/ip/1"], "alerts": {}}, "unknown setting"),
    ],
)
def test_config_errors(raw, message):
    with pytest.raises(ConfigError, match=message):
        parse_config(raw)


def test_shipped_config_loads():
    config = load_config(Path(__file__).resolve().parent.parent / "config.yml")
    assert len(config.items) == 5
    assert {i.retailer for i in config.items} == {
        "target",
        "bestbuy",
        "walmart",
        "smyths",
        "mediamarkt",
    }
    assert all(i.max_price for i in config.items)
    assert config.state.name == "stock.json"


def test_missing_config(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.yml")
