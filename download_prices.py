from datetime import date

import pandas as pd
import yfinance as yf

from load_config import load_config


def download_prices(config: dict) -> pd.DataFrame:
    """Скачивает дневные котировки инструментов за период из конфига."""
    end = date.today()
    try:
        start = end.replace(year=end.year - config["years"])
    except ValueError:
        # Для 29 февраля используем 28 февраля.
        start = end.replace(year=end.year - config["years"], day=28)

    frames = []
    print(f"Период: {start} — {end}, без текущего дня.", flush=True)
    for instrument in config["instruments"]:
        ticker = instrument["ticker"]
        prices = yf.download(
            ticker,
            start=start.isoformat(),
            end=end.isoformat(),
            interval="1d",
            auto_adjust=False,
            actions=True,
            multi_level_index=False,
            progress=False,
            threads=False,
            timeout=30,
        )
        if prices is None or prices.empty:
            raise ValueError(f"Не удалось скачать котировки {ticker}.")
        prices = prices.reset_index()
        prices.insert(0, "ticker", ticker)
        frames.append(prices)
        print(f"{ticker}: загружено {len(prices)} строк.", flush=True)

    if not frames:
        raise ValueError("Список инструментов пуст.")
    return pd.concat(frames, ignore_index=True)


if __name__ == "__main__":
    prices = download_prices(load_config())
    print(f"Всего строк: {len(prices)}")
    print(prices.head().to_string(index=False))
