import io
import json
from datetime import timedelta

import pytest
import yaml
from conftest import (
    BESTBUY_OUT,
    NOW,
    SMYTHS_IN,
    TARGET_IN,
    WALMART_MARKETPLACE,
    FakeClient,
    FakeHttp,
)
from scrapeunblocker import AuthenticationError, ServerError

from restock_alerts import cli

WATCH = [
    {"url": "https://www.target.com/p/coffee-maker/-/A-52580270", "max_price": 25},
    {"url": "https://www.bestbuy.com/site/-/6645345.p?skuId=6645345", "max_price": 60},
    {"url": "https://www.walmart.com/ip/Pokemon-ETB/19402160990", "max_price": 100},
    {
        "url": "https://www.smythstoys.com/uk/en-gb/toys/lego-star-wars-75455-boba-fett-set-for-adults/p/259087"
    },
]
ANSWERS = {
    "52580270": TARGET_IN,
    "6645345": BESTBUY_OUT,
    "19402160990": WALMART_MARKETPLACE,
    "259087": SMYTHS_IN,
}
ENV = {"SCRAPEUNBLOCKER_KEY": "test-key", "DISCORD_WEBHOOK_URL": "https://discord.example/hook"}


@pytest.fixture
def setup(tmp_path):
    config = tmp_path / "config.yml"
    config.write_text(
        yaml.safe_dump({"watch": WATCH, "delivery": {"destinations": ["discord", "telegram"]}}),
        encoding="utf-8",
    )
    return tmp_path, config


def call(config, args, *, answers=ANSWERS, env=None, http=None, now=NOW, client=None):
    out, err = io.StringIO(), io.StringIO()
    client = client or FakeClient(answers)
    http = http or FakeHttp()
    code = cli.main(
        [*args, "-c", str(config), "--env-file", str(config.parent / "none.env")],
        environ=dict(ENV if env is None else env),
        client_factory=lambda environ: client,
        http_factory=lambda: http,
        now=now,
        out=out,
        err=err,
        sleep=lambda s: None,
    )
    return code, out.getvalue(), err.getvalue(), client, http


def test_parser():
    parser = cli.build_parser()
    args = parser.parse_args(["run", "--dry-run", "--item", "a", "--item", "b"])
    assert args.command == "run" and args.dry_run and args.item == ["a", "b"]
    assert parser.parse_args(["watch", "--every", "5"]).every == 5.0
    assert parser.parse_args(["check", "https://www.walmart.com/ip/1"]).product
    assert parser.parse_args(["status"]).config == "config.yml"
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_two_runs_deliver_once(setup):
    tmp, config = setup
    code, out, err, client, http = call(config, ["run"])
    assert code == 0, err
    assert len(client.calls) == 4
    titles = [p["json"]["embeds"][0]["title"] for p in http.posts]
    assert titles == [
        "🟢 In stock at Target: Hamilton Beach 12 Cup Programmable Coffee Maker Black 46290",
        "🟢 In stock at Smyths Toys: Lego Star Wars 75455 Boba Fett Set for Adults",
    ]
    assert "sold by PrimeSellerHQ LLC, not Walmart" in out
    assert "Delivered 2 of 2 messages to discord." in out
    assert "(telegram skipped: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID not set)" in out
    state = json.loads((tmp / "state" / "stock.json").read_text(encoding="utf-8"))
    assert set(state["items"]) == {
        "target:52580270",
        "bestbuy:6645345",
        "walmart:19402160990",
        "smyths:259087",
    }
    assert state["items"]["target:52580270"]["in_stock"] is True
    assert state["items"]["bestbuy:6645345"]["in_stock"] is False

    saved = (tmp / "state" / "stock.json").read_text(encoding="utf-8")
    code, out, _, _, http2 = call(config, ["run"], now=NOW + timedelta(minutes=15))
    assert code == 0 and http2.posts == []
    assert "Nothing new to deliver." in out
    assert "already alerted" in out
    assert (tmp / "state" / "stock.json").read_text(encoding="utf-8") == saved


def test_restock_after_sold_out(setup):
    tmp, config = setup
    call(config, ["run"])
    back = {**BESTBUY_OUT, "available": True, "availableForShipping": True}
    code, out, _, _, http = call(
        config, ["run"], answers={**ANSWERS, "6645345": back}, now=NOW + timedelta(hours=3)
    )
    assert code == 0
    [post] = http.posts
    embed = post["json"]["embeds"][0]
    assert embed["title"].startswith("🟢 Back in stock at Best Buy: Pokémon")
    assert "Sold out at every check since 26 Nov" in embed["description"]
    assert "IN STOCK -> back-in-stock alert" in out


