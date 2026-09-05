#!/usr/bin/env python3
"""Label causal L2 absorption candidates and attach an evaluation-only return."""
from __future__ import annotations

import argparse
import csv
import json
import statistics
from pathlib import Path
from typing import Any


def _number(row: dict[str, str], key: str) -> float:
    value = row.get(key, "")
    if value in (None, ""):
        raise ValueError(f"missing {key}")
    return float(value)


def load_rows(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def candidates(rows: list[dict[str, Any]], protocol: dict[str, Any]) -> list[dict[str, Any]]:
    lookback = int(protocol["lookback_buckets"])
    horizon_ns = int(protocol["forward_horizon_seconds"]) * 1_000_000_000
    output: list[dict[str, Any]] = []
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_symbol.setdefault(str(row["symbol"]), []).append(row)
    for symbol, series in by_symbol.items():
        series.sort(key=lambda r: int(r["bucket_received_ns"]))
        for i in range(lookback, len(series)):
            row, prior = series[i], series[i - lookback:i]
            try:
                delta = _number(row, "delta_notional")
                mid, high, low = (_number(row, key) for key in ("mid", "high", "low"))
                bid_depth = _number(row, "top5_bid_notional")
                ask_depth = _number(row, "top5_ask_notional")
                abs_delta = statistics.median(abs(_number(p, "delta_notional")) for p in prior)
                prior_range = statistics.median((_number(p, "high") - _number(p, "low")) / _number(p, "close") * 10_000.0 for p in prior)
                prior_bid = statistics.median(_number(p, "top5_bid_notional") for p in prior)
                prior_ask = statistics.median(_number(p, "top5_ask_notional") for p in prior)
            except ValueError:
                continue
            if abs_delta <= 0 or prior_range <= 0:
                continue
            current_range = (high - low) / mid * 10_000.0
            compressed = current_range <= float(protocol["max_range_multiple_of_trailing_median"]) * prior_range
            delta_multiple = float(protocol["delta_multiple_of_trailing_median_abs"])
            depth_multiple = float(protocol["min_relevant_top5_depth_multiple_of_trailing_median"])
            side = None
            if compressed and delta <= -delta_multiple * abs_delta and bid_depth >= depth_multiple * prior_bid:
                side = "long"
            elif compressed and delta >= delta_multiple * abs_delta and ask_depth >= depth_multiple * prior_ask:
                side = "short"
            if side is None:
                continue
            target = next((x for x in series[i + 1:] if int(x["bucket_received_ns"]) >= int(row["bucket_received_ns"]) + horizon_ns), None)
            forward_bps = None if target is None or not target.get("mid") else (_number(target, "mid") / mid - 1.0) * 10_000.0
            output.append({
                "symbol": symbol, "bucket_received_ns": int(row["bucket_received_ns"]), "side": side,
                "delta_notional": delta, "range_bps": current_range,
                "trailing_abs_delta_median": abs_delta, "trailing_range_bps_median": prior_range,
                "relevant_top5_notional": bid_depth if side == "long" else ask_depth,
                "forward_horizon_seconds": int(protocol["forward_horizon_seconds"]),
                "forward_mid_return_bps": forward_bps,
            })
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Label L2 absorption candidates from 15s features")
    parser.add_argument("features", type=Path)
    parser.add_argument("--protocol", type=Path, default=Path("absorption_protocol.json"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    protocol = json.loads(args.protocol.read_text(encoding="utf-8"))
    events = candidates(load_rows(args.features), protocol)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"protocol": protocol, "events": events}, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"events": len(events), "output": str(args.output)}))


if __name__ == "__main__":
    main()
