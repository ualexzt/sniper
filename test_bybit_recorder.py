from pathlib import Path

from bybit_recorder import output_path, topics


def test_topics_are_public_l2_and_trade_streams_only():
    assert topics(("BTCUSDT", "ETHUSDT")) == [
        "orderbook.50.BTCUSDT", "publicTrade.BTCUSDT",
        "allLiquidation.BTCUSDT", "orderbook.50.ETHUSDT",
        "publicTrade.ETHUSDT", "allLiquidation.ETHUSDT",
    ]
    assert all("private" not in topic for topic in topics(("BTCUSDT",)))


def test_output_path_stays_under_requested_root():
    root = Path("capture")
    result = output_path(root, "connection")
    assert result.parent == root
    assert result.name.startswith("bybit-linear-")
    assert result.name.endswith("-connection.jsonl")
