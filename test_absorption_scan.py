import pytest

from absorption_scan import candidates


PROTOCOL = {
    "lookback_buckets": 3,
    "forward_horizon_seconds": 30,
    "delta_multiple_of_trailing_median_abs": 2.0,
    "max_range_multiple_of_trailing_median": 0.5,
    "min_relevant_top5_depth_multiple_of_trailing_median": 1.0,
}


def row(bucket, delta, high, low, bid=1000, ask=1000, mid=100):
    return {"symbol": "BTCUSDT", "bucket_received_ns": str(bucket), "delta_notional": str(delta),
            "high": str(high), "low": str(low), "close": "100", "mid": str(mid),
            "top5_bid_notional": str(bid), "top5_ask_notional": str(ask)}


def test_sell_absorption_is_long_candidate_with_forward_return_only_as_label():
    rows = [row(i * 15_000_000_000, -10 if i % 2 else 10, 101, 99) for i in range(3)]
    rows += [row(45_000_000_000, -30, 100.2, 99.8, bid=1200), row(75_000_000_000, 5, 101, 100, mid=101)]
    events = candidates(rows, PROTOCOL)
    assert len(events) == 1
    assert events[0]["side"] == "long"
    assert events[0]["forward_horizon_seconds"] == 30
    assert events[0]["forward_mid_return_bps"] == pytest.approx(100)
