"""Shared fixtures: retailer answers shaped like the real API's, a fake SDK client and a
fake HTTP client for the sinks. Nothing here touches the network."""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from typing import Any

import pytest

NOW = datetime(2026, 11, 26, 14, 5, tzinfo=UTC)

TARGET_IN = {
    "tcin": "52580270",
    "title": "Hamilton Beach 12 Cup Programmable Coffee Maker Black 46290",
    "url": "https://www.target.com/p/hamilton-beach-12-cup-programmable-coffee-maker/-/A-52580270",
    "price": 18.99,
    "originalPrice": 27.99,
    "priceType": "sale",
    "currency": "USD",
    "image": "https://target.scene7.com/is/image/Target/GUEST_1",
    "fulfillment": {
        "tcin": "52580270",
        "soldOut": False,
        "outOfStockInAllStores": False,
        "shippingStatus": "IN_STOCK",
        "shippingMinDate": "2026-11-30",
        "shippingMaxDate": "2026-11-30",
        "freeShipping": False,
        "store": {
            "storeId": "1375",
            "storeName": "Mpls Nicollet Mall",
            "pickupStatus": "IN_STOCK",
            "pickupDate": "2026-11-27",
            "inStoreStatus": "IN_STOCK",
            "quantity": 10,
            "aisle": "L 53",
        },
    },
}

TARGET_OUT = {
    "tcin": "1014840165",
    "title": "Pokémon TCG: Mega Evolution – Perfect Order Elite Trainer Box",
    "url": "https://www.target.com/p/pok-mon-tcg-perfect-order-elite-trainer-box/-/A-1014840165",
    "price": 49.99,
    "currency": "USD",
    "fulfillment": {
        "tcin": "1014840165",
        "soldOut": True,
        "outOfStockInAllStores": True,
        "shippingStatus": "OUT_OF_STOCK",
        "store": {
            "storeId": "1375",
            "storeName": "Mpls Nicollet Mall",
            "pickupStatus": "UNAVAILABLE",
            "inStoreStatus": "NOT_SOLD_IN_STORE",
            "quantity": 0,
        },
    },
}

BESTBUY_OUT = {
    "sku": "6645345",
    "title": "Pokémon - Trading Card Game: Mega Evolution - Phantasmal Flames Elite Trainer Box",
    "url": "https://www.bestbuy.com/product/pokemon-phantasmal-flames-elite-trainer-box/JJG2TL2VT6/sku/6645345",
    "price": 49.99,
    "originalPrice": None,
    "currency": "USD",
    "available": None,
    "availableForShipping": None,
    "availableForPickup": None,
    "availableForDelivery": None,
    "seller": {"id": "bby", "type": "Best Buy"},
    "image": "https://pisces.bbystatic.com/image2/BestBuy_US/images/products/etb.png",
    "releaseDate": "2025-11-14T06:00Z",
}

BESTBUY_MARKETPLACE = {
    "sku": "13138238",
    "title": "Pokémon - Trading Card Game: 30th Celebration Elite Trainer Box",
    "url": "https://www.bestbuy.com/product/pokemon-30th-celebration-elite-trainer-box/JJG/sku/13138238",
    "price": 140.49,
    "currency": "USD",
    "available": True,
    "availableForShipping": True,
    "availableForPickup": False,
    "availableForDelivery": False,
    "seller": {"id": "7791", "type": "Marketplace"},
}

WALMART_MARKETPLACE = {
    "product": {
        "itemId": "19402160990",
        "title": "Pokemon Trading Card Game Mega Evolution Perfect Order Elite Trainer Box",
        "url": "https://www.walmart.com/ip/Pokemon-TCG-Perfect-Order-Elite-Trainer-Box/19402160990",
        "price": 72.99,
        "currency": "USD",
        "availability": "IN_STOCK",
        "inStock": True,
        "seller": {"id": "AB12", "name": "PrimeSellerHQ LLC", "type": "marketplace"},
        "fulfillment": [
            {
                "type": "shipping",
                "available": True,
                "status": "IN_STOCK",
                "message": "Free shipping, arrives by Tue, Dec 1 to Sacramento, 95829",
            }
        ],
        "images": [{"url": "https://i5.walmartimages.com/seo/etb.jpeg"}],
    }
}

WALMART_IN = {
    "product": {
        "itemId": "18638703162",
        "title": "HP 15.6 inch HD Windows Laptop Intel Processor N250 4GB 128GB",
        "url": "https://www.walmart.com/ip/HP-15-6-inch-HD-Windows-Laptop/18638703162",
        "price": 229.0,
        "wasPrice": 279.0,
        "currency": "USD",
        "availability": "IN_STOCK",
        "inStock": True,
        "seller": {"id": "F55C", "name": "Walmart.com", "type": "walmart"},
        "fulfillment": [
            {
                "type": "shipping",
                "available": True,
                "message": "Free shipping, arrives by Mon, Nov 30 to Sacramento, 95829",
            },
            {
                "type": "pickup",
                "available": False,
                "message": "Pickup not available",
            },
        ],
    }
}

