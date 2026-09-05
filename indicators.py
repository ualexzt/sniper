"""NumPy-only indicator and candle helpers used by the Jesse reproduction.

The candle layout is Jesse's layout: ``timestamp, open, close, high, low,
volume``.  The implementations below intentionally follow the current Jesse
Rust kernels (SMA-seeded EMA, population standard deviation, Wilder ATR and
ADX).  No indicator uses the last row as a completed higher-timeframe bar
unless the caller says that it is completed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


def _candles(candles: np.ndarray) -> np.ndarray:
    c = np.asarray(candles, dtype=np.float64)
    if c.ndim != 2 or c.shape[1] != 6:
        raise ValueError("candles must have shape (n, 6): timestamp, open, close, high, low, volume")
    return c


def aggregate_observed_bucket(candles: np.ndarray, bucket_start_ms: int | None = None) -> np.ndarray:
    """Aggregate observed 1m rows from one clock bucket.

    Missing minutes are allowed, matching Jesse's observed-candle generator;
    OHLCV is formed from rows that have actually been observed.
    """
    c = _candles(candles)
    if len(c) == 0:
        raise ValueError("cannot aggregate an empty candle set")
    start = int(c[0, 0]) if bucket_start_ms is None else int(bucket_start_ms)
    return np.array((start, c[0, 1], c[-1, 2], np.max(c[:, 3]), np.min(c[:, 4]), np.sum(c[:, 5])), dtype=np.float64)


def aggregate_1m_to_anchor(
    candles_1m: np.ndarray,
    anchor_minutes: int,
    *,
    available_at_ms: int | None = None,
    include_forming: bool = False,
) -> np.ndarray:
    """Aggregate 1m candles into UTC clock-aligned anchor candles.

    ``available_at_ms`` is the information boundary.  A bucket is completed
    only when ``bucket_start + anchor_minutes*60_000 <= available_at_ms``.
    With ``include_forming=True`` the last observed bucket is also returned,
    even when its closing boundary is beyond the information boundary.
    """
    c = _candles(candles_1m)
    if anchor_minutes <= 0:
        raise ValueError("anchor_minutes must be positive")
    if len(c) == 0:
        return np.empty((0, 6), dtype=np.float64)
    ts = c[:, 0].astype(np.int64)
    if (np.diff(ts) <= 0).any() or (ts % 60_000 != 0).any():
        raise ValueError("1m timestamps must be strictly increasing and minute aligned")
    width = int(anchor_minutes) * 60_000
    starts = (ts // width) * width
    cuts = np.flatnonzero(np.diff(starts)) + 1
    lo = np.concatenate(([0], cuts))
    hi = np.concatenate((cuts, [len(c)]))
    if available_at_ms is None:
        available_at_ms = int(ts[-1]) + 60_000
    out: list[np.ndarray] = []
    for a, b in zip(lo, hi):
        bucket_start = int(starts[a])
        complete = bucket_start + width <= int(available_at_ms)
        if complete or (include_forming and b == len(c)):
            out.append(aggregate_observed_bucket(c[a:b], bucket_start))
    return np.asarray(out, dtype=np.float64).reshape((-1, 6))


def _source(candles: np.ndarray | list[float]) -> np.ndarray:
    x = np.asarray(candles, dtype=np.float64)
    if x.ndim == 2:
        return x[:, 2]
    if x.ndim != 1:
        raise ValueError("source must be one-dimensional or candles (n, 6)")
    return x


def sma(source: np.ndarray, period: int) -> np.ndarray:
    x = _source(source)
    if period <= 0:
        raise ValueError("period must be positive")
    out = np.full(len(x), np.nan)
    if len(x) < period:
        return out
    cs = np.concatenate(([0.0], np.cumsum(x, dtype=np.float64)))
    out[period - 1 :] = (cs[period:] - cs[:-period]) / period
    return out


def ema(source: np.ndarray, period: int) -> np.ndarray:
    """SMA-seeded EMA, equivalent to Jesse's EMA kernel."""
    x = _source(source)
    if period <= 0:
        raise ValueError("period must be positive")
    out = np.full(len(x), np.nan)
    if len(x) < period:
        return out
    out[period - 1] = np.mean(x[:period])
    alpha = 2.0 / (period + 1.0)
    for i in range(period, len(x)):
        out[i] = out[i - 1] + alpha * (x[i] - out[i - 1])
    return out


