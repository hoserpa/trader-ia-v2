"""Export fresh candle data from RPi SQLite DB for retraining."""
import sqlite3
import pandas as pd
import os

DB_PATH = "/home/jalvarez/trader-ia-v2/data/crypto_trader.db"
OUTPUT_DIR = "/home/jalvarez/trader-ia-v2/training/output/data_kraken_fresh"

os.makedirs(OUTPUT_DIR, exist_ok=True)
db = sqlite3.connect(DB_PATH)

for pair in ["BTC/EUR", "ETH/EUR", "SOL/EUR"]:
    df = pd.read_sql_query(
        f"SELECT timestamp, open, high, low, close, volume FROM candles WHERE pair='{pair}' AND timeframe='15m' ORDER BY timestamp",
        db,
    )
    fname = pair.replace("/", "-") + "_15m.parquet"
    df.to_parquet(os.path.join(OUTPUT_DIR, fname), index=False)
    print(f"{pair}: {len(df)} velas, {df['timestamp'].min()} -> {df['timestamp'].max()}")

db.close()
print("Done.")
