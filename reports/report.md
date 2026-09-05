# BBSqueezeTrend backtest report

Generated: 2026-09-05T14:02:14.955462Z

This is a custom NumPy/event simulator, not a native Jesse replay and not a proven live-trading result.

Data: Binance Vision USD-M ETHUSDT 1m candles and funding, checksum-verified.
Coverage: 2024-12-01T00:00:00Z to 2026-09-01T00:00:00Z exclusive.
`data/candles.npz` sha256: `f5b2f00c0e3b743e7222f1fca348941daa5852f4d8e7ef7e0859f6fa039ec0af`
`data/funding.npz` sha256: `23916a94442582b70b958cd6c51999995fe2f6f9d50b83807c02abe9337f7c8c`
`protocol.json` sha256: `1014e9cf7e4fbe7bdbd49126dd0c993cafa3a2c669c26fa62f46bffc75a77c4c`

## Primary comparison: published 2025 window, forming anchor, baseline 2 bps slippage

| TF | Final USDT | Return | DD | Trades | Win rate | PF | Fees | Funding |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 15m | 13,476.25 | 34.76% | 11.10% | 12 | 41.67% | 2.58 | 126.29 | 23.47 |
| 30m | 13,768.15 | 37.68% | 11.53% | 11 | 45.45% | 2.78 | 122.15 | 26.02 |
| 60m | 13,441.74 | 34.42% | 11.62% | 11 | 45.45% | 2.65 | 119.39 | 25.62 |
| 240m | 13,454.98 | 34.55% | 12.74% | 10 | 50.00% | 3.24 | 103.40 | 52.65 |

## Same window with closed-anchor-once semantics

| TF | Final USDT | Return | DD | Trades | Win rate | PF |
|---:|---:|---:|---:|---:|---:|---:|
| 15m | 14,135.29 | 41.35% | 12.82% | 10 | 50.00% | 3.50 |
| 30m | 13,603.70 | 36.04% | 13.50% | 10 | 40.00% | 2.87 |
| 60m | 13,999.54 | 40.00% | 11.69% | 10 | 50.00% | 3.43 |
| 240m | 13,568.36 | 35.68% | 11.10% | 10 | 50.00% | 3.29 |

## Later 2026 validation slice, baseline 2 bps

| Mode | TF | Final USDT | Return | DD | Trades | Win rate | PF |
|---|---:|---:|---:|---:|---:|---:|---:|
| closed_anchor_once | 15m | 9,563.59 | -4.36% | 5.33% | 2 | 0.00% | 0.00 |
| closed_anchor_once | 30m | 9,563.72 | -4.36% | 5.47% | 2 | 0.00% | 0.00 |
| closed_anchor_once | 60m | 9,563.48 | -4.37% | 5.48% | 2 | 0.00% | 0.00 |
| closed_anchor_once | 240m | 9,564.43 | -4.36% | 5.59% | 2 | 0.00% | 0.00 |
| forming_anchor | 15m | 9,570.80 | -4.29% | 5.64% | 2 | 0.00% | 0.00 |
| forming_anchor | 30m | 9,571.18 | -4.29% | 5.58% | 2 | 0.00% | 0.00 |
| forming_anchor | 60m | 9,569.56 | -4.30% | 5.66% | 2 | 0.00% | 0.00 |
| forming_anchor | 240m | 9,563.37 | -4.37% | 5.33% | 2 | 0.00% | 0.00 |

Full matrix is in `reports/summary.csv`; per-run trades are in `reports/trades/` and daily equity in `reports/equity/`.

Execution assumptions: 1x notional cap, 3% risk from initial ATR stop, 0.05% taker-like fee per side, 2 bps baseline and 10 bps stress slippage per side, stop checks on 1m OHLC, funding charged from historical funding rates using minute-open notional proxy.

Selection note: all TF results use the same frozen parameters. Picking the best TF after seeing this table is exploratory and needs a fresh untouched holdout or paper/live shadow before risking money.
