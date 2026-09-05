#!/usr/bin/env python3
"""Download and validate the Binance Vision ETHUSDT USD-M research data.

The requested range includes December 2024 as a warm-up month.  The intended
test period starts in January 2025; the complete range is retained in the
output so callers can choose their own warm-up boundary.
"""

from __future__ import annotations

import argparse
import calendar
import csv
import hashlib
import io
import json
import os
import re
import tempfile
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import numpy as np
import requests


SYMBOL = "ETHUSDT"
BASE_URL = "https://data.binance.vision/data/futures/um/monthly"
START_MONTH = (2024, 12)
END_MONTH = (2026, 8)
MAX_WORKERS = 4
TIMEOUT = (15, 120)
MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")


def month_range(start: tuple[int, int], end: tuple[int, int]) -> list[str]:
    out: list[str] = []
    year, month = start
    while (year, month) <= end:
        out.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return out


MONTHS = month_range(START_MONTH, END_MONTH)


def urls(month: str, kind: str) -> tuple[str, str]:
    if kind == "klines":
        name = f"{SYMBOL}-1m-{month}.zip"
        directory = f"{BASE_URL}/klines/{SYMBOL}/1m"
    elif kind == "fundingRate":
        name = f"{SYMBOL}-fundingRate-{month}.zip"
        directory = f"{BASE_URL}/fundingRate/{SYMBOL}"
    else:
        raise ValueError(kind)
    url = f"{directory}/{name}"
    return url, f"{url}.CHECKSUM"


def month_bounds(month: str) -> tuple[int, int]:
    match = MONTH_RE.match(month)
    if not match:
        raise ValueError(month)
    year, mon = map(int, match.groups())
    start = calendar.timegm((year, mon, 1, 0, 0, 0)) * 1000
    if mon == 12:
        next_month = (year + 1, 1)
    else:
        next_month = (year, mon + 1)
    end = calendar.timegm((*next_month, 1, 0, 0, 0)) * 1000
    return start, end


def normalize_timestamp(value: str | int | float) -> int:
    """Return an integer UTC timestamp in milliseconds.

    Binance files normally contain milliseconds, but this accepts seconds,
    microseconds, and nanoseconds so a source format change fails only when
    the resulting value is invalid or outside its expected month.
    """
    try:
        number = int(float(value))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"invalid timestamp {value!r}") from exc
    magnitude = abs(number)
    if magnitude < 10**11:  # seconds
        return number * 1000
    if magnitude < 10**14:  # milliseconds
        return number
    if magnitude < 10**17:  # microseconds
        return number // 1000
    return number // 1_000_000  # nanoseconds


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def checksum_from_text(text: bytes, filename: str) -> str:
    match = re.search(rb"\b([0-9a-fA-F]{64})\b", text)
    if not match:
        raise RuntimeError(f"checksum sidecar for {filename} has no SHA256")
    expected = match.group(1).decode("ascii").lower()
    if filename not in text.decode("utf-8", errors="replace"):
        raise RuntimeError(f"checksum sidecar does not name {filename}")
    return expected


def _get(url: str) -> requests.Response:
    last: Exception | None = None
    for attempt in range(3):
        try:
            response = requests.get(url, timeout=TIMEOUT)
            response.raise_for_status()
            return response
        except (requests.RequestException, OSError) as exc:
            last = exc
            if attempt < 2:
                time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"download failed for {url}: {last}") from last


def probe(url: str) -> tuple[str, int, str | None]:
    try:
        response = requests.head(url, allow_redirects=True, timeout=TIMEOUT)
        status = response.status_code
        return url, status, None
    except requests.RequestException as exc:
        return url, 0, str(exc)


