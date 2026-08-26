import sqlite3, pandas as pd, os

db = sqlite3.connect('/app/data/crypto_trader.db')
out = '/tmp/data_fresh'
os.makedirs(out, exist_ok=True)

for pair in ['BTC/EUR', 'ETH/EUR', 'SOL/EUR']:
    df = pd.read_sql_query(
        "SELECT timestamp, open, high, low, close, volume FROM candles WHERE pair=? AND timeframe='15m' ORDER BY timestamp",
        db, params=[pair]
    )
    fname = pair.replace('/', '-') + '_15m.csv'
    df.to_csv(os.path.join(out, fname), index=False)
    print(f'{pair}: {len(df)} velas, {df["timestamp"].min()} -> {df["timestamp"].max()}')

db.close()
print('Done')
