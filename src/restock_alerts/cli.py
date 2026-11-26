"""Command line: ``restock-alerts run | watch | check | status | test-destinations``."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, TextIO

from scrapeunblocker import Client

from . import __version__, messages
from .collect import FATAL, FetchError, Reading, ShopClient, read
from .config import CHANNELS, Config, ConfigError, Item, load_config, name_from_link, parse_link
from .messages import Message
from .process import CHANNEL_LABEL, Event, Verdict, judge, money, step
from .sinks import REGISTRY, DeliveryError, Sink, build_sinks, new_http_client
from .state import State, StateError, Watch, parse_stamp

API_KEY_ENV = "SCRAPEUNBLOCKER_KEY"
SIGNUP_URL = (
    "https://scrapeunblocker.com/?utm_source=github&utm_medium=integration"
    "&utm_campaign=example-repos"
)
PARALLEL = 4  # products read at the same time


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="restock-alerts",
        description=(
            "Restock alerts for sold-out products at Target, Best Buy, Walmart, Smyths Toys "
            "and MediaMarkt / Saturn, delivered once to Telegram, Discord, Slack, Pushover or "
            "a webhook. Stock comes from the ScrapeUnblocker retailer APIs."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "-c", "--config", default="config.yml", help="config file (default: config.yml)"
    )
    common.add_argument(
        "--env-file",
        default=".env",
        help="read environment variables from this file when it exists (default: .env)",
    )
    common.add_argument("-v", "--verbose", action="store_true", help="log every API call")

    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    run = sub.add_parser(
        "run", parents=[common], help="check every product once and deliver restocks"
    )
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="print exactly what would be delivered; deliver nothing and save nothing",
    )
    run.add_argument(
        "--item",
        action="append",
        metavar="NAME",
        help="only this product (its name, link or id in config.yml); repeat for more",
    )
    watch = sub.add_parser(
        "watch", parents=[common], help="run again and again on your own machine (Ctrl+C stops)"
    )
    watch.add_argument(
        "--every",
        type=float,
        default=10.0,
        metavar="MINUTES",
        help="minutes between checks (default: 10, at least 1)",
    )
    watch.add_argument("--dry-run", action="store_true", help="as for run: send and save nothing")
    check = sub.add_parser(
        "check",
        parents=[common],
        help="read one product now and show why it would or would not alert (1 API call)",
    )
    check.add_argument("product", help="a product link, or a name / id from config.yml")
    sub.add_parser(
        "status", parents=[common], help="each product's stock and restock history (no API)"
    )
    sub.add_parser(
        "test-destinations",
        parents=[common],
        help="send a test message to each destination (no API calls)",
    )
    return parser


def load_env_file(path: str, environ: MutableMapping[str, str]) -> None:
    """Minimal ``.env`` reader: ``KEY=value`` lines; real environment variables win."""
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.removeprefix("export ").strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in environ:
                environ[key] = value


def annotation(level: str, title: str, message: str) -> str:
    """A GitHub Actions workflow command (``::warning title=...::...``), safely escaped."""

    def data(text: str) -> str:
        return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")

    prop = data(title).replace(":", "%3A").replace(",", "%2C")
    return f"::{level} title={prop}::{data(message)}"


def short(label: str, width: int) -> str:
    return label if len(label) <= width else label[: width - 1] + "…"


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def print_preview(message: Message, sinks: Sequence[Sink], out: TextIO) -> None:
    if not sinks:
        print(f"\n{message.render(messages.PLAIN)}", file=out)
        return
    for sink in sinks:
        print(f"\n--- {sink.name} ---\n{sink.preview(message)}", file=out)


# -- reading ------------------------------------------------------------------------------


@dataclass
class Outcome:
    """One product in one run."""

    item: Item
    reading: Reading | None = None
    raw: Any = None
    error: FetchError | None = None
    calls: int = 0
    verdict: Verdict | None = None
    after: Watch | None = None
    event: str = ""


def read_all(
    client: ShopClient,
    config: Config,
    items: Sequence[Item],
    now: datetime,
    sleep: Callable[[float], None] | None = None,
) -> list[Outcome]:
    """Read every product, ``PARALLEL`` at a time. FATAL errors pass through."""

    def one(item: Item) -> Outcome:
        try:
            reading, raw, calls = read(client, item, config.target, today=now.date(), sleep=sleep)
        except FetchError as exc:
            return Outcome(item, error=exc, calls=exc.calls)
        return Outcome(item, reading=reading, raw=raw, calls=calls)

    if len(items) <= 1:
        return [one(item) for item in items]
    with ThreadPoolExecutor(max_workers=min(PARALLEL, len(items))) as pool:
        return list(pool.map(one, items))


def describe(outcome: Outcome) -> str:
    """The run log's verdict for one product."""
    if outcome.error is not None:
        return f"failed: {outcome.error}"
    reading = outcome.reading
    assert reading is not None
    if outcome.verdict is None:
        return "stock not in the answer; checked again next run"
    if outcome.event == "restock":
        return "IN STOCK -> back-in-stock alert"
    if outcome.event == "in_stock":
        return "IN STOCK -> in-stock alert (first check)"
    if outcome.event == "sold_out":
        return "sold out again -> sold-out alert"
    if outcome.verdict.match:
        open_ = ", ".join(CHANNEL_LABEL[c] for c in outcome.verdict.channels)
        return f"in stock ({open_}), already alerted"
    if outcome.after is not None and outcome.after.in_stock:
        return f"{outcome.verdict.reason} (read {outcome.after.out_reads}, waiting to confirm)"
    return outcome.verdict.reason