def bollinger_bands(candles: np.ndarray, period: int, dev: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Jesse BBANDS default: close, SMA, population standard deviation."""
    x = _candles(candles)[:, 2]
    if period <= 0:
        raise ValueError("period must be positive")
    up = np.full(len(x), np.nan)
    mid = np.full(len(x), np.nan)
    low = np.full(len(x), np.nan)
    if len(x) < period:
        return up, mid, low
    windows = np.lib.stride_tricks.sliding_window_view(x, period)
    m = windows.mean(axis=1)
    # Jesse's Rust moving_std is population std (ddof=0).
    s = windows.std(axis=1, ddof=0)
    mid[period - 1 :] = m
    up[period - 1 :] = m + dev * s
    low[period - 1 :] = m - dev * s
    return up, mid, low


def true_range(candles: np.ndarray) -> np.ndarray:
    c = _candles(candles)
    out = np.full(len(c), np.nan)
    if len(c) == 0:
        return out
    out[0] = c[0, 3] - c[0, 4]
    out[1:] = np.maximum.reduce((c[1:, 3] - c[1:, 4], np.abs(c[1:, 3] - c[:-1, 2]), np.abs(c[1:, 4] - c[:-1, 2])))
    return out


def atr(candles: np.ndarray, period: int = 14) -> np.ndarray:
    """Wilder ATR with an SMA seed at index ``period - 1``."""
    tr = true_range(candles)
    out = np.full(len(tr), np.nan)
    if period <= 0:
        raise ValueError("period must be positive")
    if len(tr) < period:
        return out
    out[period - 1] = np.mean(tr[:period])
    for i in range(period, len(tr)):
        out[i] = out[i - 1] + (tr[i] - out[i - 1]) / period
    return out


def _adx_state(candles: np.ndarray, period: int = 14) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Return ADX and its Wilder state arrays, exactly matching Jesse Rust."""
    c = _candles(candles)
    if period <= 0:
        raise ValueError("period must be positive")
    n = len(c)
    out = np.full(n, np.nan)
    tr_s = np.full(n, np.nan)
    plus_s = np.full(n, np.nan)
    minus_s = np.full(n, np.nan)
    dx = np.full(n, np.nan)
    if n <= 2 * period:
        return out, tr_s, plus_s, minus_s, dx
    tr = true_range(c)
    plus = np.zeros(n)
    minus = np.zeros(n)
    if n > 1:
        up = c[1:, 3] - c[:-1, 3]
        down = c[:-1, 4] - c[1:, 4]
        plus[1:] = np.where((up > down) & (up > 0), up, 0.0)
        minus[1:] = np.where((down > up) & (down > 0), down, 0.0)
    tr_acc = plus_acc = minus_acc = 0.0
    dx_values: list[float] = []
    for i in range(1, n):
        if i <= period:
            tr_acc += tr[i]
            plus_acc += plus[i]
            minus_acc += minus[i]
        else:
            tr_acc = tr_acc - tr_acc / period + tr[i]
            plus_acc = plus_acc - plus_acc / period + plus[i]
            minus_acc = minus_acc - minus_acc / period + minus[i]
        tr_s[i], plus_s[i], minus_s[i] = tr_acc, plus_acc, minus_acc
        if i >= period:
            d = 0.0
            if tr_acc != 0.0:
                di_plus = 100.0 * plus_acc / tr_acc
                di_minus = 100.0 * minus_acc / tr_acc
                if di_plus + di_minus != 0.0:
                    d = 100.0 * abs(di_plus - di_minus) / (di_plus + di_minus)
            dx[i] = d
            if i < 2 * period:
                dx_values.append(d)
            elif i == 2 * period:
                out[i] = sum(dx_values) / period
            elif not np.isnan(out[i - 1]):
                out[i] = (out[i - 1] * (period - 1) + d) / period
    return out, tr_s, plus_s, minus_s, dx


def adx(candles: np.ndarray, period: int = 14) -> np.ndarray:
    return _adx_state(candles, period)[0]


def linearreg_slope(candles: np.ndarray, period: int) -> np.ndarray:
    x = _candles(candles)[:, 2]
    if period <= 0:
        raise ValueError("period must be positive")
    out = np.full(len(x), np.nan)
    if len(x) < period:
        return out
    axis = np.arange(period, dtype=np.float64)
    sx = axis.sum()
    sx2 = (axis * axis).sum()
    den = period * sx2 - sx * sx
    windows = np.lib.stride_tricks.sliding_window_view(x, period)
    sy = windows.sum(axis=1)
    sxy = (windows * axis).sum(axis=1)
    out[period - 1 :] = (period * sxy - sx * sy) / den
    return out


def indicator_arrays(
    candles: np.ndarray,
    *,
    bb_period: int = 29,
    bb_dev: float = 1.82,
    kc_mult: float = 1.56,
    slope_period: int = 11,
    adx_period: int = 14,
) -> dict[str, np.ndarray]:
    """Calculate all BBSqueezeTrend indicator series for anchor candles."""
    c = _candles(candles)
    bu, bm, bl = bollinger_bands(c, bb_period, bb_dev)
    mid = ema(c[:, 2], bb_period)
    katr = atr(c, bb_period)
    au, am, al = mid + kc_mult * katr, mid, mid - kc_mult * katr
    a, ts, ps, ms, dx = _adx_state(c, adx_period)
    return {
        "bb_upper": bu, "bb_middle": bm, "bb_lower": bl,
        "kc_upper": au, "kc_middle": am, "kc_lower": al,
        "kc_atr": katr, "atr": atr(c, 14), "adx": a,
        "slope": linearreg_slope(c, slope_period),
        "_adx_tr_smooth": ts, "_adx_plus_smooth": ps,
        "_adx_minus_smooth": ms, "_adx_dx": dx,
    }


def _hp(hp: dict[str, Any] | None) -> dict[str, Any]:
    h = {} if hp is None else hp
    return {
        "bb_period": int(h.get("bb_period", 29)),
        "bb_dev": float(h.get("bb_dev", 1.82)),
        "kc_mult": float(h.get("kc_mult", 1.56)),
        "slope_period": int(h.get("slope_period", 11)),
        "adx_period": int(h.get("adx_period", 14)),
    }


def compute(candles: np.ndarray, hp: dict[str, Any] | None = None) -> dict[str, np.ndarray]:
    """Compatibility entry point for the backtest driver.

    This is the *sequential* Jesse interpretation: all returned values are
    arrays over every candle supplied.  In particular, BB and KC must use the
    entire candle history because the strategy explicitly passes
    ``sequential=True`` for both indicators.
    """
    h = _hp(hp)
    return indicator_arrays(candles, **h)


def partial_last(
    history: np.ndarray,
    forming: np.ndarray,
    hp: dict[str, Any] | None = None,
    *,
    scalar_warmup: int = 240,
) -> dict[str, float]:
    """Evaluate the current forming anchor without rebuilding lower bars.

    ``history`` contains completed anchors and ``forming`` is the observed
    partial anchor.  BB/KC are taken from the full sequential series.  Jesse's
    scalar ``atr``, ``adx`` and ``linearreg_slope`` first pass through
    ``slice_candles(..., sequential=False)``; ``scalar_warmup`` models that
    configured slice (use the exact project warmup when known).
    """
    h = _hp(hp)
    c = _candles(history)
    full = np.vstack((c, np.asarray(forming, dtype=np.float64).reshape(1, 6)))
    all_values = indicator_arrays(full, **h)
    result = {k: float(v[-1]) for k, v in all_values.items() if not k.startswith("_")}
    if scalar_warmup and len(full) > scalar_warmup:
        scalar = indicator_arrays(full[-scalar_warmup:], **h)
        for k in ("atr", "adx", "slope"):
            result[k] = float(scalar[k][-1])
    return result


def forming_snapshot(
    completed: np.ndarray,
    forming: np.ndarray,
    hp: dict[str, Any] | None = None,
    *,
    scalar_warmup: int = 240,
) -> dict[str, float]:
    """Alias with an explicit name for callers handling partial anchors."""
    return partial_last(completed, forming, hp, scalar_warmup=scalar_warmup)


def _single_from_tail(c: np.ndarray, arrays: dict[str, np.ndarray], forming: np.ndarray, *, bb_period: int, bb_dev: float, kc_mult: float, slope_period: int, adx_period: int) -> dict[str, float]:
    """Append one forming candle using prior state; work is O(period)."""
    f = np.asarray(forming, dtype=np.float64)
    if f.shape != (6,):
        raise ValueError("forming candle must have shape (6,)")
    if len(c) < max(bb_period, adx_period * 2 + 1, slope_period):
        full = indicator_arrays(np.vstack((c, f)), bb_period=bb_period, bb_dev=bb_dev, kc_mult=kc_mult, slope_period=slope_period, adx_period=adx_period)
        return {k: float(v[-1]) for k, v in full.items() if not k.startswith("_")}
    closes = np.concatenate((c[-(bb_period - 1):, 2], [f[2]])) if bb_period > 1 else np.array([f[2]])
    m = float(closes.mean())
    s = float(closes.std(ddof=0))
    bb_u, bb_m, bb_l = m + bb_dev * s, m, m - bb_dev * s
    prev_ema = float(arrays["kc_middle"][-1])
    kc_mid = prev_ema + 2.0 / (bb_period + 1.0) * (f[2] - prev_ema)
    prev = c[-1]
    tr = max(f[3] - f[4], abs(f[3] - prev[2]), abs(f[4] - prev[2]))
    kc_atr = float(arrays["kc_atr"][-1]) + (tr - float(arrays["kc_atr"][-1])) / bb_period
    atr14 = float(arrays["atr"][-1]) + (tr - float(arrays["atr"][-1])) / 14.0
    sl = np.concatenate((c[-(slope_period - 1):, 2], [f[2]])) if slope_period > 1 else np.array([f[2]])
    axis = np.arange(slope_period, dtype=np.float64)
    sx, sx2 = axis.sum(), (axis * axis).sum()
    slope = float((slope_period * float(np.dot(sl, axis)) - sx * float(sl.sum())) / (slope_period * sx2 - sx * sx))
    up, down = f[3] - prev[3], prev[4] - f[4]
    plus = up if up > down and up > 0 else 0.0
    minus = down if down > up and down > 0 else 0.0
    tr_s = float(arrays["_adx_tr_smooth"][-1]) - float(arrays["_adx_tr_smooth"][-1]) / adx_period + tr
    ps = float(arrays["_adx_plus_smooth"][-1]) - float(arrays["_adx_plus_smooth"][-1]) / adx_period + plus
    ms = float(arrays["_adx_minus_smooth"][-1]) - float(arrays["_adx_minus_smooth"][-1]) / adx_period + minus
    dip, dim = 100.0 * ps / tr_s, 100.0 * ms / tr_s
    dx = 100.0 * abs(dip - dim) / (dip + dim) if dip + dim else 0.0
    adx_v = (float(arrays["adx"][-1]) * (adx_period - 1) + dx) / adx_period
    return {"bb_upper": bb_u, "bb_middle": bb_m, "bb_lower": bb_l,
            "kc_upper": kc_mid + kc_mult * kc_atr, "kc_middle": kc_mid, "kc_lower": kc_mid - kc_mult * kc_atr,
            "kc_atr": kc_atr, "atr": atr14, "adx": adx_v, "slope": slope}


@dataclass
class FormingAnchorEvaluator:
    """Reuse completed-anchor state while a partial anchor is updated.

    Call ``snapshot(forming)`` on each trading candle.  Call ``complete`` once
    when that anchor closes; the expensive full-series calculation then occurs
    only once per anchor, rather than once per lower-timeframe candle.
    """
    completed: np.ndarray
    bb_period: int = 29
    bb_dev: float = 1.82
    kc_mult: float = 1.56
    slope_period: int = 11
    adx_period: int = 14

    def __post_init__(self) -> None:
        self.completed = _candles(self.completed)
        self.arrays = indicator_arrays(self.completed, bb_period=self.bb_period, bb_dev=self.bb_dev, kc_mult=self.kc_mult, slope_period=self.slope_period, adx_period=self.adx_period)

    def snapshot(self, forming: np.ndarray) -> dict[str, float]:
        return _single_from_tail(self.completed, self.arrays, forming, bb_period=self.bb_period, bb_dev=self.bb_dev, kc_mult=self.kc_mult, slope_period=self.slope_period, adx_period=self.adx_period)

    def complete(self, candle: np.ndarray) -> None:
        self.completed = np.vstack((self.completed, np.asarray(candle, dtype=np.float64).reshape(1, 6)))
        self.arrays = indicator_arrays(self.completed, bb_period=self.bb_period, bb_dev=self.bb_dev, kc_mult=self.kc_mult, slope_period=self.slope_period, adx_period=self.adx_period)


# Names used by the backtest driver.
indicator_snapshot = FormingAnchorEvaluator.snapshot
