# Order-flow scalp entry algorithm v1

Status: research specification.  It defines causal candidate events for replay
and paper evaluation; it is not approved for authenticated or live orders.

## Core idea

Trade a failed liquidity sweep at an objective level only after aggressive
flow is absorbed and then reverses.  Long and short rules are exact mirrors.
No single indicator, DOM wall, CVD divergence, or liquidation is an entry by
itself.

## Causal levels

Levels are calculated only from information available before the event:

1. Previous UTC day high/low and previous completed 1h/15m swing high/low.
2. Session VWAP plus volume-profile POC, VAH, and VAL from observed trades.
3. Equal-high/equal-low liquidity pools formed by at least two prior pivots.
4. Persistent L2 zones: depth that remains or replenishes while traded volume
   executes at that price area.

Automated diagonal trend lines are an optional development feature only.  A
line must be fitted from confirmed past pivots and projected forward without
redrawing history.  It cannot qualify an entry unless one of the objective
levels above is also present.

## State machine

`IDLE -> ARMED -> SWEPT -> ABSORBING -> TRIGGERED -> ORDER_ATTEMPT ->
PAPER_POSITION -> EXIT`

- `ARMED`: price enters a volatility-normalized neighbourhood of a causal
  level.  Spread, book age, data continuity, and volatility gates are healthy.
- `SWEPT`: trades or mid cross beyond the level, taking an earlier high/low.
- `ABSORBING`: aggressive flow continues beyond its rolling extreme but price
  efficiency collapses; relevant passive depth persists or replenishes.
- `TRIGGERED`: aggressive Delta reverses, microprice and best quotes move away
  from the swept side, and the level is reclaimed.
- `ORDER_ATTEMPT`: simulate a post-only order with finite TTL.  An unfilled or
  cancelled attempt is `MISSED`, never a trade or loss.
- `PAPER_POSITION`: exits are evaluated from executable book prices, including
  fees and slippage.

Every transition has a maximum age.  Expiry or stale/gapped data returns the
machine to `IDLE` without inventing a fill.

## LONG event

1. **Location:** price approaches support: prior low, VAL/POC/VWAP, equal lows,
   or a validated persistent bid zone.
2. **Sweep:** traded price moves below the level and then reclaims it.  Sweep
   distance and duration are normalized by tick size and short realized
   volatility.
3. **Sell absorption:** sell-taker notional is extreme relative to its trailing
   distribution, while downward mid-price movement per unit of sell notional
   is unusually small.  Bid liquidity at/behind the level persists or
   replenishes after executions.
4. **Trigger:** short-horizon Delta changes positive, CVD slope turns upward,
   microprice rises above mid, best bid steps up, and the reclaimed level holds.
5. **Attempt:** post-only buy at the best bid or one tick behind it.  Cancel on
   trigger invalidation or TTL; do not chase repeatedly.

## SHORT event

Exact mirror: resistance location, sweep above the level, extreme buy-taker
flow with weak upward response and persistent/replenishing asks, then negative
Delta/CVD reversal, falling microprice, lower best ask, and a post-only sell.

## Features to compute

- Trade flow: signed notional Delta over 1s/5s/15s/60s, rolling CVD slopes,
  footprint price bins, trade rate, and largest-trade concentration.
- Price response: mid/trade displacement per signed notional, realized
  volatility, sweep distance, reclaim distance, and time since sweep.
- Book: spread, microprice, top 1/5/10-level imbalance, order-flow imbalance
  from quote changes, depletion, replenishment, quote step direction, and
  book age.
- Context: causal VWAP/profile/swing levels, time of day, liquidation bursts,
  and 5-minute open-interest change.

Use rolling medians, MADs, and quantiles per symbol instead of fixed absolute
volume thresholds.  BTC and ETH must be evaluated separately as well as in a
combined portfolio.

## Execution and economics gate

With the default non-VIP assumptions, a maker entry plus taker exit costs about
7.5 bp before slippage; two taker sides cost about 11 bp.  A candidate whose
expected gross move does not clear all costs plus a safety margin is rejected,
even if its directional hit rate is high.

Primary execution cases:

1. `strict_queue`: fill only after displayed quantity ahead is conservatively
   consumed by eligible trades; cancellations ahead do not automatically grant
   a fill.
2. `zero_fill_unknown`: ambiguous queue state produces no trade.
3. `optimistic_touch`: diagnostic upper bound only, never the headline result.

## Exit and risk

- Initial invalidation is beyond the sweep extreme plus a small volatility and
  tick buffer, evaluated against executable prices.
- Early exit when the absorption level fails, opposite flow accelerates, the
  book becomes stale, or spread jumps beyond the allowed regime.
- Profit target is the next causal liquidity zone.  Partial exits are tested
  only after a one-exit baseline passes development.
- Time stop if expected displacement does not begin promptly.
- No averaging down, martingale, simultaneous opposing positions, or leverage
  optimization during signal research.

## Promotion rule

First measure candidate frequency and 15s/30s/60s/180s forward executable
returns without trading.  Freeze one development rule before opening the
holdout.  It must remain positive after fees, strict fill assumptions, missed
orders, long/short breakdown, and stress costs before paper trading begins.