# -- run ----------------------------------------------------------------------------------


def run_checks(
    config: Config,
    client: ShopClient,
    sinks: Sequence[Sink],
    *,
    dry_run: bool,
    now: datetime,
    only: Sequence[Item] | None = None,
    out: TextIO = sys.stdout,
    err: TextIO = sys.stderr,
    annotate: bool = False,
    summary_path: str | None = None,
    sleep: Callable[[float], None] | None = None,
) -> int:
    """One scheduled run: read, judge, deliver, save. Returns the exit code."""
    state = State.load(config.state)
    items = list(only or config.items)
    print(f"Checking {plural(len(items), 'product')} (1 API call each):", file=out)
    outcomes = read_all(client, config, items, now, sleep)

    events: list[tuple[Event, Outcome]] = []
    for o in outcomes:
        before = state.get(o.item.key)
        if o.reading is not None:
            o.verdict = judge(o.reading, o.item, config.rules)
            o.after, o.event = step(before, o.verdict, o.reading, now, config.rules)
            if o.event:
                assert o.after is not None and o.verdict is not None
                events.append((Event(o.event, o.item, o.reading, o.verdict, before, o.after), o))
        title = o.item.label(o.reading.title if o.reading else "")
        retailer = o.reading.retailer if o.reading else o.item.retailer_name
        print(f"  {retailer:<11}  {short(title, 44):<44}  {describe(o)}", file=out)
        if o.error is not None and annotate:
            print(annotation("warning", f"Can't read {title}", str(o.error)), file=out)
    print(f"API calls: {sum(o.calls for o in outcomes)}", file=out)

    errors = [o.error for o in outcomes if o.error is not None]
    exit_code = 0
    if errors and (len(errors) == len(outcomes) or any(e.permanent for e in errors)):
        exit_code = 1

    pending = [(messages.event_message(event, now), o) for event, o in events]
    if summary_path:
        _write_summary(summary_path, outcomes)

    print(file=out)
    delivered: set[str] = set()
    if not pending:
        print("Nothing new to deliver.", file=out)
    elif dry_run:
        print(f"Dry run: would deliver {plural(len(pending), 'message')}.", file=out)
        if not sinks:
            print("No destination secrets are set; the message(s) would be:", file=out)
        for message, _ in pending:
            print_preview(message, sinks, out)
    else:
        failed_sinks: set[str] = set()
        for message, o in pending:
            ok = False
            for sink in sinks:
                try:
                    sink.send(message)
                except DeliveryError as exc:
                    print(f"Delivery failed: {exc}", file=err)
                    failed_sinks.add(sink.name)
                else:
                    ok = True
            if ok:
                delivered.add(o.item.key)
            else:
                print(f"Not delivered: {message.subject}. It is tried again next run.", file=out)
        where = ", ".join(s.name for s in sinks if s.name not in failed_sinks) or "nowhere"
        print(
            f"Delivered {len(delivered)} of {plural(len(pending), 'message')} to {where}.", file=out
        )
        if failed_sinks:
            exit_code = 1
            if annotate:
                names = ", ".join(sorted(failed_sinks))
                print(
                    annotation("warning", "Delivery failed", f"Not accepted by {names}"), file=out
                )

    if dry_run:
        print("Dry run: nothing was sent or saved.", file=out)
        return exit_code
    for o in outcomes:
        if o.reading is None or o.verdict is None:
            continue
        if o.event and o.item.key not in delivered:
            continue  # not delivered: keep the old state so it is tried again
        state.put(o.item.key, o.after)
    if only is None:
        for key in state.keep_only({item.key for item in config.items}):
            print(f"Forgot {key} (no longer in config.yml).", file=out)
    if state.save(config.state):
        print(f"Saved {config.state.name}.", file=out)
    return exit_code


