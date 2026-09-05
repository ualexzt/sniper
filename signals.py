"""Causal multi-timeframe signal construction for the frozen research protocol."""
from __future__ import annotations

import numpy as np
import indicators


def as_jesse(candles):
    return np.column_stack([candles[k] for k in ('timestamp', 'open', 'close', 'high', 'low', 'volume')])


def aggregate(candles, minutes):
    """Only complete, contiguous UTC-aligned bars. Reject silently missing minutes."""
    raw = as_jesse(candles) if isinstance(candles, dict) else np.asarray(candles)
    if not len(raw):
        return np.empty((0, 6))
    timestamps = raw[:, 0].astype(np.int64)
    if not np.all(np.diff(timestamps) == 60_000):
        raise ValueError('Input must be contiguous 1m candles')
    block_ms = minutes * 60_000
    first = np.searchsorted(timestamps, ((timestamps[0] + block_ms - 1) // block_ms) * block_ms)
    count = (len(raw) - first) // minutes
    blocks = raw[first:first + count * minutes].reshape(count, minutes, 6)
    return np.column_stack((blocks[:, 0, 0], blocks[:, 0, 1], blocks[:, -1, 2],
                            blocks[:, :, 3].max(axis=1), blocks[:, :, 4].min(axis=1),
                            blocks[:, :, 5].sum(axis=1)))


def _fired(values, hp):
    on = (values['bb_upper'] < values['kc_upper']) & (values['bb_lower'] > values['kc_lower'])
    n = hp['min_squeeze_bars']
    valid = (np.isfinite(values['bb_upper']) & np.isfinite(values['kc_upper']) &
             np.isfinite(values['bb_lower']) & np.isfinite(values['kc_lower']))
    fired = np.zeros(len(on), dtype=bool)
    for i in range(n, len(on)):
        fired[i] = valid[i-n:i+1].all() and on[i-n:i].all() and not on[i]
    return fired


def build_signals(candles, trading_minutes, anchor_minutes, hp, mode):
    raw = as_jesse(candles)
    trade = aggregate(raw, trading_minutes)
    anchors = aggregate(raw, anchor_minutes)
    values = indicators.compute(anchors, hp)
    fired = _fired(values, hp)
    anchor_ms, trading_ms = anchor_minutes * 60_000, trading_minutes * 60_000
    timestamps = trade[:, 0].astype(np.int64) + trading_ms
    result = dict(timestamp=timestamps, close=trade[:, 2].copy(),
                  action=np.zeros(len(trade), dtype=np.int8),
                  atr=np.full(len(trade), np.nan), anchor_ts=np.full(len(trade), -1, dtype=np.int64))
    if mode == 'closed_anchor_once':
        for j, t in enumerate(timestamps):
            # Latest fully closed anchor controls trailing distance.
            k = np.searchsorted(anchors[:, 0] + anchor_ms, t, side='right') - 1
            if k < 0:
                continue
            result['atr'][j] = values['atr'][k]
            # Entry at close of first trading bar following the signal anchor.
            signal_end = t - trading_ms
            if signal_end % anchor_ms:
                continue
            s = np.searchsorted(anchors[:, 0] + anchor_ms, signal_end, side='right') - 1
            if s < 0 or anchors[s, 0] + anchor_ms != signal_end:
                continue
            result['anchor_ts'][j] = int(anchors[s, 0])
            if fired[s] and values['adx'][s] >= hp['adx_threshold']:
                result['action'][j] = np.sign(values['slope'][s]) if np.isfinite(values['slope'][s]) else 0
                # Use ATR frozen with signal also for initial stop. For tf=anchor,
                # this differs from latest closed ATR; separate field preserves both.
        result['entry_atr'] = result['atr'].copy()
        for j in np.flatnonzero(result['action']):
            s = np.searchsorted(anchors[:, 0], result['anchor_ts'][j])
            result['entry_atr'][j] = values['atr'][s]
    elif mode == 'forming_anchor':
        for j, t in enumerate(timestamps):
            # The completed trading bar belongs to the anchor containing t-1.
            anchor_start = ((t - 1) // anchor_ms) * anchor_ms
            k = np.searchsorted(anchors[:, 0], anchor_start)
            if k < hp['bb_period'] + hp['min_squeeze_bars'] or k > len(anchors):
                continue
            result['anchor_ts'][j] = anchor_start
            if k < len(anchors) and t == anchor_start + anchor_ms:
                last = {name: arr[k] for name, arr in values.items()}
                fire = fired[k]
            else:
                lo = np.searchsorted(raw[:, 0], anchor_start)
                hi = np.searchsorted(raw[:, 0], t)
                part = raw[lo:hi]
                partial = np.array([anchor_start, part[0, 1], part[-1, 2],
                                    part[:, 3].max(), part[:, 4].min(), part[:, 5].sum()])
                if hasattr(indicators, '_single_from_tail'):
                    sliced = {name: arr[:k] for name, arr in values.items()}
                    h = indicators._hp(hp)
                    last = indicators._single_from_tail(
                        anchors[:k], sliced, partial,
                        bb_period=h['bb_period'], bb_dev=h['bb_dev'],
                        kc_mult=h['kc_mult'], slope_period=h['slope_period'],
                        adx_period=h['adx_period'],
                    )
                elif hasattr(indicators, 'partial_last'):
                    last = indicators.partial_last(anchors[:k], partial, hp)
                else:
                    temp = indicators.compute(np.vstack((anchors[:k], partial)), hp)
                    last = {name: arr[-1] for name, arr in temp.items()}
                n = hp['min_squeeze_bars']
                prior_on = ((values['bb_upper'][k-n:k] < values['kc_upper'][k-n:k]) &
                            (values['bb_lower'][k-n:k] > values['kc_lower'][k-n:k]))
                current_on = last['bb_upper'] < last['kc_upper'] and last['bb_lower'] > last['kc_lower']
                fire = bool(prior_on.all() and not current_on and np.isfinite(last['bb_upper']))
            result['atr'][j] = last['atr']
            if fire and last['adx'] >= hp['adx_threshold'] and np.isfinite(last['slope']):
                result['action'][j] = np.sign(last['slope'])
        result['entry_atr'] = result['atr'].copy()
    else:
        raise ValueError(mode)
    return result
