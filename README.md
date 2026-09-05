# BBSqueezeTrend Research Harness

The current Bybit order-flow research entry specification is documented in
[`ENTRY_ALGORITHM.md`](ENTRY_ALGORITHM.md).  It remains public-data-only and is
not a live trading system.

The first causal historical level-location diagnostic is summarized in
[`reports/level_reaction_report_2025.md`](reports/level_reaction_report_2025.md).

Custom reproducible backtest harness for the Jesse `BBSqueezeTrend` strategy from:

https://jesse.trade/strategies/bbsqueezetrend

This repository does not run native Jesse. It uses checksum-verified Binance Vision ETHUSDT USD-M 1m candles and funding rates, NumPy indicator implementations audited against the downloaded Jesse source snapshot, and a small event-driven simulator with explicit execution assumptions.

## Reproduce

```bash
python3 fetch_sources.py
python3 download_data.py
python3 -m pytest -q
python3 run_suite.py
```

Outputs:

- `reports/report.md`: compact human report.
- `reports/summary.csv`: full matrix across periods, timeframes, signal modes, and cost cases.
- `reports/trades/`: per-run trade logs.
- `reports/equity/`: per-run daily equity snapshots.

## Frozen Matrix

- Market: Binance USD-M ETHUSDT perpetual.
- Data: 2024-12-01 through 2026-09-01 exclusive.
- Tested periods: 2025 calendar year, 2026-01-01 through 2026-07-17, and 2026-07-17 through 2026-09-01.
- Trading timeframes: 15m, 30m, 60m, 240m.
- Anchor timeframe: 240m.
- Signal modes: `forming_anchor` and `closed_anchor_once`.
- Costs: 0.05% fee per side, plus 2 bps baseline or 10 bps stress slippage per side.
- Sizing: 10,000 USDT flat start per run, 1x notional cap, 3% risk from the initial ATR stop, quantity floored to 0.001 ETH.

## Limits

This is a research simulator, not a live trading system and not an exact replay of the author's unknown Jesse version, exchange route, leverage, warmup, and execution config. Stops use 1m OHLC, funding uses historical funding rates with minute-open notional as a proxy, and order-book queue/depth, latency, liquidation mechanics, and exact mark-price funding are not simulated.

Selecting the best row after seeing `reports/summary.csv` consumes this dataset. Any selected variant needs a fresh untouched holdout or paper/live shadow before risking capital.
