"""Descarga datos históricos de Coinbase Advanced Trade.

Usage:
    python fetch_historical_data.py --pairs BTC-EUR,ETH-EUR --timeframe 5m --days 365
"""
import argparse
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
import ccxt
import pandas as pd
from loguru import logger

Path("logs").mkdir(exist_ok=True)


MAX_CANDLES_PER_REQUEST = 300
TIMEFRAMES = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}


async def fetch_ohlcv(ccxt_exchange, symbol: str, timeframe: str, since: int, limit: int) -> list:
    """Descarga velas usando ccxt con rate limiting."""
    await asyncio.sleep(0.15)
    try:
        ohlcv = await ccxt_exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=limit)
        return ohlcv
    except Exception as e:
        logger.error(f"Error fetching {symbol} {timeframe}: {e}")
        return []


async def download_pair_data(
    ccxt_exchange, symbol: str, timeframe: str, start_date: datetime, end_date: datetime
) -> pd.DataFrame:
    """Descarga todos los datos históricos para un par."""
    all_ohlcv = []
    current_ts = int(start_date.timestamp() * 1000)
    end_ts = int(end_date.timestamp() * 1000)
    tf_seconds = TIMEFRAMES.get(timeframe, 300)
    
    logger.info(f"Descargando {symbol} {timeframe} desde {start_date.date()}")
    
    while current_ts < end_ts:
        ohlcv = await fetch_ohlcv(ccxt_exchange, symbol, timeframe, current_ts, MAX_CANDLES_PER_REQUEST)
        
        if not ohlcv:
            logger.warning(f"Sin datos para {symbol} en timestamp {current_ts}")
            await asyncio.sleep(5)
            continue
            
        all_ohlcv.extend(ohlcv)
        current_ts = ohlcv[-1][0] + (tf_seconds * 1000)
        
        logger.info(f"  Descargadas {len(all_ohlcv)} velas hasta {datetime.fromtimestamp(ohlcv[-1][0]/1000)}")
        
        if len(ohlcv) < MAX_CANDLES_PER_REQUEST:
            break
            
        await asyncio.sleep(0.5)
    
    if not all_ohlcv:
        logger.warning(f"No se obtuvo datos para {symbol}")
        return pd.DataFrame()
    
    df = pd.DataFrame(
        all_ohlcv,
        columns=["timestamp", "open", "high", "low", "close", "volume"]
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    df = df[df["timestamp"] <= end_date]
    
    logger.info(f"  Total: {len(df)} velas ({df['timestamp'].min()} a {df['timestamp'].max()})")
    return df


async def main():
    parser = argparse.ArgumentParser(description="Descarga datos históricos de Coinbase")
    parser.add_argument("--pairs", type=str, default="BTC-EUR,ETH-EUR", help="Pares separados por coma")
    parser.add_argument("--timeframe", type=str, default="5m", help="Timeframe (1m, 5m, 15m, 1h, 4h, 1d)")
    parser.add_argument("--days", type=int, default=365, help="Días hacia atrás")
    parser.add_argument("--output", type=str, default="output/data", help="Directorio de salida")
    parser.add_argument("--sandbox", action="store_true", help="Usar sandbox de Coinbase")
    args = parser.parse_args()
    
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    exchange_config = {
        "options": {"defaultType": "spot"}
    }
    
    if args.sandbox:
        exchange_config["urls"] = {
            "api": {
                "public": "https://api-public.sandbox.exchange.coinbase.com",
                "private": "https://api-public.sandbox.exchange.coinbase.com"
            }
        }
    
    exchange = ccxt.coinbase(**exchange_config)
    
    pairs = [p.strip() for p in args.pairs.split(",")]
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=args.days)
    
    logger.info(f"Descarga iniciada: {args.days} días de datos en timeframe {args.timeframe}")
    
    for pair in pairs:
        try:
            df = await download_pair_data(exchange, pair, args.timeframe, start_date, end_date)
            if not df.empty:
                filename = output_dir / f"{pair.replace('/', '-')}_{args.timeframe}.parquet"
                df.to_parquet(filename, index=False)
                logger.info(f"Guardado: {filename} ({len(df)} filas)")
        except Exception as e:
            logger.error(f"Error descargando {pair}: {e}")
    
    logger.info("Descarga completada")


if __name__ == "__main__":
    logger.add("logs/fetch.log", rotation="50 MB", level="INFO")
    asyncio.run(main())
