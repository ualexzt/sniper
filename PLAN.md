# Bybit perpetual scalping research plan

## Status and boundary

This is a research and paper-trading project.  It must not submit authenticated
orders, store API secrets, or be treated as evidence that a strategy is
profitable.  The existing `BBSqueezeTrend` work remains an independent
historical baseline: its later validation slice was negative and it is not a
candidate for deployment.

## Frozen first hypothesis

Test a **trend-conditioned pullback** on `BTCUSDT` and `ETHUSDT` USDT-linear
perpetuals:

1. A 5-minute trend filter establishes a directional regime.
2. A 1-minute pullback followed by renewed aggressive trade flow is the entry
   hypothesis.
3. The simulator must price maker non-fills and taker emergency exits from
   captured order-book depth, not candle closes.
4. Every exit is reduce-only in paper execution; no averaging down and no
   martingale.

The numerical thresholds are deliberately *not* selected yet.  They will be
frozen once after inspecting a labelled development period and then evaluated
once on a later untouched period.

## Phases and gates

| Phase | Deliverable | Gate to continue |
|---|---|---|
| 1. Capture | Timestamped raw public L2 + trades, integrity manifest, gap report | Complete sessions, sequence resets recorded, no silent gaps |
| 2. Replay | Deterministic book reconstruction and fill simulator | Unit tests for snapshot/delta, stale book, partial and zero fills |
| 3. Develop | Fixed rule set and costs, BTC/ETH development report | Positive after maker/taker fees, spread and conservative fill assumptions |
| 4. Holdout | Unchanged-rule later-period report | Positive portfolio result with adequate sample size; no single-symbol dependence |
| 5. Paper | Live shadow signals and paper fills | Reconciliation of every signal, order attempt, fill, position and daily P&L |
| 6. Review | Risk and execution review | Explicit user approval is required before any authenticated/live capability |

## Cost and safety rules

- Use the account's actual fee tier when known; until then assume 2 bp maker
  and 5.5 bp taker per side, plus observed spread and adverse selection.
- Funding is charged from official timestamps; it is not ignored just because
  target holds are short.
- A strategy must survive a higher-slippage stress case and a zero-maker-fill
  case.  Rejected or missed signals are not losses and must stay visible.
- The recorder is public-data-only.  Its files are evidence, never a reason to
  infer that an order would have filled at the displayed price.

## First implementation slice

`bybit_recorder.py` records raw public Bybit linear `orderbook.50` and
`publicTrade` WebSocket events with local receive timestamps.  It is deliberately
separate from the existing candle backtest, so the old dataset and conclusions
cannot leak into selection of the new hypothesis.

## Rejected candle-only baseline — 2026-09-05

The pre-declared `trend_conditioned_pullback_development_v1` candidate set was
run on the 2025 development window with 5.5 bp fee and 5 bp slippage per side,
1 ATR stop, 1.2 ATR target, and 15-minute time stop.  All four variants were
negative after costs (net P&L from -9,407 to -9,997 USDT on a 10,000 USDT
simulation).  None is eligible for holdout, paper, or deployment.  The 2026
holdout was deliberately not run.

This means that candle-only trend plus EMA-pullback/reclaim is not sufficient
for our intended scalper.  The next hypothesis must be based on the captured
L2 state and trade flow, then first replayed with conservative maker-fill
assumptions.
