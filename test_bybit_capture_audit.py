import pytest

from bybit_capture_audit import GAP_NS, audit_rows


def book(received_ns, kind, update):
    return {
        "received_ns": received_ns,
        "message": {
            "topic": "orderbook.50.BTCUSDT",
            "type": kind,
            "data": {"s": "BTCUSDT", "u": update},
        },
    }


def test_audit_accepts_snapshot_then_increasing_deltas():
    report = audit_rows([book(1, "snapshot", 100), book(2, "delta", 101)])
    assert report["pass"] is True
    assert report["orderbook_snapshots"] == {"orderbook.50.BTCUSDT:BTCUSDT": 1}


def test_audit_flags_gap_and_non_monotonic_delta():
    report = audit_rows([book(1, "snapshot", 100), book(1 + GAP_NS + 1, "delta", 100)])
    assert report["pass"] is False
    assert len(report["gaps_over_5_seconds"]) == 1
    assert report["non_monotonic_or_orphan_deltas"] == {"orderbook.50.BTCUSDT:BTCUSDT": 1}


def test_audit_rejects_malformed_orderbook_event():
    with pytest.raises(ValueError, match="without integer update id"):
        audit_rows([{"received_ns": 1, "message": {"topic": "orderbook.50.BTCUSDT", "data": {}}}])
