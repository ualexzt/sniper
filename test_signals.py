import numpy as np

import signals


HP = {
    "bb_period": 29,
    "bb_dev": 1.82,
    "kc_mult": 1.56,
    "slope_period": 11,
    "adx_threshold": 19.11,
    "min_squeeze_bars": 4,
    "atr_stop_mult": 1.74,
    "atr_trail_mult": 3.71,
}


def synthetic_minutes(n=20000):
    ts = np.arange(n, dtype=np.int64) * 60_000
    base = 2000 + np.sin(np.arange(n) / 200) * 30 + np.arange(n) * 0.002
    open_ = base
    close = base + np.sin(np.arange(n) / 11) * 2
    high = np.maximum(open_, close) + 3
    low = np.minimum(open_, close) - 3
    return {
        "timestamp": ts,
        "open": open_.astype(float),
        "high": high.astype(float),
        "low": low.astype(float),
        "close": close.astype(float),
        "volume": np.ones(n, dtype=float),
    }


def prefix(d, n):
    return {k: v[:n].copy() for k, v in d.items()}


def test_forming_signals_do_not_change_when_future_is_appended():
    full = synthetic_minutes()
    cut = 12000
    a = signals.build_signals(prefix(full, cut), 15, 240, HP, "forming_anchor")
    b = signals.build_signals(full, 15, 240, HP, "forming_anchor")
    mask = b["timestamp"] <= int(full["timestamp"][cut - 1]) + 60_000
    assert np.array_equal(a["timestamp"], b["timestamp"][mask])
    assert np.array_equal(a["action"], b["action"][mask])
    assert np.allclose(a["atr"], b["atr"][mask], equal_nan=True)


def test_closed_anchor_entries_are_delayed_to_next_trade_close():
    data = synthetic_minutes(8000)
    out = signals.build_signals(data, 60, 240, HP, "closed_anchor_once")
    acted = np.flatnonzero(out["anchor_ts"] >= 0)
    assert len(acted) > 0
    for j in acted[:200]:
        assert out["timestamp"][j] == out["anchor_ts"][j] + 240 * 60_000 + 60 * 60_000
