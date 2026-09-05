#!/usr/bin/env python3
"""Causal diagnostic of failed sweeps around pre-existing price levels."""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from statistics import mean, median
from typing import Any

import numpy as np

import indicators
from signals import aggregate


ROOT = Path(__file__).resolve().parent


def ms(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)


def load(path: Path) -> dict[str, np.ndarray]:
    source = np.load(path)
    return {name: source[name] for name in source.files}


def atr14(c: dict[str, np.ndarray]) -> np.ndarray:
    jesse = np.column_stack((c["timestamp"], c["open"], c["close"], c["high"], c["low"], c["volume"]))
    return indicators.atr(jesse, 14)


def previous_days(c: dict[str, np.ndarray]) -> dict[int, tuple[float, float]]:
    days = c["timestamp"].astype(np.int64) // 86_400_000
    result: dict[int, tuple[float, float]] = {}
    for day in np.unique(days):
        mask = days == day
        result[int(day + 1)] = (float(np.max(c["high"][mask])), float(np.min(c["low"][mask])))
    return result


def session_vwap(c: dict[str, np.ndarray]) -> np.ndarray:
    """VWAP visible at each minute open, excluding the current candle."""
    days = c["timestamp"].astype(np.int64) // 86_400_000
    result = np.full(len(days), np.nan)
    for day in np.unique(days):
        indexes = np.flatnonzero(days == day)
        volume = c["volume"][indexes]
        turnover = c["turnover"][indexes]
        cumulative_volume = np.cumsum(volume)
        cumulative_turnover = np.cumsum(turnover)
        valid = cumulative_volume[:-1] > 0
        result[indexes[1:][valid]] = cumulative_turnover[:-1][valid] / cumulative_volume[:-1][valid]
    return result


def confirmed_swings(c: dict[str, np.ndarray], left: int, right: int) -> tuple[list[tuple[int, str, float]], list[tuple[int, str, float]]]:
    raw = np.column_stack((c["timestamp"], c["open"], c["close"], c["high"], c["low"], c["volume"]))
    hourly = aggregate(raw, 60)
    highs: list[tuple[int, str, float]] = []
    lows: list[tuple[int, str, float]] = []
    for i in range(left, len(hourly) - right):
        available_ms = int(hourly[i + right, 0] + 60 * 60_000)
        identity = str(int(hourly[i, 0]))
        if hourly[i, 3] > np.max(hourly[i-left:i, 3]) and hourly[i, 3] >= np.max(hourly[i+1:i+right+1, 3]):
            highs.append((available_ms, identity, float(hourly[i, 3])))
        if hourly[i, 4] < np.min(hourly[i-left:i, 4]) and hourly[i, 4] <= np.min(hourly[i+1:i+right+1, 4]):
            lows.append((available_ms, identity, float(hourly[i, 4])))
    return highs, lows


def outcome(c: dict[str, np.ndarray], i: int, side: str, horizons: list[int]) -> dict[str, float]:
    entry = float(c["open"][i + 1])
    sign = 1.0 if side == "long" else -1.0
    result: dict[str, float] = {"entry": entry}
    for horizon in horizons:
        end = i + horizon
        gross = sign * (float(c["close"][end]) / entry - 1.0) * 10_000.0
        window_high = float(np.max(c["high"][i + 1:end + 1]))
        window_low = float(np.min(c["low"][i + 1:end + 1]))
        mfe = (window_high / entry - 1.0) * 10_000.0 if side == "long" else (entry / window_low - 1.0) * 10_000.0
        mae = (entry / window_low - 1.0) * 10_000.0 if side == "long" else (window_high / entry - 1.0) * 10_000.0
        result[f"return_{horizon}m_bps"] = gross
        result[f"mfe_{horizon}m_bps"] = mfe
        result[f"mae_{horizon}m_bps"] = mae
    return result


