"""What the watcher remembers between runs, kept in one JSON file.

For every watched product, by key (``bestbuy:6645345``):

- ``since``: the first check that got an answer.
- ``in_stock``: a restock was alerted and has not sold out since. While it is true,
  nothing more is sent for this product.
- ``out_reads`` / ``pending_out``: sold-out reads in a row after an alert, and when the
  first of them came. A restock only ends after ``rules.confirm_sold_out`` of them, so one
  odd read does not cause a second alert for the same restock.
- ``out_since``: when the current sold-out spell began (as far as the checks saw).
- ``restocks``: the last restocks, when each began and sold out again, and the price.

The file only changes when stock changes, so in GitHub Actions the workflow commits it
back (``state/stock.json``) without flooding the history. On your own machine it stays
next to ``config.yml``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any

VERSION = 1
KEEP_RESTOCKS = 20


class StateError(RuntimeError):
    """The state file exists but cannot be read."""


def stamp(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_stamp(text: str) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass(frozen=True)
class Restock:
    at: str  # the check that first saw it in stock (and alerted)
    until: str = ""  # the first of the sold-out reads that ended it; "" while open
    price: float | None = None
    first: bool = False  # already in stock at the first check: not a restock as such

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"at": self.at}
        if self.until:
            data["until"] = self.until
        if self.price is not None:
            data["price"] = self.price
        if self.first:
            data["first"] = True
        return data

    @classmethod
    def from_dict(cls, raw: Any) -> Restock:
        price = raw.get("price")
        return cls(
            at=str(raw["at"]),
            until=str(raw.get("until") or ""),
            price=float(price) if isinstance(price, int | float) else None,
            first=raw.get("first") is True,
        )


@dataclass(frozen=True)
class Watch:
    since: str
    in_stock: bool = False
    out_since: str = ""
    out_reads: int = 0
    pending_out: str = ""
    restocks: tuple[Restock, ...] = ()

    def seen_in_stock(self) -> bool:
        return bool(self.restocks)

    def with_restock(self, restock: Restock) -> Watch:
        return replace(self, restocks=(*self.restocks, restock)[-KEEP_RESTOCKS:])

    def close_last(self, until: str) -> Watch:
        if not self.restocks:
            return self
        last = replace(self.restocks[-1], until=until)
        return replace(self, restocks=(*self.restocks[:-1], last))

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"since": self.since, "in_stock": self.in_stock}
        if self.out_since:
            data["out_since"] = self.out_since
        if self.out_reads:
            data["out_reads"] = self.out_reads
        if self.pending_out:
            data["pending_out"] = self.pending_out
        if self.restocks:
            data["restocks"] = [r.to_dict() for r in self.restocks]
        return data

    @classmethod
    def from_dict(cls, raw: Any) -> Watch:
        return cls(
            since=str(raw["since"]),
            in_stock=raw.get("in_stock") is True,
            out_since=str(raw.get("out_since") or ""),
            out_reads=int(raw.get("out_reads") or 0),
            pending_out=str(raw.get("pending_out") or ""),
            restocks=tuple(Restock.from_dict(r) for r in raw.get("restocks") or []),
        )


@dataclass
class State:
    items: dict[str, Watch] = field(default_factory=dict)

    def get(self, key: str) -> Watch | None:
        return self.items.get(key)

    def put(self, key: str, watch: Watch | None) -> None:
        if watch is None:
            self.items.pop(key, None)
        else:
            self.items[key] = watch

    def keep_only(self, keys: set[str]) -> list[str]:
        """Forget products no longer in config.yml. Returns the keys dropped."""
        gone = sorted(k for k in self.items if k not in keys)
        for key in gone:
            del self.items[key]
        return gone

    @classmethod
    def load(cls, path: str | Path) -> State:
        path = Path(path)
        if not path.is_file():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StateError(f"cannot read {path}: {exc}") from None
        if not isinstance(raw, dict) or not isinstance(raw.get("items", {}), dict):
            raise StateError(f"{path}: not a restock-alerts state file")
        try:
            items = {str(k): Watch.from_dict(v) for k, v in (raw.get("items") or {}).items()}
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise StateError(f"{path}: {exc!r}") from None
        return cls(items=items)

    def to_json(self) -> str:
        data = {
            "version": VERSION,
            "items": {k: self.items[k].to_dict() for k in sorted(self.items)},
        }
        return json.dumps(data, ensure_ascii=False, indent=1) + "\n"

    def save(self, path: str | Path) -> bool:
        """Write the file when its content changed. Returns True when it was written."""
        path = Path(path)
        text = self.to_json()
        if path.is_file() and path.read_text(encoding="utf-8") == text:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
        return True
