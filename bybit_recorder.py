#!/usr/bin/env python3
"""Public, read-only Bybit linear market-data recorder.

This starts no authenticated session and cannot place an order.  It writes raw
messages so later replay can distinguish an exchange event time from the time
the local recorder received it.  The replay engine, not this program, is
responsible for reconstructing books and judging data gaps.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import time
import uuid


URL = "wss://stream.bybit.com/v5/public/linear"
DEFAULT_SYMBOLS = ("BTCUSDT", "ETHUSDT")


def topics(symbols: tuple[str, ...]) -> list[str]:
    return [topic for symbol in symbols for topic in
            (f"orderbook.50.{symbol}", f"publicTrade.{symbol}",
             f"allLiquidation.{symbol}")]


def output_path(root: Path, connection_id: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return root / f"bybit-linear-{stamp}-{connection_id}.jsonl"


async def record(root: Path, symbols: tuple[str, ...], rotate_minutes: int) -> None:
    try:
        import websockets
    except ImportError as exc:  # Make the safety boundary usable without deps.
        raise RuntimeError("install the optional dependency: pip install websockets") from exc

    root.mkdir(parents=True, exist_ok=True)
    backoff = (1, 2, 4, 8, 15)
    attempt = 0
    while True:
        connection_id = uuid.uuid4().hex
        path = output_path(root, connection_id)
        started_ns = time.time_ns()
        try:
            async with websockets.connect(URL, ping_interval=20, ping_timeout=10) as socket:
                await socket.send(json.dumps({"op": "subscribe", "args": topics(symbols)}))
                print(f"connected {connection_id}; writing {path}", flush=True)
                attempt = 0
                with path.open("x", encoding="utf-8") as handle:
                    while True:
                        raw = await asyncio.wait_for(socket.recv(), timeout=30)
                        row = {
                            "received_ns": time.time_ns(),
                            "connection_id": connection_id,
                            "message": json.loads(raw),
                        }
                        handle.write(json.dumps(row, separators=(",", ":")) + "\n")
                        handle.flush()
                        if time.time_ns() - started_ns >= rotate_minutes * 60 * 1_000_000_000:
                            break
        except (OSError, asyncio.TimeoutError, RuntimeError) as exc:
            delay = backoff[min(attempt, len(backoff) - 1)]
            attempt += 1
            print(f"recorder reconnect in {delay}s after {type(exc).__name__}: {exc}", flush=True)
            await asyncio.sleep(delay)


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only Bybit L2/trade recorder")
    parser.add_argument("--output", type=Path, default=Path("data/bybit_raw"))
    parser.add_argument("--symbols", nargs="+", default=list(DEFAULT_SYMBOLS))
    parser.add_argument("--rotate-minutes", type=int, default=60)
    args = parser.parse_args()
    if args.rotate_minutes < 1:
        raise SystemExit("--rotate-minutes must be at least 1")
    symbols = tuple(symbol.upper() for symbol in args.symbols)
    if not symbols or any(not symbol.isalnum() for symbol in symbols):
        raise SystemExit("symbols must be non-empty alphanumeric market symbols")
    asyncio.run(record(args.output, symbols, args.rotate_minutes))


if __name__ == "__main__":
    main()
