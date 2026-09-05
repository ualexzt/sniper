"""Small, deterministic event driven backtester for one minute OHLC data.

The engine deliberately has no exchange or market-data integration.  Timestamps
are minute boundaries: a signal at ``t`` is known at the close immediately
before the bar whose open is ``t`` and, consequently, can enter at that open.
This convention is useful when the signal producer timestamps its close event
at the following minute boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Dict, List, Mapping, Optional

import numpy as np


_MINUTE = 60_000
_SLIP_EXIT = 1.0  # retained as a named constant to make execution assumptions clear


def _array(data: Mapping[str, Any], name: str, *, dtype: Any = float,
           required: bool = True) -> Optional[np.ndarray]:
    if name not in data:
        if required:
            raise ValueError(f"missing {name}")
        return None
    a = np.asarray(data[name], dtype=dtype)
    if a.ndim != 1:
        raise ValueError(f"{name} must be a one-dimensional array")
    return a


def _check_same_length(data: Mapping[str, Any], names: List[str]) -> int:
    lengths = {len(data[n]) for n in names if n in data}
    if not lengths or len(lengths) != 1:
        raise ValueError("input arrays must have equal lengths")
    return lengths.pop()


def _finite(a: np.ndarray, name: str) -> None:
    if not np.all(np.isfinite(a)):
        raise ValueError(f"{name} contains non-finite values")


def _floor_qty(qty: float) -> float:
    # The venue's smallest useful size is one thousandth.  Flooring rather
    # than rounding keeps a risk cap conservative and avoids oversizing.
    if not math.isfinite(qty) or qty < 0.001:
        return 0.0
    return math.floor(qty * 1000.0 + 1e-10) / 1000.0


@dataclass
class _Position:
    side: int
    qty: float
    entry_ts: int
    entry_signal_ts: int
    entry_price: float
    signal_close: float
    initial_stop: float
    stop: float
    target: float | None = None
    funding: float = 0.0
    entry_fee: float = 0.0
    high_close: float = 0.0
    low_close: float = 0.0
    latest_atr: float = 0.0


def run_backtest(
    candles: Mapping[str, Any],
    signals: Mapping[str, Any],
    funding: Mapping[str, Any],
    start_ms: int,
    end_ms: int,
    fee: float = 0.0005,
    slippage_bps: float = 2,
    initial: float = 10_000,
    risk_pct: float = 0.03,
    leverage: float = 1,
    sizing_mode: str = "jesse",
    stop_atr_mult: float = 1.74,
    trail_atr_mult: float | None = 3.71,
    target_atr_mult: float | None = None,
    max_hold_minutes: float | None = None,
    **_: Any,
) -> Dict[str, Any]:
    """Run a long/short one-position OHLC backtest.

    Signals are looked up by their timestamp.  Their ``close`` (or
    ``signal_close``) is the close used to calculate the planned stop and
    sizing; the current candle's high/low/close is never used to set an entry
    price.  Missing signal close values fall back to the preceding candle's
    close, preserving the no-lookahead rule.
    """
    if int(start_ms) != start_ms or int(end_ms) != end_ms or end_ms < start_ms:
        raise ValueError("invalid start_ms/end_ms")
    start_ms, end_ms = int(start_ms), int(end_ms)
    for name, value in (("fee", fee), ("slippage_bps", slippage_bps),
                        ("initial", initial), ("risk_pct", risk_pct),
                        ("leverage", leverage), ("stop_atr_mult", stop_atr_mult)):
        if not math.isfinite(float(value)):
            raise ValueError(f"{name} must be finite")
    if fee < 0 or slippage_bps < 0 or initial <= 0 or risk_pct <= 0 or leverage <= 0:
        raise ValueError("invalid fee, capital, risk, or leverage")
    if stop_atr_mult <= 0:
        raise ValueError("stop_atr_mult must be positive")
    for name, value in (("trail_atr_mult", trail_atr_mult),
                        ("target_atr_mult", target_atr_mult),
                        ("max_hold_minutes", max_hold_minutes)):
        if value is not None and (not math.isfinite(float(value)) or float(value) <= 0):
            raise ValueError(f"{name} must be positive when set")
    if sizing_mode not in {"jesse", "risk"}:
        raise ValueError("sizing_mode must be 'jesse' or 'risk'")

    cnames = ["timestamp", "open", "high", "low", "close", "volume"]
    _check_same_length(candles, cnames)
    ct = _array(candles, "timestamp", dtype=np.int64)
    co = _array(candles, "open")
    ch = _array(candles, "high")
    cl = _array(candles, "low")
    cc = _array(candles, "close")
    cv = _array(candles, "volume")
    assert ct is not None and co is not None and ch is not None and cl is not None and cc is not None and cv is not None
    if len(ct) == 0:
        raise ValueError("candles are empty")
    _finite(co, "open"); _finite(ch, "high"); _finite(cl, "low"); _finite(cc, "close"); _finite(cv, "volume")
    if np.any(np.diff(ct) <= 0) or np.any(ct % _MINUTE != 0):
        raise ValueError("candle timestamps must be strictly increasing minute boundaries")
    if np.any(co <= 0) or np.any(ch <= 0) or np.any(cl <= 0) or np.any(cc <= 0) or np.any(cv < 0):
        raise ValueError("prices must be positive and volume non-negative")
    if np.any(ch < np.maximum.reduce([co, cl, cc])) or np.any(cl > np.minimum.reduce([co, ch, cc])):
        raise ValueError("invalid OHLC values")

    snames = ["timestamp", "action", "atr", "anchor_ts"]
    _check_same_length(signals, snames)
    st = _array(signals, "timestamp", dtype=np.int64)
    sa = _array(signals, "action", dtype=np.int64)
    satr = _array(signals, "atr")
    san = _array(signals, "anchor_ts", dtype=np.int64)
    assert st is not None and sa is not None and satr is not None and san is not None
    # Warmup signal rows may carry NaN ATR.  They are ignored unless they are
    # actionable; the row-level check happens when an entry is considered.
    if np.any((sa < -1) | (sa > 1)):
        raise ValueError("actions must be -1, 0, or 1")
    if np.any(np.diff(st) < 0):
        raise ValueError("signal timestamps must be ordered")
    signal_close = _array(signals, "close")
    if signal_close is None:
        signal_close = _array(signals, "signal_close", required=False)
    if signal_close is not None:
        if np.any(np.isinf(signal_close)):
            raise ValueError("signal close contains infinity")
    entry_atr = _array(signals, "entry_atr", required=False)
    if entry_atr is not None:
        if np.any(np.isinf(entry_atr)):
            raise ValueError("entry ATR contains infinity")

    # Funding is allowed to contain events between bars.  Each event is
    # applied on the first minute boundary at/after its timestamp.
    _check_same_length(funding, ["timestamp", "funding_rate"])
    ft = _array(funding, "timestamp", dtype=np.int64)
    fr = _array(funding, "funding_rate")
    assert ft is not None and fr is not None
    _finite(fr, "funding_rate")
    if np.any(np.diff(ft) < 0):
        raise ValueError("funding timestamps must be ordered")

    first = int(np.searchsorted(ct, start_ms, side="left"))
    # The run interval is [start_ms, end_ms): a bar stamped at end_ms is out.
    last = int(np.searchsorted(ct, end_ms, side="left"))
    if first >= last:
        raise ValueError("no candles in requested range")
    # Keep all events in the requested candle range.  A signal gets the next
    # open if it falls on a boundary not explicitly represented by candles.
    bar_t = ct[first:last]; bar_o = co[first:last]; bar_h = ch[first:last]
    bar_l = cl[first:last]; bar_c = cc[first:last]
    bar_v = cv[first:last]
    index = {int(t): i for i, t in enumerate(bar_t)}

    # Map signal rows to execution bars.  A signal at a minute boundary is
    # executable at that exact open; an off-grid signal uses the next open.
    by_bar: Dict[int, List[int]] = {}
    for j, ts in enumerate(st):
        if ts < start_ms or ts >= end_ms:
            continue
        k = int(np.searchsorted(bar_t, ts, side="left"))
        if k < len(bar_t):
            by_bar.setdefault(k, []).append(j)

    funding_by_bar: Dict[int, List[int]] = {}
    for j, ts in enumerate(ft):
        if ts < start_ms or ts >= end_ms:
            continue
        # Funding timestamps can carry a few milliseconds of exchange clock
        # skew.  Charge them in the minute containing the event, using that
        # minute's open as the documented notional proxy.
        k = int(np.searchsorted(bar_t, ts, side="right")) - 1
        if ts < bar_t[0] or ts >= bar_t[-1] + _MINUTE:
            k = -1
        if k < len(bar_t):
            if k >= 0:
                funding_by_bar.setdefault(k, []).append(j)

    slip = float(slippage_bps) / 10_000.0
    cash = float(initial)
    pos: Optional[_Position] = None
    trades: List[Dict[str, Any]] = []
    consumed: set[int] = set()
    gross_total = fees_total = funding_total = 0.0
    equity_points: List[Dict[str, float]] = []
    close_peak = float(initial)
    max_dd = 0.0

    def mark(price: float) -> float:
        return cash if pos is None else cash + pos.side * pos.qty * (price - pos.entry_price)

    def close_position(ts: int, exit_price: float, label: str) -> None:
        nonlocal pos, cash, gross_total, fees_total
        assert pos is not None
        gross = pos.side * pos.qty * (exit_price - pos.entry_price)
        exit_fee = abs(exit_price * pos.qty) * fee
        net = gross - pos.entry_fee - exit_fee - pos.funding
        cash += gross - exit_fee
        gross_total += gross
        fees_total += pos.entry_fee + exit_fee
        trades.append({
            "side": "long" if pos.side > 0 else "short",
            "qty": pos.qty,
            "entry_ts": pos.entry_ts,
            "exit_ts": int(ts),
            "entry_price": pos.entry_price,
            "exit_price": exit_price,
            "initial_stop": pos.initial_stop,
            "exit_label": label,
            "gross_pnl": gross,
            "fees": pos.entry_fee + exit_fee,
            "funding": pos.funding,
            "net_pnl": net,
            "hold_minutes": (int(ts) - pos.entry_ts) / 60_000.0,
        })
        pos = None

    for i, ts0 in enumerate(bar_t):
        ts = int(ts0)
        carried = pos is not None
        # Funding is charged before any entry and before this bar's stop.
        if carried:
            for j in funding_by_bar.get(i, []):
                charge = pos.side * float(fr[j]) * pos.qty * float(bar_o[i])
                cash -= charge
                pos.funding += charge
                funding_total += charge

        # A trailing update is caused only by a completed trading-bar signal.
        # It happens before this boundary's entry and intrabar stop checks, but
        # a newly opened position does not consume the same close as a trail.
        if pos is not None and trail_atr_mult is not None:
            for j in by_bar.get(i, []):
                trail_close = (float(signal_close[j]) if signal_close is not None
                               else (float(bar_c[i - 1]) if i > 0 else math.nan))
                trail_atr = float(satr[j])
                if not (math.isfinite(trail_close) and trail_close > 0 and
                        math.isfinite(trail_atr) and trail_atr > 0):
                    continue
                pos.high_close = max(pos.high_close, trail_close)
                pos.low_close = min(pos.low_close, trail_close)
                pos.latest_atr = trail_atr
                candidate = (pos.high_close - float(trail_atr_mult) * trail_atr if pos.side > 0
                             else pos.low_close + float(trail_atr_mult) * trail_atr)
                pos.stop = max(pos.stop, candidate) if pos.side > 0 else min(pos.stop, candidate)

        # The current open is the first executable price for a signal at ts.
        # Occupied signals remain unconsumed, so a later repeated signal may
        # enter after the position has gone away.
        if pos is None:
            for j in by_bar.get(i, []):
                action = int(sa[j])
                if action == 0 or int(san[j]) in consumed:
                    continue
                sc = float(signal_close[j]) if signal_close is not None else (float(bar_c[i - 1]) if i > 0 else float(bar_o[i]))
                atr = float(entry_atr[j]) if entry_atr is not None else float(satr[j])
                if not (math.isfinite(sc) and sc > 0 and math.isfinite(atr) and atr > 0):
                    continue
                stop = sc - stop_atr_mult * atr if action > 0 else sc + stop_atr_mult * atr
                # Jesse's original notional cap uses signal close.  Risk mode
                # additionally charges a conservative fee/slippage round trip.
                dist = abs(sc - stop)
                entry = float(bar_o[i]) * (1.0 + slip if action > 0 else 1.0 - slip)
                if sizing_mode == "jesse":
                    raw = min(risk_pct * cash / dist, cash / sc)
                    raw *= (1.0 - fee * 3.0) ** 2
                else:
                    roundtrip = sc * (2.0 * fee + 2.0 * slip)
                    raw = risk_pct * cash / (dist + roundtrip)
                # Apply the notional cap using the actual slipped fill and
                # reserve its fee, so the cap is respected after fees too.
                raw = min(raw, 0.95 * cash * leverage / (entry * (1.0 + fee)))
                qty = _floor_qty(raw)
                if qty <= 0:
                    continue
                entry_fee = abs(entry * qty) * fee
                cash -= entry_fee
                target = (entry + float(target_atr_mult) * atr if action > 0 else entry - float(target_atr_mult) * atr) if target_atr_mult is not None else None
                pos = _Position(action, qty, ts, int(st[j]), entry, sc, stop, stop, target,
                                entry_fee=entry_fee, high_close=entry,
                                low_close=entry, latest_atr=atr)
                consumed.add(int(san[j]))
                break

        # Stops use this minute's OHLC.  Capture the adverse intrabar mark
        # before realizing a stop so drawdown remains conservative.
        if pos is not None:
            adverse_price = float(bar_l[i]) if pos.side > 0 else float(bar_h[i])
            adverse_eq = mark(adverse_price)
            if close_peak > 0:
                max_dd = max(max_dd, (close_peak - adverse_eq) / close_peak)

        # A gap through the stop is filled at
        # the worse of the open and planned stop, with adverse slippage.
        if pos is not None:
            stop_hit = (pos.side > 0 and float(bar_l[i]) <= pos.stop) or (pos.side < 0 and float(bar_h[i]) >= pos.stop)
            if stop_hit:
                raw_exit = min(pos.stop, float(bar_o[i])) if pos.side > 0 else max(pos.stop, float(bar_o[i]))
                exit_price = raw_exit * (1.0 - slip if pos.side > 0 else 1.0 + slip)
                close_position(ts, exit_price, "stop")

        # A candle can touch both stop and target.  The stop check is purposely
        # first, which makes the unknown intrabar path adverse rather than
        # granting a favourable target fill.
        if pos is not None and pos.target is not None:
            target_hit = (pos.side > 0 and float(bar_h[i]) >= pos.target) or (pos.side < 0 and float(bar_l[i]) <= pos.target)
            if target_hit:
                raw_exit = max(pos.target, float(bar_o[i])) if pos.side > 0 else min(pos.target, float(bar_o[i]))
                exit_price = raw_exit * (1.0 - slip if pos.side > 0 else 1.0 + slip)
                close_position(ts, exit_price, "target")

        if pos is not None and max_hold_minutes is not None and (ts - pos.entry_ts) >= float(max_hold_minutes) * 60_000:
            exit_price = float(bar_c[i]) * (1.0 - slip if pos.side > 0 else 1.0 + slip)
            close_position(ts, exit_price, "time_stop")

        close_eq = mark(float(bar_c[i]))
        close_peak = max(close_peak, close_eq)
        if close_peak > 0:
            max_dd = max(max_dd, (close_peak - close_eq) / close_peak)
        equity_points.append({"timestamp": ts, "equity": close_eq, "cash": cash})

    if pos is not None:
        # Forced liquidation is at the final close, with adverse slippage.
        raw = float(bar_c[-1])
        exit_price = raw * (1.0 - slip if pos.side > 0 else 1.0 + slip)
        close_position(int(end_ms), exit_price, "forced_close")
        # The final equity point must reflect the forced liquidation.
        equity_points[-1] = {"timestamp": int(end_ms), "equity": cash, "cash": cash}
        if close_peak > 0:
            max_dd = max(max_dd, (close_peak - cash) / close_peak)

    net_total = gross_total - fees_total - funding_total
    nets = np.asarray([float(t["net_pnl"]) for t in trades], dtype=float)
    wins = nets[nets > 0]
    losses = nets[nets < 0]
    positive_sum = float(wins.sum()) if len(wins) else 0.0
    loss_sum = float(-losses.sum()) if len(losses) else 0.0
    # Keep daily output compact while retaining a starting and ending point.
    daily: Dict[int, Dict[str, float]] = {}
    for p in equity_points:
        day = int(p["timestamp"] // 86_400_000)
        daily[day] = p
    equity = [{"timestamp": int(start_ms), "equity": float(initial), "cash": float(initial)}]
    for day in sorted(daily):
        p = daily[day]
        if int(p["timestamp"]) != int(equity[-1]["timestamp"]):
            equity.append(p)
    if int(equity[-1]["timestamp"]) != int(end_ms):
        equity.append({"timestamp": int(end_ms), "equity": float(cash), "cash": float(cash)})

    metrics: Dict[str, Any] = {
        "initial_equity": float(initial),
        "final_equity": float(cash),
        "gross_pnl": float(gross_total),
        "fees": float(fees_total),
        "funding": float(funding_total),
        "net_pnl": float(net_total),
        "trade_count": len(trades),
        "win_count": int(len(wins)),
        "loss_count": int(len(losses)),
        "win_rate": float(len(wins) / len(nets)) if len(nets) else 0.0,
        "profit_factor": (positive_sum / loss_sum if loss_sum else (math.inf if positive_sum else 0.0)),
        "avg_hold_minutes": float(np.mean([t["hold_minutes"] for t in trades])) if trades else 0.0,
        "forced_count": sum(t["exit_label"] == "forced_close" for t in trades),
        "best_win_share": float(max(wins) / positive_sum) if positive_sum else 0.0,
        "best_trade_net_share": float(max(nets) / net_total) if len(nets) and net_total > 0 else 0.0,
        "max_drawdown": float(max_dd * initial),
        "max_drawdown_pct": float(max_dd),
    }
    return {
        "metrics": metrics,
        "trades": trades,
        "equity": equity,
        "assumptions": {
            "bar": "1m OHLC; stops checked using low/high",
            "gap_stop": "worse of stop and open, then adverse slippage",
            "trail": "completed signal close-based trail, active for following minute",
            "target": "target checked after adverse stop when both can occur in one OHLC candle",
            "funding_mark": "event assigned to containing minute; minute open proxy; positive rate costs longs",
        },
    }