def test_dry_run_sends_and_saves_nothing(setup):
    tmp, config = setup
    code, out, _, client, http = call(config, ["run", "--dry-run"])
    assert code == 0 and http.posts == [] and len(client.calls) == 4
    assert "Dry run: would deliver 2 messages." in out
    assert "--- discord ---" in out and "🟢 In stock at Target" in out
    assert "Dry run: nothing was sent or saved." in out
    assert not (tmp / "state" / "stock.json").exists()


def test_failed_delivery_is_tried_again(setup):
    tmp, config = setup
    http = FakeHttp({"discord.example": [500]})
    code, out, err, _, _ = call(config, ["run"], http=http)
    assert code == 1 and "Delivery failed: discord: HTTP 500" in err
    assert "It is tried again next run." in out
    state = json.loads((tmp / "state" / "stock.json").read_text(encoding="utf-8"))
    assert "target:52580270" not in state["items"]  # not delivered, so not remembered
    assert state["items"]["bestbuy:6645345"]["in_stock"] is False
    code, _, _, _, http2 = call(config, ["run"], now=NOW + timedelta(minutes=15))
    assert code == 0 and len(http2.posts) == 2


def test_failed_read_keeps_state_and_warns(setup):
    tmp, config = setup
    call(config, ["run"])
    answers = {**ANSWERS, "52580270": ServerError("busy", status_code=502)}
    code, out, _, client, http = call(
        config,
        ["run"],
        answers=answers,
        env={**ENV, "GITHUB_ACTIONS": "true"},
        now=NOW + timedelta(minutes=15),
    )
    assert code == 0 and http.posts == []
    assert "failed: ServerError" in out and "::warning title=Can't read" in out
    assert "API calls: 5" in out  # the failed product was tried twice
    state = json.loads((tmp / "state" / "stock.json").read_text(encoding="utf-8"))
    assert state["items"]["target:52580270"]["in_stock"] is True


def test_only_one_item(setup):
    _, config = setup
    code, out, _, client, _ = call(config, ["run", "--item", "6645345"])
    assert code == 0 and [c[1] for c in client.calls] == ["6645345"]
    code, _, err, _, _ = call(config, ["run", "--item", "nope"])
    assert code == 2 and "not a product name" in err


def test_no_key_and_no_destination(setup):
    _, config = setup
    code, _, err, client, _ = call(config, ["run"], env={})
    assert code == 2 and "SCRAPEUNBLOCKER_KEY" in err and client.calls == []
    code, out, _, client, _ = call(
        config, ["run"], env={"SCRAPEUNBLOCKER_KEY": "k", "GITHUB_ACTIONS": "true"}
    )
    assert code == 0 and client.calls == []
    assert "::notice title=No destination::" in out


def test_fatal_error_stops_the_run(setup):
    _, config = setup
    answers = {k: AuthenticationError("bad key", status_code=401) for k in ANSWERS}
    code, _, err, _, http = call(config, ["run"], answers=answers)
    assert code == 2 and "AuthenticationError" in err and http.posts == []


def test_check_command(setup):
    _, config = setup
    code, out, _, client, _ = call(config, ["check", "19402160990"])
    assert code == 0 and len(client.calls) == 1
    assert "seller    PrimeSellerHQ LLC (marketplace)" in out
    assert "Verdict: does not count: sold by PrimeSellerHQ LLC, not Walmart." in out
    code, out, _, _, _ = call(
        config,
        ["check", "https://www.walmart.com/ip/HP-Laptop/18638703162"],
        answers={"18638703162": WALMART_MARKETPLACE["product"] | {"seller": {"type": "walmart"}}},
    )
    assert code == 0 and "Verdict: counts as IN STOCK (shipping)." in out
    assert "Not in config.yml yet" in out


def test_status(setup):
    _, config = setup
    code, out, _, _, _ = call(config, ["status"])
    assert code == 0 and out.count("not checked yet") == 4
    call(config, ["run"])
    code, out, _, _, _ = call(config, ["status"], env={})
    assert "in stock since 26 Nov 14:05 UTC (alerted)" in out
    assert "not in stock since 26 Nov 14:05 UTC" in out
    assert "Coffee Maker" in out  # a name from the link when config.yml has none


def test_test_destinations(setup):
    _, config = setup
    code, out, _, client, http = call(config, ["test-destinations"])
    assert code == 0 and client.calls == [] and len(http.posts) == 1
    assert "discord   ok, test message sent" in out
    assert "telegram  skipped" in out


def test_watch_loop_runs_and_reloads(setup):
    _, config = setup
    out, err = io.StringIO(), io.StringIO()
    client = FakeClient(ANSWERS)
    args = cli.build_parser().parse_args(["watch", "-c", str(config), "--every", "0.5"])
    slept = []
    code = cli.watch_loop(args, client, [], lambda: NOW, out, err, slept.append, rounds=2)
    assert code == 0 and slept == [60.0] and len(client.calls) == 8
    assert out.getvalue().count("== 2026-11-26 14:05:00 UTC ==") == 2
