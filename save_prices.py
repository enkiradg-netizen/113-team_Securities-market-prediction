import sqlite3
from contextlib import closing
from pathlib import Path

import pandas as pd

from download_prices import download_prices
from load_config import load_config


def save_prices(prices: pd.DataFrame, config: dict) -> None:
    """Сохраняет котировки в SQLite, обновляя строки без дублей."""
    path = Path(__file__).resolve().parent / config["database"]
    path.parent.mkdir(parents=True, exist_ok=True)

    columns = [
        "ticker", "Date", "Open", "High", "Low", "Close",
        "Adj Close", "Volume", "Dividends", "Stock Splits",
    ]
    data = prices[columns].copy()
    data["Date"] = pd.to_datetime(data["Date"]).dt.strftime("%Y-%m-%d")
    if data.isna().any().any():
        raise ValueError("В котировках есть пропуски. Сначала нужна очистка.")
    rows = list(data.itertuples(index=False, name=None))

    with closing(sqlite3.connect(path)) as connection:
        with connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS daily_prices (
                    ticker TEXT NOT NULL,
                    date TEXT NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    adj_close REAL NOT NULL,
                    volume INTEGER NOT NULL,
                    dividends REAL NOT NULL,
                    stock_splits REAL NOT NULL,
                    PRIMARY KEY (ticker, date)
                )
            """)
            connection.executemany("""
                INSERT INTO daily_prices (
                    ticker, date, open, high, low, close,
                    adj_close, volume, dividends, stock_splits
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticker, date) DO UPDATE SET
                    open=excluded.open, high=excluded.high, low=excluded.low,
                    close=excluded.close, adj_close=excluded.adj_close,
                    volume=excluded.volume, dividends=excluded.dividends,
                    stock_splits=excluded.stock_splits
            """, rows)
    print(f"Сохранено {len(rows)} строк. База: {path}")


if __name__ == "__main__":
    config = load_config()
    prices = download_prices(config)
    save_prices(prices, config)
