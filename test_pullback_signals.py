import numpy as np

import pullback_signals


def candles(n=700):
    ts = np.arange(n, dtype=np.int64) * 60_000
    # Smooth alternating sections create both trend and pullback conditions
    # without random future-dependent data.
    base = 100 + np.sin(np.arange(n) / 31) * 5 + np.arange(n) * 0.01
    close = base + np.sin(np.arange(n) / 3) * 0.8
    open_ = np.r_[close[0], close[:-1]]
    return {
        "timestamp": ts,
        "open": open_,
        "high": np.maximum(open_, close) + 0.4,
        "low": np.minimum(open_, close) - 0.4,
        "close": close,
        "volume": np.ones(n),
    }


def test_pullback_signals_are_prefix_invariant_and_signed():
    full = candles()
    cut = 550
    first = pullback_signals.build_signals({k: v[:cut] for k, v in full.items()})
    whole = pullback_signals.build_signals(full)
    for name in ("timestamp", "action", "anchor_ts"):
        assert np.array_equal(first[name], whole[name][:cut])
    assert np.allclose(first["atr"], whole["atr"][:cut], equal_nan=True)
    assert set(np.unique(whole["action"])).issubset({-1, 0, 1})
