"""Causal, symmetric 5m-trend / 1m-pullback signal hypothesis.

This is deliberately a candle-level research signal.  It says nothing about
maker queue position or fill probability; those belong to the later L2 replay.
"""
from __future__ import annotations

import numpy as np

import indicators
from signals import aggregate


DEFAULTS = {
    "trend_fast_ema": 20,
    "trend_slow_ema": 50,
    "entry_ema": 12,
    "atr_period": 14,
    "min_pullback_atr": 0.25,
    "max_pullback_atr": 1.50,
    "trend_min_separation_atr": 0.0,
    "min_reclaim_body_atr": 0.0,
    "volume_period": 20,
    "min_volume_ratio": 0.0,
    "cooldown_minutes": 0,
}


def build_signals(candles: dict[str, np.ndarray], hp: dict[str, float] | None = None) -> dict[str, np.ndarray]:
    """Return actions known only after each completed 1m candle.

    A long needs a completed 5m fast-over-slow regime, a pullback below the
    1m entry EMA, then a close reclaiming it.  Shorts are an exact sign mirror.
    The returned timestamp is the next minute boundary, so the engine enters
    at the following 1m open.
    """
    h = {**DEFAULTS, **(hp or {})}
    raw = np.column_stack([candles[k] for k in ("timestamp", "open", "close", "high", "low", "volume")])
    minute_close = raw[:, 2]
    minute_high, minute_low = raw[:, 3], raw[:, 4]
    entry_ema = indicators.ema(minute_close, int(h["entry_ema"]))
    atr = indicators.atr(raw, int(h["atr_period"]))
    volume_avg = indicators.sma(raw[:, 5], int(h["volume_period"]))
    trend = aggregate(raw, 5)
    fast = indicators.ema(trend[:, 2], int(h["trend_fast_ema"]))
    slow = indicators.ema(trend[:, 2], int(h["trend_slow_ema"]))
    trend_atr = indicators.atr(trend, int(h["atr_period"]))

    timestamps = raw[:, 0].astype(np.int64) + 60_000
    action = np.zeros(len(raw), dtype=np.int8)
    anchor_ts = np.full(len(raw), -1, dtype=np.int64)
    # At 1m close t, only 5m bars whose close is <= t are usable.
    trend_end = trend[:, 0].astype(np.int64) + 5 * 60_000
    last_signal_end = -np.inf
    for i, end in enumerate(timestamps):
        k = int(np.searchsorted(trend_end, end, side="right") - 1)
        if k < 0:
            continue
        anchor_ts[i] = int(trend[k, 0])
        if i == 0 or not (np.isfinite(atr[i]) and atr[i] > 0 and np.isfinite(entry_ema[i]) and np.isfinite(entry_ema[i - 1])):
            continue
        if not (np.isfinite(trend_atr[k]) and trend_atr[k] > 0 and np.isfinite(volume_avg[i]) and volume_avg[i] > 0):
            continue
        separation = float(h["trend_min_separation_atr"]) * trend_atr[k]
        long_regime = np.isfinite(fast[k]) and np.isfinite(slow[k]) and fast[k] - slow[k] >= separation and trend[k, 2] > slow[k]
        short_regime = np.isfinite(fast[k]) and np.isfinite(slow[k]) and slow[k] - fast[k] >= separation and trend[k, 2] < slow[k]
        pullback_long = entry_ema[i - 1] - minute_low[i - 1]
        pullback_short = minute_high[i - 1] - entry_ema[i - 1]
        lower, upper = float(h["min_pullback_atr"]) * atr[i], float(h["max_pullback_atr"]) * atr[i]
        reclaim_ok = abs(minute_close[i] - raw[i, 1]) >= float(h["min_reclaim_body_atr"]) * atr[i]
        volume_ok = raw[i, 5] >= float(h["min_volume_ratio"]) * volume_avg[i]
        cooldown_ok = end - last_signal_end >= float(h["cooldown_minutes"]) * 60_000
        if long_regime and reclaim_ok and volume_ok and cooldown_ok and lower <= pullback_long <= upper and minute_close[i - 1] <= entry_ema[i - 1] and minute_close[i] > entry_ema[i]:
            action[i] = 1
        elif short_regime and reclaim_ok and volume_ok and cooldown_ok and lower <= pullback_short <= upper and minute_close[i - 1] >= entry_ema[i - 1] and minute_close[i] < entry_ema[i]:
            action[i] = -1
        if action[i]:
            last_signal_end = end
    return {"timestamp": timestamps, "close": minute_close.copy(), "action": action,
            "atr": atr, "entry_atr": atr.copy(), "anchor_ts": anchor_ts}
