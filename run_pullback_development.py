#!/usr/bin/env python3
"""Run the frozen development-only pullback candidate matrix."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import numpy as np

import pullback_signals
from research_engine import run_backtest


ROOT = Path(__file__).resolve().parent


def ms(day: str) -> int:
    return int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp() * 1000)


def main() -> None:
    protocol = json.loads((ROOT / "pullback_development_protocol.json").read_text())
    c = np.load(ROOT / "data/candles.npz")
    f = np.load(ROOT / "data/funding.npz")
    start, end = ms(protocol["development"]["start"]), ms(protocol["development"]["end_exclusive"])
    # Cut exactly at development end: this is both faster and guards against a
    # future accidental non-causal signal implementation.
    mask = c["timestamp"] < end
    candles = {name: c[name][mask] for name in c.files}
    funding = {name: f[name] for name in f.files}
    rows = []
    for name, hp in protocol["candidates"].items():
        signals = pullback_signals.build_signals(candles, hp)
        x = protocol["execution"]
        result = run_backtest(
            candles, signals, funding, start, end, initial=10_000, leverage=1,
            sizing_mode="risk", fee=x["fee_per_side"],
            slippage_bps=x["slippage_bps_per_side"], risk_pct=x["risk_pct"],
            stop_atr_mult=x["stop_atr_mult"], target_atr_mult=x["target_atr_mult"],
            max_hold_minutes=x["max_hold_minutes"], trail_atr_mult=x["trail_atr_mult"],
        )
        trades = result["trades"]
        metrics = result["metrics"]
        rows.append({
            "candidate": name,
            "signals_long": int((signals["action"] == 1).sum()),
            "signals_short": int((signals["action"] == -1).sum()),
            "trades_long": sum(t["side"] == "long" for t in trades),
            "trades_short": sum(t["side"] == "short" for t in trades),
            "trade_count": metrics["trade_count"],
            "net_pnl": metrics["net_pnl"],
            "return_pct": metrics["final_equity"] / metrics["initial_equity"] - 1,
            "max_drawdown_pct": metrics["max_drawdown_pct"],
            "profit_factor": metrics["profit_factor"],
        })
    output = ROOT / "reports" / "pullback_development_2025.json"
    output.write_text(json.dumps({"protocol": protocol, "rows": rows}, indent=2) + "\n")
    for row in rows:
        print(json.dumps(row, sort_keys=True))
    print(f"wrote {output}")


if __name__ == "__main__":
    main()
