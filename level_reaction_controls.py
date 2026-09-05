#!/usr/bin/env python3
"""Deterministic matched controls for the level-reaction diagnostic."""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from statistics import mean, median

import numpy as np

from level_reaction_scan import ROOT, atr14, load, ms, outcome, write_csv


def month_hour(timestamp_ms: int) -> tuple[str, int]:
    value = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
    return value.strftime("%Y-%m"), value.hour


def seed_for(*values: str) -> int:
    return int.from_bytes(hashlib.sha256("|".join(values).encode()).digest()[:8], "big")


def main() -> None:
    protocol = json.loads((ROOT / "level_reaction_protocol.json").read_text(encoding="utf-8"))
    with (ROOT / "reports" / "level_reaction_events_2025.csv").open(newline="", encoding="utf-8") as handle:
        events = list(csv.DictReader(handle))
    horizons = [int(x) for x in protocol["forward_horizons_minutes"]]
    start, end = ms(protocol["data"]["development_start"]), ms(protocol["data"]["development_end_exclusive"])
    paired: list[dict[str, object]] = []

    for symbol in protocol["symbols"]:
        candles = load(ROOT / "data" / "bybit_history" / f"{symbol}_1m_2024-12_2025-12.npz")
        timestamps = candles["timestamp"].astype(np.int64)
        atr = atr14(candles)
        valid = np.flatnonzero((timestamps >= start) & (timestamps < end) & np.isfinite(atr))
        labels: dict[int, tuple[str, int, int]] = {}
        pools: dict[tuple[str, int, int], list[int]] = {}
        by_month: dict[str, list[int]] = {}
        for i in valid:
            month, _ = month_hour(int(timestamps[i]))
            by_month.setdefault(month, []).append(int(i))
        for month, indexes in by_month.items():
            cuts = np.quantile(atr[indexes], [0.2, 0.4, 0.6, 0.8])
            for i in indexes:
                _, hour = month_hour(int(timestamps[i]))
                quintile = int(np.searchsorted(cuts, atr[i], side="right"))
                label = (month, hour, quintile)
                labels[i] = label
                if i + max(horizons) < len(timestamps):
                    pools.setdefault(label, []).append(i)
        index_by_ts = {int(value): i for i, value in enumerate(timestamps)}
        symbol_events = [event for event in events if event["symbol"] == symbol]
        group_event_indexes: dict[tuple[str, str], set[int]] = {}
        for event in symbol_events:
            group_event_indexes.setdefault((event["level_type"], event["side"]), set()).add(index_by_ts[int(event["timestamp_ms"])])
        for event in symbol_events:
            event_i = index_by_ts[int(event["timestamp_ms"])]
            pool = pools[labels[event_i]]
            blocked = group_event_indexes[(event["level_type"], event["side"])]
            offset = seed_for(symbol, event["level_type"], event["side"], event["timestamp_ms"]) % len(pool)
            control_i = next((pool[(offset + step) % len(pool)] for step in range(len(pool))
                              if pool[(offset + step) % len(pool)] not in blocked and pool[(offset + step) % len(pool)] != event_i), None)
            if control_i is None:
                continue
            control = outcome(candles, control_i, event["side"], horizons)
            row: dict[str, object] = {"symbol": symbol, "level_type": event["level_type"], "side": event["side"],
                                      "event_timestamp_ms": int(event["timestamp_ms"]),
                                      "control_timestamp_ms": int(timestamps[control_i])}
            for horizon in horizons:
                event_return = float(event[f"return_{horizon}m_bps"])
                control_return = float(control[f"return_{horizon}m_bps"])
                row[f"event_{horizon}m_bps"] = event_return
                row[f"control_{horizon}m_bps"] = control_return
                row[f"excess_{horizon}m_bps"] = event_return - control_return
            paired.append(row)

    summary: list[dict[str, object]] = []
    rng = np.random.default_rng(20250905)
    keys = sorted({(row["symbol"], row["level_type"], row["side"]) for row in paired})
    for symbol, level_type, side in keys:
        group = [row for row in paired if (row["symbol"], row["level_type"], row["side"]) == (symbol, level_type, side)]
        for horizon in horizons:
            excess = np.asarray([float(row[f"excess_{horizon}m_bps"]) for row in group])
            bootstrap = np.mean(rng.choice(excess, size=(2000, len(excess)), replace=True), axis=1)
            summary.append({"symbol": symbol, "level_type": level_type, "side": side,
                            "horizon_minutes": horizon, "pairs": len(excess),
                            "mean_excess_bps": mean(excess), "median_excess_bps": median(excess),
                            "mean_excess_ci95_low": float(np.quantile(bootstrap, 0.025)),
                            "mean_excess_ci95_high": float(np.quantile(bootstrap, 0.975))})
    write_csv(ROOT / "reports" / "level_reaction_controls_2025.csv", paired)
    write_csv(ROOT / "reports" / "level_reaction_control_summary_2025.csv", summary)
    print(json.dumps({"pairs": len(paired), "summary_rows": len(summary)}))


if __name__ == "__main__":
    main()
