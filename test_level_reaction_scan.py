import numpy as np

from level_reaction_scan import outcome, scan, session_vwap


def test_session_vwap_excludes_current_bar():
    c = {"timestamp": np.array([0, 60_000, 120_000]),
         "volume": np.array([2.0, 1.0, 1.0]), "turnover": np.array([200.0, 110.0, 120.0])}
    result = session_vwap(c)
    assert np.isnan(result[0])
    assert result[1] == 100
    assert result[2] == 310 / 3


def test_forward_outcome_enters_at_next_open_and_is_symmetric():
    c = {"open": np.array([100.0, 101.0, 100.0]), "high": np.array([101.0, 103.0, 102.0]),
         "low": np.array([99.0, 99.0, 98.0]), "close": np.array([100.0, 102.0, 99.0])}
    long = outcome(c, 0, "long", [2])
    short = outcome(c, 0, "short", [2])
    assert long["entry"] == 101
    assert long["return_2m_bps"] < 0
    assert short["return_2m_bps"] > 0


def test_scan_uses_candle_row_count_not_mapping_key_count():
    n = 200
    start = 1_735_689_600_000
    close = np.full(n, 100.0)
    close[30] = 100.1
    high = close + 0.2
    low = close - 0.2
    low[30] = 99.9
    c = {"timestamp": start + np.arange(n, dtype=np.int64) * 60_000,
         "open": close.copy(), "high": high, "low": low,
         "close": close, "volume": np.ones(n), "turnover": close.copy()}
    protocol = {
        "data": {"development_start": "2025-01-01T00:00:00Z", "development_end_exclusive": "2025-01-02T00:00:00Z"},
        "forward_horizons_minutes": [5],
        "levels": {"confirmed_1h_swing_high_low": {"left_hours": 2, "right_hours": 2}},
        "event": {"dynamic_vwap_cooldown_minutes": 30, "max_penetration_atr": 1.0},
    }
    # Flat prior volume/price makes VWAP 100; the sweep happens well beyond
    # the seven dictionary keys and catches use of len(mapping) as row count.
    result = scan(c, protocol, "TEST")
    assert any(event["level_type"] == "session_vwap" and event["side"] == "long" for event in result)