def _write_summary(path: str, outcomes: Sequence[Outcome]) -> None:
    lines = [
        "### Restock check",
        "",
        "| Product | Retailer | Price | Result |",
        "|---|---|---|---|",
    ]
    for o in outcomes:
        title = o.item.label(o.reading.title if o.reading else "").replace("|", "/")
        retailer = o.reading.retailer if o.reading else o.item.retailer_name
        price = ""
        if o.reading is not None and o.reading.price is not None:
            price = money(o.reading.price, o.reading.currency)
        result = describe(o).replace("|", "/")[:140]
        lines.append(f"| {short(title, 70)} | {retailer} | {price} | {result} |")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


# -- other commands -----------------------------------------------------------------------


def _item_for(config: Config, text: str) -> Item:
    """An item of config.yml, or a new one (default rules) from a product link."""
    found = config.find(text)
    if found:
        return found[0]
    if not text.strip().lower().startswith(("http://", "https://")):
        raise ConfigError(f"{text!r} is not a product link or a name / id in config.yml")
    link = parse_link(text)
    for item in config.items:
        if (item.retailer, item.product_id) == (link["retailer"], link["product_id"]):
            return item
    return Item(
        retailer=link["retailer"],
        product_id=link["product_id"],
        url=text.strip(),
        retailer_only=config.rules.sold_by_retailer_only,
        country=link.get("country")
        or (config.mediamarkt_country if link["retailer"] == "mediamarkt" else ""),
        banner=link.get("banner") or ("mediamarkt" if link["retailer"] == "mediamarkt" else ""),
    )


