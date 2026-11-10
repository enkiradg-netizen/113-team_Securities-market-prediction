"""
Дневные бары инструмента: база для дневных признаков (ATR, STD, изменение за день).

Фьючерсы. Дневного таймфрейма в склеенных рядах нет, а дневным барам Massive
команда не доверяет (см. README): за часть дней их нет, у зерновых бывает
склейка с предыдущим днём. Поэтому день собираем из часовых баров по торговому
дню биржи (session_end_date): open первого бара, high и low за день, close
последнего бара, объём — сумма.

Цены в склеенном ряду умножены на factor (ratio back-adjust). Внутри торгового
дня factor один и тот же, поэтому реальная цена дня = цена в ряду / factor.

Акции. Берём готовый 1day (с аукционом закрытия, см. README) и цены split_adj_*:
без ложного скачка в день сплита. Торговый день — дата по Нью-Йорку.
"""

import pandas as pd

from src.data.project_config import PROCESSED_DIR

FUTURES_DIR = PROCESSED_DIR / "futures_continuous"
STOCKS_DIR = PROCESSED_DIR / "stocks"
NY = "America/New_York"


def load_source(asset: str, ticker: str, bar_size: str) -> pd.DataFrame:
    """Читает исходный ряд из data/processed."""
    folder = FUTURES_DIR if asset == "futures" else STOCKS_DIR
    return pd.read_parquet(folder / f"{ticker}_{bar_size}.parquet")


def trade_dates(bars: pd.DataFrame, asset: str) -> pd.Series:
    """Торговый день каждого бара: session_end_date у фьючерсов, дата по Нью-Йорку у акций."""
    if asset == "futures":
        return pd.to_datetime(bars["session_end_date"])
    return bars["time"].dt.tz_convert(NY).dt.tz_localize(None).dt.normalize()


def futures_daily_bars(ticker: str) -> pd.DataFrame:
    """
    Дневные бары фьючерса из часового склеенного ряда.

    Возвращает скорректированные цены (как в склеенном ряду), factor дня
    и реальные цены real_* = цена / factor.
    """
    hours = load_source("futures", ticker, "1hour").sort_values(["session_end_date", "time"])
    grouped = hours.groupby("session_end_date", sort=True)
    days = pd.DataFrame({
        "time": grouped["time"].first(),
        "contract": grouped["contract"].last(),
        "open": grouped["open"].first(),
        "high": grouped["high"].max(),
        "low": grouped["low"].min(),
        "close": grouped["close"].last(),
        "volume": grouped["volume"].sum(),
        "transactions": grouped["transactions"].sum(),
        "factor": grouped["factor"].first(),
    })
    if (grouped["factor"].nunique() > 1).any():
        raise ValueError(f"{ticker}: внутри торгового дня разный factor")
    days.index = pd.to_datetime(days.index)
    days.index.name = "trade_date"
    for column in ["open", "high", "low", "close"]:
        days[f"real_{column}"] = days[column] / days["factor"]
    return days


def stock_daily_bars(ticker: str) -> pd.DataFrame:
    """Дневные бары акции из 1day: цены со сплитами, factor = 1."""
    bars = load_source("stocks", ticker, "1day")
    days = pd.DataFrame({
        "open": bars["split_adj_open"].to_numpy(),
        "high": bars["split_adj_high"].to_numpy(),
        "low": bars["split_adj_low"].to_numpy(),
        "close": bars["split_adj_close"].to_numpy(),
        "adj_close": bars["adj_close"].to_numpy(),
        "volume": bars["split_adj_volume"].to_numpy(),
        "factor": 1.0,
    }, index=trade_dates(bars, "stocks").to_numpy())
    days.index.name = "trade_date"
    if days.index.duplicated().any():
        raise ValueError(f"{ticker}: повторяются дни в 1day")
    for column in ["open", "high", "low", "close"]:
        days[f"real_{column}"] = days[column]
    return days.sort_index()


def daily_bars(asset: str, ticker: str) -> pd.DataFrame:
    """Дневные бары инструмента (см. futures_daily_bars и stock_daily_bars)."""
    return futures_daily_bars(ticker) if asset == "futures" else stock_daily_bars(ticker)
