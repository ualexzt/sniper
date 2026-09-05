#!/usr/bin/env python3
"""Run the frozen BBSqueezeTrend research matrix and write reports."""
from __future__ import annotations

import csv
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

import signals
from research_engine import run_backtest


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
REPORT_DIR = ROOT / "reports"
EQUITY_DIR = REPORT_DIR / "equity"
TRADES_DIR = REPORT_DIR / "trades"


def dt_ms(day: str) -> int:
    return int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp() * 1000)


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_npz(path: Path) -> dict[str, np.ndarray]:
    data = np.load(path)
    return {name: data[name] for name in data.files}


def check_candles(c: dict[str, np.ndarray]) -> None:
    ts = c["timestamp"].astype(np.int64)
    if len(ts) == 0:
        raise RuntimeError("empty candle data")
    if not np.all(np.diff(ts) == 60_000):
        raise RuntimeError("1m candle data is not contiguous")
    for name in ("open", "high", "low", "close", "volume"):
        if not np.all(np.isfinite(c[name])):
            raise RuntimeError(f"{name} contains non-finite values")
    if np.any(c["open"] <= 0) or np.any(c["high"] <= 0) or np.any(c["low"] <= 0) or np.any(c["close"] <= 0):
        raise RuntimeError("non-positive OHLC price")
    if np.any(c["volume"] < 0):
        raise RuntimeError("negative volume")
    if np.any(c["high"] < np.maximum.reduce([c["open"], c["low"], c["close"]])):
        raise RuntimeError("invalid high")
    if np.any(c["low"] > np.minimum.reduce([c["open"], c["high"], c["close"]])):
        raise RuntimeError("invalid low")


def pct(x: float) -> str:
    if math.isinf(x):
        return "inf"
    return f"{100*x:.2f}%"


