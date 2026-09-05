import numpy as np

from research_engine import run_backtest


def candles(rows):
    t, o, h, l, c = zip(*rows)
    return {"timestamp": np.array(t), "open": np.array(o, float),
            "high": np.array(h, float), "low": np.array(l, float),
            "close": np.array(c, float), "volume": np.ones(len(t))}


def sig(rows):
    t, a, atr, anchor, *close = zip(*rows)
    d = {"timestamp": np.array(t), "action": np.array(a),
         "atr": np.array(atr, float), "anchor_ts": np.array(anchor)}
    if close:
        d["close"] = np.array(close[0], float)
    return d


def empty_funding():
    return {"timestamp": np.array([], dtype=np.int64), "funding_rate": np.array([], float)}


def test_entry_uses_next_open_and_no_lookahead():
    c = candles([(0, 100, 101, 99, 100), (60_000, 110, 111, 109, 110), (120_000, 112, 113, 111, 112)])
    r = run_backtest(c, sig([(60_000, 1, 1, 7, 100)]), empty_funding(), 0, 120_000,
                     fee=0, slippage_bps=0)
    assert r["trades"][0]["entry_ts"] == 60_000
    assert r["trades"][0]["entry_price"] == 110


def test_long_and_short_gap_stops():
    c = candles([(0, 100, 101, 99, 100), (60_000, 90, 95, 89, 92),
                  (120_000, 110, 111, 109, 110), (180_000, 110, 111, 109, 110)])
    long_r = run_backtest(c, sig([(60_000, 1, 1, 1, 100)]), empty_funding(), 0, 180_000,
                              fee=0, slippage_bps=0)
    assert long_r["trades"][0]["exit_label"] == "stop"
    assert long_r["trades"][0]["exit_price"] == 90
    c2 = candles([(0, 100, 101, 99, 100), (60_000, 110, 111, 105, 109),
                   (120_000, 90, 91, 89, 90), (180_000, 90, 91, 89, 90)])
    short_r = run_backtest(c2, sig([(60_000, -1, 1, 2, 100)]), empty_funding(), 0, 180_000,
                               fee=0, slippage_bps=0)
    assert short_r["trades"][0]["exit_price"] == 110


def test_funding_sign_and_trail_is_not_retroactive():
    c = candles([(0, 100, 101, 99, 100), (60_000, 100, 105, 99, 104),
                  (120_000, 104, 106, 95, 100), (180_000, 100, 101, 99, 100)])
    f = {"timestamp": np.array([120_000]), "funding_rate": np.array([.01])}
    r = run_backtest(c, sig([(60_000, 1, 1, 3, 100), (120_000, 0, 1, 4, 100)]), f, 0, 180_000,
                          fee=0, slippage_bps=0)
    # At 104 close, trail is 100.29 and becomes active at 120000.  The 95 low
    # therefore stops it at the next bar, rather than retroactively at 120000.
    assert r["trades"][0]["exit_label"] == "stop"
    assert np.isclose(r["metrics"]["funding"], r["trades"][0]["qty"] * 104 * .01)


def test_anchor_consumed_and_equity_identity():
    c = candles([(0, 100, 101, 99, 100), (60_000, 100, 101, 99, 100),
                  (120_000, 100, 101, 99, 100), (180_000, 110, 111, 109, 110)])
    # Same anchor appears twice; only the first entry is consumed.
    r = run_backtest(c, sig([(60_000, 1, 1, 5, 100), (120_000, 1, 1, 5, 100)]), empty_funding(), 0, 180_000,
                          fee=0, slippage_bps=0)
    assert len(r["trades"]) == 1
    assert abs(r["metrics"]["final_equity"] - (r["metrics"]["initial_equity"] + r["metrics"]["net_pnl"])) < 1e-8


def test_target_and_time_stop_are_explicit_and_causal():
    c = candles([(0, 100, 100, 100, 100), (60_000, 100, 102, 99, 101),
                 (120_000, 101, 101, 100, 100), (180_000, 100, 100, 99, 99)])
    target = run_backtest(c, sig([(60_000, 1, 1, 8, 100)]), empty_funding(), 0, 180_000,
                          fee=0, slippage_bps=0, stop_atr_mult=2, trail_atr_mult=None,
                          target_atr_mult=1)
    assert target["trades"][0]["exit_label"] == "target"
    assert target["trades"][0]["exit_price"] == 101

    timed = run_backtest(c, sig([(60_000, 1, 1, 9, 100)]), empty_funding(), 0, 180_000,
                         fee=0, slippage_bps=0, stop_atr_mult=5, trail_atr_mult=None,
                         max_hold_minutes=1)
    assert timed["trades"][0]["exit_label"] == "time_stop"
    assert timed["trades"][0]["exit_ts"] == 120_000
