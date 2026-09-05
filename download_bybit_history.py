#!/usr/bin/env python3
"""Download checksum-manifested Bybit linear 1m klines for frozen research."""
from __future__ import annotations

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import time

import numpy as np
import requests


ROOT = Path(__file__).resolve().parent
API = "https://api.bybit.com/v5/market/kline"
CHUNK_MINUTES = 1000


def timestamp(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)


def fetch_chunk(symbol: str, start_ms: int, end_ms: int) -> list[list[str]]:
    params = {"category": "linear", "symbol": symbol, "interval": "1",
              "start": start_ms, "end": end_ms - 1, "limit": 1000}
    last: Exception | None = None
    for attempt in range(5):
        try:
            response = requests.get(API, params=params, timeout=(10, 45))
            response.raise_for_status()
            payload = response.json()
            if payload.get("retCode") != 0:
                raise RuntimeError(f"Bybit error {payload.get('retCode')}: {payload.get('retMsg')}")
            return payload["result"]["list"]
        except (requests.RequestException, ValueError, KeyError, RuntimeError) as exc:
            last = exc
            if attempt < 4:
                time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"failed {symbol} {start_ms}-{end_ms}: {last}") from last


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def save_atomic(path: Path, arrays: dict[str, np.ndarray]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
        temporary = Path(handle.name)
        np.savez_compressed(handle, **arrays)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def download(symbol: str, start_ms: int, end_ms: int) -> dict[str, object]:
    width = CHUNK_MINUTES * 60_000
    chunks = [(start, min(start + width, end_ms)) for start in range(start_ms, end_ms, width)]
    rows: list[list[str]] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(fetch_chunk, symbol, a, b): (a, b) for a, b in chunks}
        for number, future in enumerate(as_completed(futures), 1):
            rows.extend(future.result())
            if number % 100 == 0 or number == len(chunks):
                print(f"{symbol}: {number}/{len(chunks)} chunks", flush=True)
    unique = {int(row[0]): row for row in rows}
    ordered = [unique[key] for key in sorted(unique)]
    expected = np.arange(start_ms, end_ms, 60_000, dtype=np.int64)
    actual = np.asarray([int(row[0]) for row in ordered], dtype=np.int64)
    if not np.array_equal(actual, expected):
        missing = np.setdiff1d(expected, actual)
        raise RuntimeError(f"{symbol} history is not contiguous; missing {len(missing)} minutes")
    arrays = {
        "timestamp": actual,
        "open": np.asarray([float(row[1]) for row in ordered]),
        "high": np.asarray([float(row[2]) for row in ordered]),
        "low": np.asarray([float(row[3]) for row in ordered]),
        "close": np.asarray([float(row[4]) for row in ordered]),
        "volume": np.asarray([float(row[5]) for row in ordered]),
        "turnover": np.asarray([float(row[6]) for row in ordered]),
    }
    if np.any(arrays["high"] < np.maximum.reduce([arrays["open"], arrays["low"], arrays["close"]])):
        raise RuntimeError(f"{symbol} invalid high")
    if np.any(arrays["low"] > np.minimum.reduce([arrays["open"], arrays["high"], arrays["close"]])):
        raise RuntimeError(f"{symbol} invalid low")
    path = ROOT / "data" / "bybit_history" / f"{symbol}_1m_2024-12_2025-12.npz"
    save_atomic(path, arrays)
    return {"symbol": symbol, "path": str(path.relative_to(ROOT)), "rows": len(actual),
            "first_ms": int(actual[0]), "last_ms": int(actual[-1]), "sha256": file_sha256(path)}


def main() -> None:
    protocol_path = ROOT / "level_reaction_protocol.json"
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    start_ms = timestamp(protocol["data"]["start_inclusive"])
    end_ms = timestamp(protocol["data"]["development_end_exclusive"])
    outputs = [download(symbol, start_ms, end_ms) for symbol in protocol["symbols"]]
    manifest = {"generated_at": datetime.now(timezone.utc).isoformat(),
                "endpoint": API,
                "download_protocol_sha256": file_sha256(protocol_path),
                "outputs": outputs}
    target = ROOT / "data" / "bybit_history" / "manifest.json"
    target.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(target)


if __name__ == "__main__":
    main()
