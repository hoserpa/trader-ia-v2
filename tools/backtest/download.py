"""Descarga historico OHLCV de Binance Vision (sustituto publico)."""
import argparse
import gzip
import os
import urllib.request
import zipfile
from datetime import date, timedelta
from pathlib import Path

PAIRS = {"BTC/EUR": "BTCEUR", "ETH/EUR": "ETHEUR", "SOL/EUR": "SOLEUR"}
BASE = "https://data.binance.vision/data/spot/monthly/klines"


def months_between(start: date, end: date):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        m += 1
        if m > 12:
            m = 1
            y += 1


def download_month(symbol, year, month, out_dir):
    name = f"{symbol}-1m-{year:04d}-{month:02d}.zip"
    url = f"{BASE}/{symbol}/1m/{name}"
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / name
    if not target.exists():
        print("downloading", url)
        urllib.request.urlretrieve(url, target)
    return target


def convert_zip(zip_path, out_csv):
    rows = []
    with zipfile.ZipFile(zip_path) as z:
        for member in z.namelist():
            with z.open(member) as f:
                for line in f:
                    line = line.decode("utf-8").strip()
                    if not line:
                        continue
                    cols = line.split(",")
                    ts_ms = int(cols[0])
                    ts = ts_ms // 1_000_000
                    rows.append((ts, cols[1], cols[2], cols[3], cols[4], cols[5]))
    rows.sort(key=lambda r: r[0])
    with gzip.open(out_csv, "wt", encoding="utf-8", newline="") as f:
        f.write("ts,open,high,low,close,volume\n")
        for r in rows:
            f.write(",".join(str(x) for x in r) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True, help="YYYY-MM")
    ap.add_argument("--end", required=True, help="YYYY-MM")
    ap.add_argument("--out", default="data/history")
    args = ap.parse_args()
    out = Path(args.out)
    start = date.fromisoformat(args.start + "-01")
    end = date.fromisoformat(args.end + "-01")
    for pair, symbol in PAIRS.items():
        pair_dir = out / pair.replace("/", "_")
        pair_dir.mkdir(parents=True, exist_ok=True)
        all_rows = []
        for y, m in months_between(start, end):
            try:
                zp = download_month(symbol, y, m, pair_dir)
            except Exception as e:
                print("skip", pair, y, m, e)
                continue
            # accumulate
            import zipfile, gzip

            with zipfile.ZipFile(zp) as z:
                for member in z.namelist():
                    with z.open(member) as f:
                        for line in f:
                            line = line.decode("utf-8").strip()
                            if not line:
                                continue
                            cols = line.split(",")
                            all_rows.append((int(cols[0]) // 1_000_000, cols[1], cols[2], cols[3], cols[4], cols[5]))
        all_rows.sort(key=lambda r: r[0])
        # dedup
        seen = set()
        uniq = []
        for r in all_rows:
            if r[0] in seen:
                continue
            seen.add(r[0])
            uniq.append(r)
        out_csv = out / f"{pair.replace('/', '_')}_1m.csv.gz"
        with gzip.open(out_csv, "wt", encoding="utf-8", newline="") as f:
            f.write("ts,open,high,low,close,volume\n")
            for r in uniq:
                f.write(",".join(str(x) for x in r) + "\n")
        print(pair, "rows", len(uniq), "->", out_csv)


if __name__ == "__main__":
    main()
PY