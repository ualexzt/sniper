import json
from pathlib import Path

from l2_features import build_features


def row(received_ns, message):
    return json.dumps({"received_ns": received_ns, "connection_id": "x", "message": message})


def test_snapshot_delta_and_signed_trade_features(tmp_path: Path):
    path = tmp_path / "capture.jsonl"
    path.write_text("\n".join([
        row(1, {"topic": "orderbook.50.BTCUSDT", "type": "snapshot", "cts": 1,
                "data": {"s": "BTCUSDT", "b": [["100", "2"]], "a": [["101", "3"]]}}),
        row(2, {"topic": "publicTrade.BTCUSDT", "data": [
            {"s": "BTCUSDT", "p": "101", "v": "1", "S": "Buy"}]}),
        row(3, {"topic": "orderbook.50.BTCUSDT", "type": "delta", "cts": 3,
                "data": {"s": "BTCUSDT", "b": [["100", "0"], ["99", "4"]], "a": []}}),
        row(4, {"topic": "publicTrade.BTCUSDT", "data": [
            {"s": "BTCUSDT", "p": "100", "v": "0.5", "S": "Sell"}]}),
    ]) + "\n")
    features = build_features(path, interval_ns=15)
    assert len(features) == 1
    out = features[0]
    assert out["trade_count"] == 2
    assert out["buy_notional"] == 101
    assert out["sell_notional"] == 50
    assert out["delta_notional"] == 51
    assert out["cvd_notional"] == 51
    assert out["mid"] == 100
    assert out["spread_bps"] == 200
