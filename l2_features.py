#!/usr/bin/env python3
"""Causal L2 reconstruction and 15-second trade-flow feature extraction.

Input is raw JSONL from ``bybit_recorder.py``.  Events are processed in local
receive order: this is deliberately the information boundary available to a
live client.  A row represents a completed or partial 15-second receive-time
bucket and never reads a later book update to describe an earlier trade.
"""
from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator


INTERVAL_NS = 15_000_000_000


@dataclass
class Book:
    bids: dict[float, float] = field(default_factory=dict)
    asks: dict[float, float] = field(default_factory=dict)
    received_ns: int | None = None
    cts_ms: int | None = None

    def apply(self, message: dict[str, Any], received_ns: int) -> None:
        data = message["data"]
        if message["type"] == "snapshot":
            self.bids.clear()
            self.asks.clear()
        elif message["type"] != "delta":
            raise ValueError(f"unknown book message type {message['type']!r}")
        for side, target in (("b", self.bids), ("a", self.asks)):
            for price_s, size_s in data.get(side, []):
                price, size = float(price_s), float(size_s)
                if price <= 0 or size < 0:
                    raise ValueError("book price must be positive and size non-negative")
                if size == 0:
                    target.pop(price, None)
                else:
                    target[price] = size
        self.received_ns = received_ns
        cts = message.get("cts")
        self.cts_ms = int(cts) if cts is not None else None

    def metrics(self, received_ns: int) -> dict[str, float | int | None]:
        if not self.bids or not self.asks:
            return {"mid": None, "spread_bps": None, "top5_imbalance": None,
                    "top5_bid_notional": None, "top5_ask_notional": None,
                    "book_age_ms": None, "book_cts_ms": self.cts_ms}
        bid, ask = max(self.bids), min(self.asks)
        mid = (bid + ask) / 2.0
        bid5 = sorted(self.bids.items(), reverse=True)[:5]
        ask5 = sorted(self.asks.items())[:5]
        bid_notional = sum(price * size for price, size in bid5)
        ask_notional = sum(price * size for price, size in ask5)
        total = bid_notional + ask_notional
        return {
            "mid": mid,
            "spread_bps": (ask - bid) / mid * 10_000.0,
            "top5_imbalance": (bid_notional - ask_notional) / total if total else 0.0,
            "top5_bid_notional": bid_notional,
            "top5_ask_notional": ask_notional,
            "book_age_ms": (received_ns - self.received_ns) / 1_000_000.0 if self.received_ns is not None else None,
            "book_cts_ms": self.cts_ms,
        }


def raw_rows(path: Path) -> Iterator[tuple[int, dict[str, Any]]]:
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            try:
                row = json.loads(line)
                received_ns, message = row["received_ns"], row["message"]
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                raise ValueError(f"invalid raw row {number}") from exc
            if not isinstance(received_ns, int) or not isinstance(message, dict):
                raise ValueError(f"invalid raw row {number}")
            yield received_ns, message


def _new_bucket(symbol: str, bucket_ns: int, book: Book, received_ns: int, cvd: float) -> dict[str, Any]:
    return {
        "symbol": symbol, "bucket_received_ns": bucket_ns, "trade_count": 0,
        "buy_notional": 0.0, "sell_notional": 0.0, "delta_notional": 0.0,
        "cvd_notional": cvd, "open": None, "high": None, "low": None, "close": None,
        **book.metrics(received_ns),
    }


def build_features(path: Path, interval_ns: int = INTERVAL_NS) -> list[dict[str, Any]]:
    if interval_ns <= 0:
        raise ValueError("interval_ns must be positive")
    books: dict[str, Book] = {}
    active: dict[tuple[str, int], dict[str, Any]] = {}
    cvd: dict[str, float] = {}

    for received_ns, message in raw_rows(path):
        topic = str(message.get("topic", ""))
        if topic.startswith("orderbook."):
            data = message.get("data")
            if not isinstance(data, dict) or "s" not in data:
                raise ValueError("malformed orderbook message")
            symbol = str(data["s"])
            books.setdefault(symbol, Book()).apply(message, received_ns)
            continue
        if not topic.startswith("publicTrade."):
            continue
        for trade in message.get("data", []):
            symbol = str(trade["s"])
            if symbol not in books:
                # A trade before an initial local book snapshot is usable for
                # Delta/CVD but not executable-book features.
                books[symbol] = Book()
            price, size = float(trade["p"]), float(trade["v"])
            if price <= 0 or size <= 0 or trade["S"] not in {"Buy", "Sell"}:
                raise ValueError("malformed public trade")
            signed = price * size * (1.0 if trade["S"] == "Buy" else -1.0)
            cvd[symbol] = cvd.get(symbol, 0.0) + signed
            bucket_ns = received_ns // interval_ns * interval_ns
            key = (symbol, bucket_ns)
            row = active.setdefault(key, _new_bucket(symbol, bucket_ns, books[symbol], received_ns, cvd[symbol] - signed))
            row["trade_count"] += 1
            if signed > 0:
                row["buy_notional"] += signed
            else:
                row["sell_notional"] -= signed
            row["delta_notional"] += signed
            row["cvd_notional"] = cvd[symbol]
            row["open"] = price if row["open"] is None else row["open"]
            row["high"] = price if row["high"] is None else max(row["high"], price)
            row["low"] = price if row["low"] is None else min(row["low"], price)
            row["close"] = price
            # The feature state observed at this trade is the only book state
            # attached to the bucket; later book deltas cannot leak backward.
            row.update(books[symbol].metrics(received_ns))
    return [active[key] for key in sorted(active)]


def write_features(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["symbol", "bucket_received_ns", "trade_count", "buy_notional", "sell_notional",
              "delta_notional", "cvd_notional", "open", "high", "low", "close", "mid",
              "spread_bps", "top5_imbalance", "top5_bid_notional", "top5_ask_notional",
              "book_age_ms", "book_cts_ms"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Reconstruct raw Bybit L2 and emit 15s flow features")
    parser.add_argument("capture", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = build_features(args.capture)
    write_features(args.output, rows)
    print(json.dumps({"capture": str(args.capture), "output": str(args.output), "rows": len(rows)}))


if __name__ == "__main__":
    main()
