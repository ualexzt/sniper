#!/usr/bin/env python3
"""Audit raw JSONL captured by :mod:`bybit_recorder` without altering it."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


GAP_NS = 5_000_000_000


def audit_rows(rows: Iterable[dict[str, Any]], gap_ns: int = GAP_NS) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    gaps: list[dict[str, int]] = []
    timestamp_reversals = 0
    book_resets: Counter[str] = Counter()
    bad_deltas: Counter[str] = Counter()
    last_received: int | None = None
    last_update: dict[str, int] = {}
    row_count = 0

    for row in rows:
        row_count += 1
        received = row.get("received_ns")
        message = row.get("message")
        if not isinstance(received, int) or not isinstance(message, dict):
            raise ValueError(f"row {row_count} lacks integer received_ns or object message")
        if last_received is not None:
            if received < last_received:
                timestamp_reversals += 1
            elif received - last_received > gap_ns:
                gaps.append({"after_received_ns": last_received, "gap_ns": received - last_received})
        last_received = received

        topic = str(message.get("topic", message.get("op", "other")))
        counts[topic] += 1
        if not topic.startswith("orderbook."):
            continue
        data = message.get("data")
        if not isinstance(data, dict):
            raise ValueError(f"row {row_count} has malformed orderbook data")
        symbol = str(data.get("s", topic.rsplit(".", 1)[-1]))
        update = data.get("u")
        if not isinstance(update, int):
            raise ValueError(f"row {row_count} has orderbook event without integer update id")
        key = f"{topic}:{symbol}"
        if message.get("type") == "snapshot":
            book_resets[key] += 1
            last_update[key] = update
        elif message.get("type") == "delta":
            previous = last_update.get(key)
            if previous is None or update <= previous:
                bad_deltas[key] += 1
            last_update[key] = update
        else:
            raise ValueError(f"row {row_count} has unknown orderbook message type")

    return {
        "rows": row_count,
        "topics": dict(sorted(counts.items())),
        "receive_timestamp_reversals": timestamp_reversals,
        "gaps_over_5_seconds": gaps,
        "orderbook_snapshots": dict(sorted(book_resets.items())),
        "non_monotonic_or_orphan_deltas": dict(sorted(bad_deltas.items())),
        "pass": row_count > 0 and not timestamp_reversals and not gaps and not bad_deltas,
    }


def read_rows(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON on line {line_number}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"line {line_number} is not an object")
            yield value


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit a Bybit public capture JSONL file")
    parser.add_argument("capture", type=Path)
    args = parser.parse_args()
    digest = hashlib.sha256(args.capture.read_bytes()).hexdigest()
    report = audit_rows(read_rows(args.capture))
    report["capture"] = str(args.capture)
    report["sha256"] = digest
    print(json.dumps(report, indent=2, sort_keys=True))
    if not report["pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