def check_product(
    config: Config, client: ShopClient, text: str, *, now: datetime, out: TextIO
) -> int:
    item = _item_for(config, text)
    try:
        reading, _, _ = read(client, item, config.target, today=now.date())
    except FetchError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    verdict = judge(reading, item, config.rules)
    in_config = any(i.key == item.key for i in config.items)
    print(f"{reading.retailer} {item.product_id}  {item.label(reading.title)}", file=out)
    print(f"  {'link':<9} {reading.url}", file=out)
    price = money(reading.price, reading.currency) if reading.price is not None else "not shown"
    if reading.price is not None and reading.was_price and reading.was_price > reading.price:
        price += f" (was {money(reading.was_price, reading.currency)})"
    print(f"  {'price':<9} {price}", file=out)
    for name in CHANNELS:
        channel = reading.channel(name)
        if channel is None:
            text_ = "not reported by this retailer"
        elif channel.available:
            text_ = f"in stock{': ' + channel.note if channel.note else ''}"
        else:
            text_ = "sold out / unavailable"
        wanted = "" if name in item.channels else "  (not watched: channels)"
        print(f"  {name:<9} {text_}{wanted}", file=out)
    if reading.preorder:
        print(
            f"  {'pre-order':<9} yes{', release ' + reading.release if reading.release else ''}",
            file=out,
        )
    seller = reading.seller or "not reported"
    if reading.by_retailer is False:
        seller += " (marketplace)"
    print(f"  {'seller':<9} {seller}", file=out)
    print(f"  {'raw':<9} {reading.status}", file=out)
    if verdict is None:
        print("\nVerdict: the answer had no stock fields; a run would change nothing.", file=out)
    elif verdict.match:
        open_ = " and ".join(CHANNEL_LABEL[c] for c in verdict.channels)
        print(f"\nVerdict: counts as IN STOCK ({open_}).", file=out)
    else:
        print(f"\nVerdict: does not count: {verdict.reason}.", file=out)
    if not in_config:
        print("Not in config.yml yet: add it under watch: to get alerts.", file=out)
    print("API calls: 1. Nothing was saved.", file=out)
    return 0


def show_status(config: Config, out: TextIO, now: datetime) -> int:
    state = State.load(config.state)
    print(f"State: {config.state}", file=out)
    cutoff = now - timedelta(days=30)
    for item in config.items:
        watch = state.get(item.key)
        label = short(item.label(name_from_link(item.url)), 44)
        if watch is None:
            print(f"  {item.retailer_name:<11}  {label:<44}  not checked yet", file=out)
            continue
        if watch.in_stock:
            last = parse_stamp(watch.restocks[-1].at) if watch.restocks else None
            text = f"in stock since {last:%d %b %H:%M} UTC (alerted)" if last else "in stock"
            if watch.out_reads:
                text += f", {watch.out_reads} sold-out read(s) since"
        else:
            began = parse_stamp(watch.out_since)
            # "Not in stock" covers sold out and in stock only above your cap or from resellers.
            text = f"not in stock since {began:%d %b %H:%M} UTC" if began else "not in stock"
        recent = [r for r in watch.restocks if (at := parse_stamp(r.at)) and at >= cutoff]
        restocks = [r for r in recent if not r.first]
        if restocks:
            text += f"; {plural(len(restocks), 'restock')} in 30 days"
        print(f"  {item.retailer_name:<11}  {label:<44}  {text}", file=out)
    return 0


def test_destinations(sinks: Sequence[Sink], skipped: Sequence[str], out: TextIO) -> int:
    for name in skipped:
        need = " and ".join(REGISTRY[name].env)
        print(f"  {name:<9} skipped ({need} not set)", file=out)
    failed = 0
    for sink in sinks:
        try:
            result = sink.test()
        except DeliveryError as exc:
            failed += 1
            print(f"  {sink.name:<9} FAILED {exc}", file=out)
        else:
            print(f"  {sink.name:<9} ok, {result}", file=out)
    return 1 if failed else 0


def _make_client(environ: Mapping[str, str]) -> Client:
    # One retry is done here (collect.read); the SDK's own retries would bill again.
    return Client(api_key=environ[API_KEY_ENV].strip(), timeout=150.0, max_retries=0)


def _pick(config: Config, names: Sequence[str]) -> list[Item]:
    picked: list[Item] = []
    for text in names:
        matches = config.find(text)
        if not matches:
            raise ConfigError(f"{text!r} is not a product name, link or id in config.yml")
        picked += [i for i in matches if i not in picked]
    return picked