def preflight() -> dict[tuple[str, str], dict[str, object]]:
    targets = [(month, kind) for month in MONTHS for kind in ("klines", "fundingRate")]
    probe_urls = [u for month, kind in targets for u in urls(month, kind)]
    results: dict[str, tuple[int, str | None]] = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(probe, u) for u in probe_urls]
        for future in as_completed(futures):
            url, status, error = future.result()
            results[url] = (status, error)

    available = []
    unavailable: list[str] = []
    metadata: dict[tuple[str, str], dict[str, object]] = {}
    for month, kind in targets:
        archive_url, checksum_url = urls(month, kind)
        archive_status, archive_error = results[archive_url]
        checksum_status, checksum_error = results[checksum_url]
        ok = 200 <= archive_status < 300 and 200 <= checksum_status < 300
        if ok:
            available.append(month)
        else:
            unavailable.append(
                f"{kind} {month}: archive HTTP {archive_status} {archive_error or ''}; "
                f"checksum HTTP {checksum_status} {checksum_error or ''}"
            )
        metadata[(month, kind)] = {
            "archive_url": archive_url,
            "checksum_url": checksum_url,
            "archive_http": archive_status,
            "checksum_http": checksum_status,
        }
    latest = max(available) if available else "none"
    print(f"Binance Vision preflight: latest accessible requested month: {latest}", flush=True)
    if unavailable:
        raise RuntimeError("requested source month unavailable; refusing to truncate:\n" + "\n".join(unavailable))
    return metadata


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def fetch_archive(root: Path, month: str, kind: str, metadata: dict[str, object]) -> dict[str, object]:
    raw = root / "data" / "raw"
    archive_url = str(metadata["archive_url"])
    checksum_url = str(metadata["checksum_url"])
    archive_name = Path(archive_url).name
    archive_path = raw / archive_name
    checksum_path = raw / f"{archive_name}.CHECKSUM"

    # Always retrieve the authoritative sidecar.  A cached archive is reused
    # only after its bytes match that sidecar.
    checksum_response = _get(checksum_url)
    checksum_text = checksum_response.content
    expected = checksum_from_text(checksum_text, archive_name)
    atomic_write(checksum_path, checksum_text)
    if archive_path.exists():
        archive_bytes = archive_path.read_bytes()
        actual = sha256_bytes(archive_bytes)
        if actual != expected:
            archive_bytes = _get(archive_url).content
            actual = sha256_bytes(archive_bytes)
            atomic_write(archive_path, archive_bytes)
    else:
        archive_bytes = _get(archive_url).content
        actual = sha256_bytes(archive_bytes)
        atomic_write(archive_path, archive_bytes)
    if actual != expected:
        raise RuntimeError(f"SHA256 mismatch for {archive_name}: expected {expected}, got {actual}")
    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            members = [info for info in archive.infolist() if not info.is_dir()]
            csv_members = [info for info in members if info.filename.lower().endswith(".csv")]
            if len(csv_members) != 1:
                raise RuntimeError(f"{archive_name} contains {len(csv_members)} CSV files, expected one")
            content = archive.read(csv_members[0])
    except (zipfile.BadZipFile, KeyError) as exc:
        raise RuntimeError(f"invalid ZIP archive {archive_name}") from exc
    return {
        **metadata,
        "month": month,
        "kind": kind,
        "archive": archive_name,
        "checksum": expected,
        "checksum_sha256": sha256_bytes(checksum_text),
        "bytes": len(archive_bytes),
        "content": content,
    }


def header_index(header: list[str], names: Iterable[str], fallback: int | None = None) -> int:
    normalized = {name.strip().lower(): i for i, name in enumerate(header)}
    for name in names:
        if name in normalized:
            return normalized[name]
    if fallback is not None:
        return fallback
    raise RuntimeError(f"required CSV column missing; header={header!r}, wanted={list(names)!r}")


def parse_candles(archives: list[dict[str, object]]) -> tuple[np.ndarray, ...]:
    rows: list[tuple[int, float, float, float, float, float]] = []
    for item in archives:
        month = str(item["month"])
        start, end = month_bounds(month)
        content = bytes(item["content"])
        reader = csv.reader(io.StringIO(content.decode("utf-8-sig")))
        header = next(reader, None)
        if header is None:
            raise RuntimeError(f"empty kline CSV for {month}")
        ti = header_index(header, ("open_time", "open time"), 0)
        oi = header_index(header, ("open",), 1)
        hi = header_index(header, ("high",), 2)
        li = header_index(header, ("low",), 3)
        ci = header_index(header, ("close",), 4)
        vi = header_index(header, ("volume",), 5)
        month_rows = 0
        for line_no, row in enumerate(reader, 2):
            if not row or not any(field.strip() for field in row):
                continue
            try:
                timestamp = normalize_timestamp(row[ti])
                values = tuple(float(row[i]) for i in (oi, hi, li, ci, vi))
            except (IndexError, ValueError) as exc:
                raise RuntimeError(f"invalid kline row {month}:{line_no}: {row!r}") from exc
            if not start <= timestamp < end:
                raise RuntimeError(f"kline timestamp outside {month}: {timestamp}")
            if not all(np.isfinite(values)):
                raise RuntimeError(f"nonfinite OHLCV in kline row {month}:{line_no}")
            rows.append((timestamp, *values))
            month_rows += 1
        expected = (end - start) // 60_000
        if month_rows != expected:
            raise RuntimeError(f"kline coverage for {month}: {month_rows} rows, expected {expected}")
    rows.sort(key=lambda row: row[0])
    if not rows:
        raise RuntimeError("no candle rows")
    timestamps = np.asarray([r[0] for r in rows], dtype=np.int64)
    diffs = np.diff(timestamps)
    if np.any(diffs <= 0):
        raise RuntimeError("duplicate or unsorted candle timestamps")
    if np.any(diffs != 60_000):
        bad = int(np.flatnonzero(diffs != 60_000)[0])
        raise RuntimeError(f"candle gap at {timestamps[bad]} -> {timestamps[bad + 1]}")
    columns = [np.asarray([r[i] for r in rows], dtype=np.float64) for i in range(1, 6)]
    return (timestamps, *columns)