def scan(c: dict[str, np.ndarray], protocol: dict[str, Any], symbol: str) -> list[dict[str, Any]]:
    start = ms(protocol["data"]["development_start"])
    end = ms(protocol["data"]["development_end_exclusive"])
    horizons = [int(x) for x in protocol["forward_horizons_minutes"]]
    max_horizon = max(horizons)
    atr = atr14(c)
    prior = previous_days(c)
    vwap = session_vwap(c)
    pivot = protocol["levels"]["confirmed_1h_swing_high_low"]
    swing_highs, swing_lows = confirmed_swings(c, int(pivot["left_hours"]), int(pivot["right_hours"]))
    high_idx = low_idx = 0
    active_high: tuple[str, float] | None = None
    active_low: tuple[str, float] | None = None
    consumed: set[tuple[str, str]] = set()
    last_vwap: dict[str, int | None] = {"long": None, "short": None}
    cooldown_ms = int(protocol["event"]["dynamic_vwap_cooldown_minutes"]) * 60_000
    max_penetration = float(protocol["event"]["max_penetration_atr"])
    events: list[dict[str, Any]] = []

    for i, timestamp in enumerate(c["timestamp"].astype(np.int64)):
        while high_idx < len(swing_highs) and swing_highs[high_idx][0] <= timestamp:
            _, identity, price = swing_highs[high_idx]
            active_high = (identity, price)
            high_idx += 1
        while low_idx < len(swing_lows) and swing_lows[low_idx][0] <= timestamp:
            _, identity, price = swing_lows[low_idx]
            active_low = (identity, price)
            low_idx += 1
        if timestamp < start or timestamp >= end or i + max_horizon >= len(c["timestamp"]) or not (math.isfinite(atr[i]) and atr[i] > 0):
            continue
        day = int(timestamp // 86_400_000)
        levels: list[tuple[str, str, str, float, bool]] = []
        if day in prior:
            pdh, pdl = prior[day]
            levels.extend((("previous_day_high", str(day - 1), "short", pdh, True),
                           ("previous_day_low", str(day - 1), "long", pdl, True)))
        if active_high:
            levels.append(("confirmed_1h_swing_high", active_high[0], "short", active_high[1], True))
        if active_low:
            levels.append(("confirmed_1h_swing_low", active_low[0], "long", active_low[1], True))
        if math.isfinite(vwap[i]):
            levels.extend((("session_vwap", str(day), "long", float(vwap[i]), False),
                           ("session_vwap", str(day), "short", float(vwap[i]), False)))

        for kind, identity, side, level, static in levels:
            key = (kind, identity)
            if static and key in consumed:
                continue
            crossed = float(c["low"][i]) < level if side == "long" else float(c["high"][i]) > level
            reclaimed = float(c["close"][i]) > level if side == "long" else float(c["close"][i]) < level
            if crossed and static:
                consumed.add(key)
            if not (crossed and reclaimed):
                continue
            if not static and last_vwap[side] is not None and int(timestamp) - int(last_vwap[side]) < cooldown_ms:
                continue
            penetration = (level - float(c["low"][i])) / atr[i] if side == "long" else (float(c["high"][i]) - level) / atr[i]
            if penetration > max_penetration:
                continue
            if not static:
                last_vwap[side] = int(timestamp)
            events.append({"symbol": symbol, "timestamp_ms": int(timestamp), "level_type": kind,
                           "level_identity": identity, "side": side, "level_price": level,
                           "signal_close": float(c["close"][i]), "penetration_atr": float(penetration),
                           **outcome(c, i, side, horizons)})
    return events


def summarize(events: list[dict[str, Any]], protocol: dict[str, Any]) -> list[dict[str, Any]]:
    horizons = [int(x) for x in protocol["forward_horizons_minutes"]]
    maker_taker = float(protocol["cost_thresholds_bps"]["maker_entry_taker_exit"])
    result: list[dict[str, Any]] = []
    keys = sorted({(e["symbol"], e["level_type"], e["side"]) for e in events})
    for symbol, level_type, side in keys:
        group = [e for e in events if (e["symbol"], e["level_type"], e["side"]) == (symbol, level_type, side)]
        for horizon in horizons:
            values = [float(e[f"return_{horizon}m_bps"]) for e in group]
            result.append({"symbol": symbol, "level_type": level_type, "side": side,
                           "horizon_minutes": horizon, "events": len(group),
                           "mean_return_bps": mean(values), "median_return_bps": median(values),
                           "positive_rate": sum(x > 0 for x in values) / len(values),
                           "clears_7_5bp_rate": sum(x > maker_taker for x in values) / len(values),
                           "median_mfe_bps": median(float(e[f"mfe_{horizon}m_bps"]) for e in group),
                           "median_mae_bps": median(float(e[f"mae_{horizon}m_bps"]) for e in group)})
    return result


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(rows[0]) if rows else [],
            lineterminator="\n",
        )
        if rows:
            writer.writeheader()
            writer.writerows(rows)


def main() -> None:
    protocol = json.loads((ROOT / "level_reaction_protocol.json").read_text(encoding="utf-8"))
    all_events: list[dict[str, Any]] = []
    for symbol in protocol["symbols"]:
        path = ROOT / "data" / "bybit_history" / f"{symbol}_1m_2024-12_2025-12.npz"
        all_events.extend(scan(load(path), protocol, symbol))
    summary = summarize(all_events, protocol)
    write_csv(ROOT / "reports" / "level_reaction_events_2025.csv", all_events)
    write_csv(ROOT / "reports" / "level_reaction_summary_2025.csv", summary)
    print(json.dumps({"events": len(all_events), "summary_rows": len(summary)}))


if __name__ == "__main__":
    main()