def _wanted(config: Config) -> str:
    return "; ".join(" + ".join(REGISTRY[n].env) for n in config.delivery.destinations)


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: MutableMapping[str, str] | None = None,
    client_factory: Callable[[Mapping[str, str]], ShopClient] = _make_client,
    http_factory: Callable[[], Any] = new_http_client,
    now: datetime | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    args = build_parser().parse_args(argv)
    environ = os.environ if environ is None else environ
    out = out or sys.stdout
    err = err or sys.stderr
    for stream in (out, err):
        if hasattr(stream, "reconfigure"):
            # Product names can hold any character; never crash on a narrow console.
            stream.reconfigure(errors="replace")
    fixed_now = now
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    # httpx logs full request URLs at INFO, and webhook URLs and bot tokens are secrets.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    def clock() -> datetime:
        return fixed_now or datetime.now(UTC).replace(microsecond=0)

    load_env_file(args.env_file, environ)
    has_key = bool((environ.get(API_KEY_ENV) or "").strip())
    annotate = environ.get("GITHUB_ACTIONS") == "true"
    try:
        config = load_config(args.config)
        if args.command == "status":
            return show_status(config, out, clock())
        if args.command in ("run", "watch", "check") and not has_key:
            print(
                f"error: set {API_KEY_ENV} to your ScrapeUnblocker API key "
                f"(get one at {SIGNUP_URL})",
                file=err,
            )
            return 2
        if args.command == "check":
            return check_product(
                config, client_factory(environ), args.product, now=clock(), out=out
            )

        http = http_factory()
        try:
            sinks, skipped = build_sinks(
                config.delivery.destinations, environ, http, config.delivery.pushover_priority
            )
            if args.command == "test-destinations":
                if not sinks:
                    print(
                        f"error: no destination is set up. Set one of: {_wanted(config)}", file=err
                    )
                    return 2
                return test_destinations(sinks, skipped, out)
            if not sinks and not args.dry_run:
                # Nothing could be delivered, so spend nothing: restocks stay unsent.
                text = (
                    f"No destination is set up, so nothing was checked. Set one of: "
                    f"{_wanted(config)}"
                )
                print(annotation("notice", "No destination", text) if annotate else text, file=out)
                return 0
            for name in skipped:
                need = " and ".join(REGISTRY[name].env)
                print(f"({name} skipped: {need} not set)", file=out)
            client = client_factory(environ)
            if args.command == "watch":
                return watch_loop(args, client, sinks, clock, out, err, sleep)
            only = _pick(config, args.item) if args.item else None
            return run_checks(
                config,
                client,
                sinks,
                dry_run=args.dry_run,
                now=clock(),
                only=only,
                out=out,
                err=err,
                annotate=annotate,
                summary_path=environ.get("GITHUB_STEP_SUMMARY") or None,
            )
        finally:
            close = getattr(http, "close", None)
            if callable(close):
                close()
    except (ConfigError, StateError, DeliveryError) as exc:
        print(f"error: {exc}", file=err)
        return 2
    except FATAL as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=err)
        return 2


def watch_loop(
    args: argparse.Namespace,
    client: ShopClient,
    sinks: Sequence[Sink],
    clock: Callable[[], datetime],
    out: TextIO,
    err: TextIO,
    sleep: Callable[[float], None],
    rounds: int | None = None,
) -> int:
    """Run, wait ``--every`` minutes, run again. config.yml is read again each time."""
    every = max(float(args.every), 1.0)
    done = 0
    try:
        while True:
            started = clock()
            print(f"\n== {started:%Y-%m-%d %H:%M:%S} UTC ==", file=out)
            try:
                config = load_config(args.config)
                run_checks(
                    config, client, sinks, dry_run=args.dry_run, now=started, out=out, err=err
                )
            except (ConfigError, StateError) as exc:
                print(f"error: {exc} (trying again next round)", file=err)
            done += 1
            if rounds is not None and done >= rounds:
                return 0
            print(f"Next check in {every:g} min. Ctrl+C stops.", file=out)
            sleep(every * 60)
    except KeyboardInterrupt:
        print("\nStopped.", file=out)
        return 0