SMYTHS_IN = {
    "code": "259087",
    "hdSection": {
        "stockStatus": "INSTOCK",
        "stockMessage": "In Stock",
        "guestDeliveryMessage": "FREE Standard Delivery",
        "isAvailableForDelivery": True,
        "isPreOrder": False,
    },
    "ccSection": {
        "stockStatus": "INSTOCK",
        "stockMessage": "In Stock",
        "deliveryMessage": "FREE collection within 2 hours",
        "storeName": None,
        "isPreOrder": False,
        "storeClosed": False,
    },
    "prices": {"price": "£119.99", "value": "119.99", "wasPrice": None, "rrpPrice": "£149.99"},
    "paymentMessage": {"currency": "GBP", "country": "GB"},
}

SMYTHS_OUT = {
    "code": "263924",
    "hdSection": {
        "stockStatus": "OUTOFSTOCK",
        "stockMessage": "Out of stock",
        "isPreOrder": False,
        "expectedStockDate": None,
    },
    "ccSection": {
        "stockStatus": "OUTOFSTOCK",
        "stockMessage": "Out of stock",
        "storeName": "Northampton",
        "isPreOrder": False,
    },
    "prices": {"price": "£49.99", "value": "49.99"},
    "paymentMessage": {"currency": "GBP", "country": "GB"},
}

MEDIAMARKT_OUT = {
    "productId": "3003326",
    "title": "POKEMON 12913 ME01 Mega-Entwicklung Top Trainer Box Lucario Sammelkartenspiel",
    "url": "https://www.mediamarkt.de/de/product/_pokemon-12913-me01-top-trainer-box-lucario-3003326.html",
    "price": 59.99,
    "strikePrice": None,
    "currency": "EUR",
    "marketplace": False,
    "seller": None,
    "availability": {
        "online": "TEMPORARILY_NOT_AVAILABLE",
        "buyable": False,
        "deliverable": False,
        "storePickup": False,
        "pickupStatus": "NOT_AVAILABLE",
    },
}

MEDIAMARKT_IN = {
    "productId": "3071615",
    "title": "APPLE iPhone 18 Pro Max 256 GB Silber Dual SIM",
    "url": "https://www.mediamarkt.de/de/product/_apple-iphone-18-pro-max-256gb-silver-3071615.html",
    "price": 1599,
    "currency": "EUR",
    "marketplace": False,
    "seller": None,
    "image": "https://assets.mmsrg.com/isr/166325/c1/-/iphone.png",
    "availability": {
        "online": "AVAILABLE",
        "buyable": True,
        "deliverable": True,
        "storePickup": True,
        "pickupStatus": "PARTIALLY_AVAILABLE",
        "releaseDate": "2026-09-18",
        "delivery": {
            "status": "AVAILABLE",
            "shippingCost": 0,
            "earliest": "2026-11-27T07:00:00.000Z",
            "latest": "2026-11-28T17:00:00.000Z",
        },
    },
}


class FakeClient:
    """Stands in for ``scrapeunblocker.Client``: answers from a dict keyed by product id.

    A value can be an answer (dict), an exception to raise, or a list of those to hand
    out one per call."""

    def __init__(self, answers: dict[str, Any]) -> None:
        # Lists are copied so popping answers never changes the shared fixtures.
        self.answers = {k: list(v) if isinstance(v, list) else v for k, v in answers.items()}
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def _answer(self, method: str, product_id: str, kwargs: dict[str, Any]) -> Any:
        self.calls.append((method, product_id, kwargs))
        value = self.answers[product_id]
        if isinstance(value, list):
            value = value.pop(0) if len(value) > 1 else value[0]
        if isinstance(value, BaseException):
            raise value
        return copy.deepcopy(value)

    def target_product(self, tcin: str | None = None, **kwargs: Any) -> Any:
        return self._answer("target_product", str(tcin), kwargs)

    def bestbuy_product(self, sku: str | None = None, **kwargs: Any) -> Any:
        return self._answer("bestbuy_product", str(sku), kwargs)

    def walmart_product(self, **kwargs: Any) -> Any:
        item_id = kwargs.pop("item_id")
        return self._answer("walmart_product", str(item_id), kwargs)

    def smyths_inventory(self, code: str, **kwargs: Any) -> Any:
        return self._answer("smyths_inventory", str(code), kwargs)

    def mediamarkt_product(self, product_id: str | None = None, **kwargs: Any) -> Any:
        return self._answer("mediamarkt_product", str(product_id), kwargs)


class FakeResponse:
    def __init__(self, status_code: int = 200, text: str = "ok", headers: Any = None) -> None:
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}


class FakeHttp:
    """Records every POST. ``statuses`` hands out status codes per URL substring."""

    def __init__(self, statuses: dict[str, list[int]] | None = None) -> None:
        self.posts: list[dict[str, Any]] = []
        self.statuses = statuses or {}

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.posts.append({"url": url, **kwargs})
        for part, codes in self.statuses.items():
            if part in url and codes:
                return FakeResponse(codes.pop(0) if len(codes) > 1 else codes[0], "nope")
        return FakeResponse()


@pytest.fixture
def fake_http() -> FakeHttp:
    return FakeHttp()
