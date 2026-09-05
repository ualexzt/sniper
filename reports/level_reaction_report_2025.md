# Bybit level-reaction development report — 2025

This is a causal level-location diagnostic, not a strategy backtest and not
evidence that an order could fill.  It uses Bybit linear perpetual 1m candles;
DOM persistence, trade Delta, queue position, fees actually paid, and slippage
are not present in these historical candles.

## Frozen scope

- Symbols: BTCUSDT and ETHUSDT.
- Warmup: 2024-12-01 through 2025-01-01.
- Development: 2025-01-01 through 2026-01-01 exclusive.
- 2026 holdout was not downloaded or evaluated.
- Levels: previous UTC-day high/low, session VWAP using only prior completed
  minutes, and latest confirmed 1h swing high/low with two hours on each side.
- Event: a 1m candle trades beyond a pre-existing level and closes back through
  it; penetration cannot exceed 1.0 current 1m ATR.
- Forward entry proxy: next 1m open. Horizons: 5, 15, and 60 minutes.
- Static levels are consumed on their first sweep. Dynamic VWAP uses a
  30-minute same-side cooldown.

The primary event rules were fixed before the first output.  The matched-control
extension was specified after seeing the primary table but before running the
control output.  Controls match symbol, calendar month, UTC hour, and monthly
ATR quintile.  This staged timing means the control result is diagnostic, not
an untouched confirmation.

## Data integrity

Both files contain 570,240 contiguous minutes. The data manifest records:

- BTCUSDT SHA256: `d7aefaafbb43de44c83f97c668789c8739581cb8e6e8741bc0f851e2aafe9d7e`
- ETHUSDT SHA256: `fbd525094da41d12084ac3ebd537bb819d7d49de3aa69aa9a8dfc1066352a743`

The manifest preserves the protocol hash captured at download separately from
the final analysis-protocol hash. They differ because the matched-control
extension was added after the primary output, as disclosed above.

The scan produced 14,631 events and 14,631 matched pairs. Most events are VWAP
reclaims; static previous-day and confirmed-swing samples are much smaller.

## Main findings

### Session VWAP

VWAP reclaims do not show a usable standalone edge. Across BTC and ETH, long
and short median returns are approximately flat at 5 and 15 minutes, matched
excess confidence intervals include zero, and only about 20–38% clear the
7.5 bp maker-entry/taker-exit threshold at those horizons. VWAP is therefore a
context/location feature, not an entry trigger.

### BTC confirmed 1h swing levels

The most interesting row is a failed sweep above a confirmed 1h swing high:

- 174 short events.
- Median raw return: +3.08 bp at 5m and +4.98 bp at 15m — below the 7.5 bp
  fee threshold before slippage.
- At 60m, mean matched excess is +13.60 bp with bootstrap 95% interval
  `[+2.07, +24.99]`.

The 60-minute result is not a scalping result, varies materially by month, and
is one positive row among multiple comparisons. It is retained only as a
location candidate to condition with L2 absorption and reversal confirmation.

### Previous-day levels

ETH previous-day high produced 44 short events with median raw return +10.72 bp
at 15m and +19.93 bp at 60m. Its matched 60m excess interval is wide and crosses
zero (`[-15.09, +51.03]`), so the sample does not establish an edge.

ETH previous-day low is notably adverse for a blind long reversal: 56 events,
median raw return -17.79 bp at 15m and -9.86 bp at 60m. The matched 60m mean
excess is -62.12 bp with interval `[-114.60, -15.05]`. In development this is a
long veto or continuation hypothesis, not permission to trade it.

BTC previous-day high/low results are mixed and their intervals cross zero.

## Decision

No level is promoted as a standalone entry.  For the intended scalp horizon,
the level-only effect does not reliably clear costs.  The next replay must use
levels only to arm the state machine, then require:

1. sweep;
2. extreme aggressive Delta with low price efficiency;
3. persistent or replenishing relevant depth;
4. opposite Delta/CVD reversal plus microprice and quote-step confirmation.

BTC confirmed 1h swing highs and previous-day highs remain candidate short
locations. ETH previous-day lows should initially veto blind longs. These are
development observations and must not be read as frozen trading rules until
the combined order-flow definition is fixed and tested on untouched data.

## Statistical limits

Events and their forward windows overlap, so observations are serially
dependent and the simple paired bootstrap intervals may be too narrow. Control
matching does not fully match trend regime or distance to other levels.
Multiple rows were inspected. A future test needs block bootstrap, non-overlap
sensitivity, monthly breakdown, strict execution simulation, and an untouched
holdout.
