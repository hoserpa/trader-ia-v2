"""Validacion y carga de datos historicos."""
import csv
import gzip
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


def load_raw(path):
    rows = []
    with gzip.open(path, "rt", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(r)
    return rows


def validate(rows):
    issues = []
    ts_prev = None
    dup = 0
    bad_ohlc = 0
    nonpos = 0
    gaps = 0
    total = len(rows)
    for r in rows:
        ts = int(r["ts"])
        if ts_prev is not None and ts <= ts_prev:
            if ts == ts_prev:
                dup += 1
            else:
                issues.append(("non_monotonic", ts))
        if ts_prev is not None and ts - ts_prev > 60:
            gaps += 1
        o, h, l, c = float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])
        if not (l <= o <= h and l <= c <= h):
            bad_ohlc += 1
        if o <= 0 or h <= 0 or l <= 0 or c <= 0 or float(r["volume"]) < 0:
            nonpos += 1
        ts_prev = ts
    report = {
        "rows": total,
        "duplicates": dup,
        "bad_ohlc": bad_ohlc,
        "non_positive": nonpos,
        "gaps_gt_60s": gaps,
        "gap_pct": round(gaps / max(total, 1) * 100, 4),
    }
    return report


def load_candles(path, start=None, end=None, resolution="1m"):
    df = pd.read_csv(path)
    df["ts"] = pd.to_datetime(df["ts"], unit="s", utc=True)
    if start:
        df = df[df["ts"] >= pd.Timestamp(start, tz="UTC")]
    if end:
        df = df[df["ts"] <= pd.Timestamp(end, tz="UTC")]
    if resolution != "1m":
        rule = resolution.replace("m", "min")
        df = df.set_index("ts").resample(rule).agg({
            "open": "first", "high": "max", "low": "min",
            "close": "last", "volume": "sum",
        }).dropna().reset_index()
    return df


def label_months(prices, bull=0.10, bear=-0.10):
    # prices: dict pair -> DataFrame with ts, close
    monthly = {}
    for pair, df in prices.items():
        d = df.copy()
        d["month"] = d["ts"].dt.to_period("M")
        g = d.groupby("month")["close"].agg(["first", "last"])
        g["ret"] = g["last"] / g["first"] - 1
        monthly[pair] = g["ret"]
    # equal-weight basket
    basket = None
    for pair in prices:
        if basket is None:
            basket = monthly[pair].copy()
        else:
            basket = basket.add(monthly[pair], fill_value=0)
    basket = basket / len(prices)
    labels = {}
    for m, r in basket.items():
        if r > bull:
            labels[str(m)] = "bull"
        elif r < bear:
            labels[str(m)] = "bear"
        else:
            labels[str(m)] = "range"
    return labels, basket.to_dict()


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    args = ap.parse_args()
    rows = load_raw(args.path)
    print(validate(rows))