def parse_funding(archives: list[dict[str, object]]) -> tuple[np.ndarray, np.ndarray, np.ndarray | None]:
    rows: list[tuple[int, float, int | None]] = []
    has_interval = False
    for item in archives:
        month = str(item["month"])
        start, end = month_bounds(month)
        reader = csv.reader(io.StringIO(bytes(item["content"]).decode("utf-8-sig")))
        header = next(reader, None)
        if header is None:
            raise RuntimeError(f"empty funding CSV for {month}")
        ti = header_index(header, ("calc_time", "calc time", "funding_time", "funding time", "timestamp"), 0)
        ri = header_index(header, ("funding_rate", "funding rate"), 2 if len(header) > 2 else 1)
        interval_i: int | None = None
        for candidate in ("funding_interval", "funding interval", "interval"):
            if candidate in {field.strip().lower() for field in header}:
                interval_i = header_index(header, (candidate,))
                has_interval = True
                break
        month_rows = 0
        for line_no, row in enumerate(reader, 2):
            if not row or not any(field.strip() for field in row):
                continue
            try:
                timestamp = normalize_timestamp(row[ti])
                rate = float(row[ri])
                interval = int(float(row[interval_i])) if interval_i is not None else None
            except (IndexError, ValueError) as exc:
                raise RuntimeError(f"invalid funding row {month}:{line_no}: {row!r}") from exc
            if not start <= timestamp < end:
                raise RuntimeError(f"funding timestamp outside {month}: {timestamp}")
            if not np.isfinite(rate):
                raise RuntimeError(f"nonfinite funding rate in {month}:{line_no}")
            rows.append((timestamp, rate, interval))
            month_rows += 1
        if month_rows == 0:
            raise RuntimeError(f"funding coverage missing for {month}")
    rows.sort(key=lambda row: row[0])
    timestamps = np.asarray([r[0] for r in rows], dtype=np.int64)
    if len(timestamps) and np.any(np.diff(timestamps) <= 0):
        raise RuntimeError("duplicate or unsorted funding timestamps")
    rates = np.asarray([r[1] for r in rows], dtype=np.float64)
    intervals = np.asarray([r[2] for r in rows], dtype=np.int64) if has_interval else None
    return timestamps, rates, intervals


def save_npz(path: Path, **arrays: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".npz", dir=path.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        np.savez_compressed(temporary, **arrays)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--probe-only", action="store_true", help="probe all requested archives without downloading")
    args = parser.parse_args()
    root = args.root.resolve()
    metadata = preflight()
    if args.probe_only:
        return 0

    targets = [(month, kind) for month in MONTHS for kind in ("klines", "fundingRate")]
    fetched: dict[tuple[str, str], dict[str, object]] = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        future_map = {
            pool.submit(fetch_archive, root, month, kind, metadata[(month, kind)]): (month, kind)
            for month, kind in targets
        }
        failures = []
        for future in as_completed(future_map):
            target = future_map[future]
            try:
                fetched[target] = future.result()
            except Exception as exc:
                failures.append(f"{target[1]} {target[0]}: {exc}")
        if failures:
            raise RuntimeError("one or more requested downloads failed:\n" + "\n".join(sorted(failures)))

    candle_archives = [fetched[(month, "klines")] for month in MONTHS]
    funding_archives = [fetched[(month, "fundingRate")] for month in MONTHS]
    candle_arrays = parse_candles(candle_archives)
    funding_arrays = parse_funding(funding_archives)
    save_npz(root / "data" / "candles.npz", timestamp=candle_arrays[0], open=candle_arrays[1], high=candle_arrays[2], low=candle_arrays[3], close=candle_arrays[4], volume=candle_arrays[5])
    funding_kwargs: dict[str, np.ndarray] = {"timestamp": funding_arrays[0], "funding_rate": funding_arrays[1]}
    if funding_arrays[2] is not None:
        funding_kwargs["interval"] = funding_arrays[2]
    save_npz(root / "data" / "funding.npz", **funding_kwargs)

    source_records = []
    for month, kind in targets:
        item = fetched[(month, kind)]
        source_records.append({k: v for k, v in item.items() if k != "content"})
        source_records[-1]["rows"] = int(sum(1 for _ in csv.reader(io.StringIO(bytes(item["content"]).decode("utf-8-sig")))) - 1)
    manifest = {
        "symbol": SYMBOL,
        "venue": "Binance Vision USD-M futures",
        "candle_interval": "1m",
        "requested_months": MONTHS,
        "test_period_start": "2025-01-01T00:00:00Z",
        "latest_accessible_requested_month": max(MONTHS),
        "sources": source_records,
        "outputs": {
            "candles": {"path": "data/candles.npz", "rows": int(len(candle_arrays[0])), "sha256": sha256_bytes((root / "data" / "candles.npz").read_bytes())},
            "funding": {"path": "data/funding.npz", "rows": int(len(funding_arrays[0])), "sha256": sha256_bytes((root / "data" / "funding.npz").read_bytes())},
        },
        "missing_months": [],
        "validation": {"deduplicated": False, "candle_gap_ms": 60_000, "timestamps": "normalized to integer UTC milliseconds", "ohlc_nonfinite": False},
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    manifest_path = root / "data" / "manifest.json"
    atomic_write(manifest_path, (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode())
    print(f"Wrote {len(candle_arrays[0]):,} candles and {len(funding_arrays[0]):,} funding rows", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
