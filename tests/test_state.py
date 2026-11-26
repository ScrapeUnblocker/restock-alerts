import json

import pytest

from restock_alerts.state import KEEP_RESTOCKS, Restock, State, StateError, Watch


def test_round_trip_and_save_only_on_change(tmp_path):
    path = tmp_path / "state" / "stock.json"
    state = State()
    state.put(
        "bestbuy:6645345",
        Watch(
            since="2026-11-20T10:00:00Z",
            in_stock=True,
            out_reads=1,
            pending_out="2026-11-26T14:20:00Z",
            restocks=(Restock(at="2026-11-26T14:05:00Z", price=49.99),),
        ),
    )
    state.put("target:1", Watch(since="2026-11-20T10:00:00Z", out_since="2026-11-20T10:00:00Z"))
    assert state.save(path) is True
    assert state.save(path) is False  # same content: not written again
    loaded = State.load(path)
    assert loaded.items == state.items
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["version"] == 1 and list(raw["items"]) == ["bestbuy:6645345", "target:1"]
    assert raw["items"]["target:1"] == {
        "since": "2026-11-20T10:00:00Z",
        "in_stock": False,
        "out_since": "2026-11-20T10:00:00Z",
    }


def test_missing_file_is_empty(tmp_path):
    assert State.load(tmp_path / "nope.json").items == {}


@pytest.mark.parametrize("text", ["{not json", "[]", '{"items": {"x": {"in_stock": true}}}'])
def test_bad_file(tmp_path, text):
    path = tmp_path / "stock.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(StateError):
        State.load(path)


def test_restock_history_is_capped():
    watch = Watch(since="2026-01-01T00:00:00Z")
    for day in range(1, KEEP_RESTOCKS + 6):
        watch = watch.with_restock(Restock(at=f"2026-02-{day:02d}T00:00:00Z"))
    assert len(watch.restocks) == KEEP_RESTOCKS
    assert watch.restocks[0].at == "2026-02-06T00:00:00Z"
    closed = watch.close_last("2026-02-25T01:00:00Z")
    assert closed.restocks[-1].until == "2026-02-25T01:00:00Z"


def test_keep_only_and_put_none():
    state = State()
    state.put("a:1", Watch(since="x"))
    state.put("b:2", Watch(since="x"))
    assert state.keep_only({"a:1"}) == ["b:2"]
    state.put("a:1", None)
    assert state.items == {}