def money(x: float) -> str:
    return f"{x:,.2f}"


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    REPORT_DIR.mkdir(exist_ok=True)
    EQUITY_DIR.mkdir(exist_ok=True)
    TRADES_DIR.mkdir(exist_ok=True)

    protocol = json.loads((ROOT / "protocol.json").read_text(encoding="utf-8"))
    candles = load_npz(DATA_DIR / "candles.npz")
    funding = load_npz(DATA_DIR / "funding.npz")
    check_candles(candles)

    hp = protocol["parameters"]
    data_start = int(candles["timestamp"][0])
    data_end = int(candles["timestamp"][-1]) + 60_000
    target_end = dt_ms(protocol["target_data_end_exclusive"][:10])
    if data_end < target_end:
        raise RuntimeError(f"data ends at {iso(data_end)}, target is {iso(target_end)}")

    all_rows: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []
    signal_cache: dict[tuple[int, str], dict[str, np.ndarray]] = {}

    for tf in protocol["trading_timeframes_minutes"]:
        for mode in protocol["signal_modes"]:
            key = (int(tf), str(mode))
            signal_cache[key] = signals.build_signals(candles, int(tf), protocol["anchor_timeframe_minutes"], hp, str(mode))

    for period in protocol["periods"]:
        start_ms = dt_ms(period["start"])
        end_ms = dt_ms(period["end"])
        if start_ms < data_start or end_ms > data_end:
            raise RuntimeError(f"period {period['name']} outside data coverage")
        for tf in protocol["trading_timeframes_minutes"]:
            for mode in protocol["signal_modes"]:
                sig = signal_cache[(int(tf), str(mode))]
                for label, slip in (("baseline_2bps", protocol["execution"]["base_slippage_bps_per_side"]),
                                    ("stress_10bps", protocol["execution"]["stress_slippage_bps_per_side"])):
                    result = run_backtest(
                        candles,
                        sig,
                        funding,
                        start_ms,
                        end_ms,
                        fee=float(protocol["execution"]["fee_per_side"]),
                        slippage_bps=float(slip),
                        initial=float(protocol["execution"]["initial_balance_usdt"]),
                        risk_pct=float(protocol["execution"]["risk_fraction"]),
                        leverage=float(protocol["execution"]["leverage"]),
                        sizing_mode="jesse",
                    )
                    metrics = result["metrics"]
                    stem = f"{period['name']}_{tf}m_{mode}_{label}"
                    write_csv(TRADES_DIR / f"{stem}.csv", result["trades"])
                    write_csv(EQUITY_DIR / f"{stem}.csv", result["equity"])
                    row = {
                        "period": period["name"],
                        "start": period["start"],
                        "end": period["end"],
                        "tf_minutes": int(tf),
                        "signal_mode": mode,
                        "cost_case": label,
                        "final_equity": metrics["final_equity"],
                        "return_pct": metrics["final_equity"] / metrics["initial_equity"] - 1,
                        "net_pnl": metrics["net_pnl"],
                        "gross_pnl": metrics["gross_pnl"],
                        "fees": metrics["fees"],
                        "funding": metrics["funding"],
                        "max_drawdown_pct": metrics["max_drawdown_pct"],
                        "trade_count": metrics["trade_count"],
                        "win_rate": metrics["win_rate"],
                        "profit_factor": metrics["profit_factor"],
                        "forced_count": metrics["forced_count"],
                        "best_trade_net_share": metrics["best_trade_net_share"],
                    }
                    all_rows.append(row)
                    details.append({"row": row, "assumptions": result["assumptions"]})

    write_csv(REPORT_DIR / "summary.csv", all_rows)
    (REPORT_DIR / "details.json").write_text(json.dumps(details, indent=2, sort_keys=True), encoding="utf-8")

    baseline = [r for r in all_rows if r["cost_case"] == "baseline_2bps"]
    primary = [r for r in baseline if r["period"] == "2025_published_window" and r["signal_mode"] == "forming_anchor"]
    primary.sort(key=lambda r: r["tf_minutes"])
    closed = [r for r in baseline if r["period"] == "2025_published_window" and r["signal_mode"] == "closed_anchor_once"]
    closed.sort(key=lambda r: r["tf_minutes"])
    later = [r for r in baseline if r["period"] == "2026_later_validation"]
    later.sort(key=lambda r: (r["signal_mode"], r["tf_minutes"]))

    lines: list[str] = []
    lines.append("# BBSqueezeTrend backtest report")
    lines.append("")
    lines.append(f"Generated: {datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')}")
    lines.append("")
    lines.append("This is a custom NumPy/event simulator, not a native Jesse replay and not a proven live-trading result.")
    lines.append("")
    lines.append("Data: Binance Vision USD-M ETHUSDT 1m candles and funding, checksum-verified.")
    lines.append(f"Coverage: {iso(data_start)} to {iso(data_end)} exclusive.")
    lines.append(f"`data/candles.npz` sha256: `{file_sha256(DATA_DIR / 'candles.npz')}`")
    lines.append(f"`data/funding.npz` sha256: `{file_sha256(DATA_DIR / 'funding.npz')}`")
    lines.append(f"`protocol.json` sha256: `{file_sha256(ROOT / 'protocol.json')}`")
    lines.append("")
    lines.append("## Primary comparison: published 2025 window, forming anchor, baseline 2 bps slippage")
    lines.append("")
    lines.append("| TF | Final USDT | Return | DD | Trades | Win rate | PF | Fees | Funding |")
    lines.append("|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in primary:
        lines.append(f"| {r['tf_minutes']}m | {money(r['final_equity'])} | {pct(r['return_pct'])} | {pct(r['max_drawdown_pct'])} | {r['trade_count']} | {pct(r['win_rate'])} | {r['profit_factor']:.2f} | {money(r['fees'])} | {money(r['funding'])} |")
    lines.append("")
    lines.append("## Same window with closed-anchor-once semantics")
    lines.append("")
    lines.append("| TF | Final USDT | Return | DD | Trades | Win rate | PF |")
    lines.append("|---:|---:|---:|---:|---:|---:|---:|")
    for r in closed:
        lines.append(f"| {r['tf_minutes']}m | {money(r['final_equity'])} | {pct(r['return_pct'])} | {pct(r['max_drawdown_pct'])} | {r['trade_count']} | {pct(r['win_rate'])} | {r['profit_factor']:.2f} |")
    lines.append("")
    lines.append("## Later 2026 validation slice, baseline 2 bps")
    lines.append("")
    lines.append("| Mode | TF | Final USDT | Return | DD | Trades | Win rate | PF |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for r in later:
        lines.append(f"| {r['signal_mode']} | {r['tf_minutes']}m | {money(r['final_equity'])} | {pct(r['return_pct'])} | {pct(r['max_drawdown_pct'])} | {r['trade_count']} | {pct(r['win_rate'])} | {r['profit_factor']:.2f} |")
    lines.append("")
    lines.append("Full matrix is in `reports/summary.csv`; per-run trades are in `reports/trades/` and daily equity in `reports/equity/`.")
    lines.append("")
    lines.append("Execution assumptions: 1x notional cap, 3% risk from initial ATR stop, 0.05% taker-like fee per side, 2 bps baseline and 10 bps stress slippage per side, stop checks on 1m OHLC, funding charged from historical funding rates using minute-open notional proxy.")
    lines.append("")
    lines.append("Selection note: all TF results use the same frozen parameters. Picking the best TF after seeing this table is exploratory and needs a fresh untouched holdout or paper/live shadow before risking money.")
    (REPORT_DIR / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"wrote {REPORT_DIR / 'summary.csv'}")
    print(f"wrote {REPORT_DIR / 'report.md'}")


if __name__ == "__main__":
    main()
