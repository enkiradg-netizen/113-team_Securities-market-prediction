"""
Готовые ряды по акциям: все виды цен для каждого таймфрейма.

Massive корректирует цены только на сплиты, а на дивиденды не корректирует.
Поэтому оба коэффициента считаем здесь из сырых баров, сплитов и дивидендов
(их качает download_stock_bars.py):
    split_factor — произведение split_from / split_to по всем сплитам ПОСЛЕ дня бара;
    dividend_factor — произведение (1 - дивиденд / закрытие накануне) по всем
    экс-дивидендным датам ПОСЛЕ дня бара.
Считаем только события нашего периода, поэтому на последнем баре оба
коэффициента равны 1 и результат не зависит от даты скачивания.

Дневной бар Massive иногда неполный: 30.05.2023 у 19 акций объём занижен до 127 раз,
а закрытие неверное. Признак такого дня: сумма объёма 5-минутных баров больше дневного
объёма (обычно она меньше, потому что аукцион закрытия есть только в дневном баре).
Такие дни собираем из 5-минутных баров и отмечаем столбцом day_bar_fixed (только в 1day).

Запуск (из корня проекта, после download_stock_bars):
    python -m src.data.prepare_stock_bars --ticker NVDA AAPL
    python -m src.data.prepare_stock_bars --member iura
    python -m src.data.prepare_stock_bars --all

Результат: data/processed/stocks/<тикер>_<таймфрейм>.parquet
    time (начало бара, UTC), ticker, source_ticker (тикер, по которому пришёл бар),
    open, high, low, close, volume — как пришли, без корректировки;
    split_adj_open, split_adj_high, split_adj_low, split_adj_close, split_adj_volume — со сплитами;
    adj_open, adj_high, adj_low, adj_close — со сплитами и дивидендами;
    vwap, trades, split_factor, dividend_factor;
    in_session — бар пересекается с основной сессией NYSE;
    day_bar_fixed — только в 1day: дневной бар собран из 5-минутных.
"""

import argparse

import pandas as pd
import pandas_market_calendars as mcal

from src.data.project_config import (END, PROCESSED_DIR, RAW_DIR, START, STOCK_BAR_SIZES, STOCKS_CALENDAR,
                                     load_instruments, stocks_to_process)

STOCKS_DIR = RAW_DIR / "stocks"
OUT_DIR = PROCESSED_DIR / "stocks"
EXCHANGE_TIMEZONE = "America/New_York"
BAR_MINUTES = {"5min": 5, "15min": 15, "1hour": 60, "4hour": 240}
PRICES = ["open", "high", "low", "close"]
MAX_FIVE_MIN_SHARE = 1.02  # если сумма 5-минутных баров больше дневного объёма в столько раз, бар неполный


def read_raw(path):
    """Читает сырой файл акции или объясняет, какой скрипт запустить."""
    if not path.exists():
        raise SystemExit(f"Нет файла {path}. Сначала запустите: python -m src.data.download_stock_bars")
    return pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)


def factors_by_day(days: list, ratios: pd.Series) -> dict:
    """
    Для каждого дня считает произведение коэффициентов всех событий ПОСЛЕ этого дня.
    ratios — коэффициенты событий, в индексе дата события.
    """
    if ratios.empty:
        return {day: 1.0 for day in days}  # у акции не было таких событий
    return {day: ratios[ratios.index > day].prod() for day in days}


def split_ratios(ticker: str) -> pd.Series:
    """Коэффициенты сплитов нашего периода: дата → split_from / split_to."""
    splits = read_raw(STOCKS_DIR / "splits" / f"{ticker}.csv")
    splits = splits[splits["execution_date"].between(START.isoformat(), END.isoformat())]
    ratios = splits["split_from"] / splits["split_to"]
    return ratios.groupby(splits["execution_date"]).prod()


def dividend_ratios(ticker: str, day_close: pd.Series) -> pd.Series:
    """
    Коэффициенты дивидендов нашего периода: дата → 1 - дивиденд / закрытие накануне.
    day_close — дневное закрытие без корректировки, в индексе дата.
    """
    dividends = read_raw(STOCKS_DIR / "dividends" / f"{ticker}.csv")
    dividends = dividends[dividends["ex_dividend_date"].between(START.isoformat(), END.isoformat())]
    other = dividends[dividends["currency"] != "USD"]
    if not other.empty:
        print(f"  {ticker}: ВНИМАНИЕ: дивиденды не в USD пропущены: {other['ex_dividend_date'].tolist()}")
    # Иногда в один день две выплаты (обычная и особая), их складываем.
    cash = dividends[dividends["currency"] == "USD"].groupby("ex_dividend_date")["cash_amount"].sum()
    ratios = {}
    for ex_date, amount in cash.items():
        before = day_close[day_close.index < ex_date]
        if before.empty:
            continue  # выплата в первый день периода: закрытия накануне у нас нет
        ratios[ex_date] = 1 - amount / before.iloc[-1]
    return pd.Series(ratios, dtype=float)


def session_flags(bars: pd.DataFrame, bar_size: str, schedule: pd.DataFrame) -> pd.Series:
    """
    True, если бар пересекается с основной сессией NYSE этого дня.

    Часовой бар 9:00 смешивает предторговлю и первые 30 минут сессии, у него тоже True:
    чисто отобрать основную сессию можно только на 5 и 15 минутах.
    """
    if bar_size == "1day":
        return bars["day"].isin(schedule.index)
    market_open = pd.to_datetime(bars["day"].map(schedule["market_open"]), utc=True)
    market_close = pd.to_datetime(bars["day"].map(schedule["market_close"]), utc=True)
    bar_end = pd.to_datetime(bars["t"] + BAR_MINUTES[bar_size] * 60_000, unit="ms", utc=True)
    return (bars["time"] < market_close) & (bar_end > market_open)


def fix_broken_days(days: pd.DataFrame, five_min: pd.DataFrame) -> pd.DataFrame:
    """
    Собирает из 5-минутных баров дневные бары, которые у Massive неполные.

    Цены берём из основной сессии, объём и число сделок — за весь день.
    """
    days = days.copy()
    days["day_bar_fixed"] = False
    five_min_volume = five_min.groupby("day")["volume"].sum()
    for index, row in days.iterrows():
        if five_min_volume.get(row["day"], 0) <= row["volume"] * MAX_FIVE_MIN_SHARE:
            continue
        whole_day = five_min[five_min["day"] == row["day"]]
        session = whole_day[whole_day["in_session"]]
        days.loc[index, ["open", "high", "low", "close"]] = [
            session["open"].iloc[0], session["high"].max(), session["low"].min(), session["close"].iloc[-1]]
        days.loc[index, "volume"] = whole_day["volume"].sum()
        days.loc[index, "trades"] = whole_day["trades"].sum()
        days.loc[index, "vwap"] = (whole_day["vwap"] * whole_day["volume"]).sum() / whole_day["volume"].sum()
        days.loc[index, "day_bar_fixed"] = True
    return days


def prepare_stock(ticker: str, schedule: pd.DataFrame) -> None:
    """Собирает готовые файлы всех таймфреймов одной акции."""
    day_bars = read_raw(STOCKS_DIR / "bars" / "1day" / f"{ticker}.parquet")
    day_time = pd.to_datetime(day_bars["t"], unit="ms", utc=True).dt.tz_convert(EXCHANGE_TIMEZONE)
    day_close = day_bars.set_index(day_time.dt.strftime("%Y-%m-%d"))["c"].sort_index()
    splits = split_ratios(ticker)
    dividends = dividend_ratios(ticker, day_close)

    for bar_size in STOCK_BAR_SIZES:
        raw = read_raw(STOCKS_DIR / "bars" / bar_size / f"{ticker}.parquet")
        bars = raw.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close",
                                   "v": "volume", "vw": "vwap", "n": "trades"})
        bars["time"] = pd.to_datetime(bars["t"], unit="ms", utc=True)
        bars["day"] = bars["time"].dt.tz_convert(EXCHANGE_TIMEZONE).dt.strftime("%Y-%m-%d")

        bars["in_session"] = session_flags(bars, bar_size, schedule)
        if bar_size == "5min":
            five_min = bars
        if bar_size == "1day":
            bars = fix_broken_days(bars, five_min)

        days = sorted(bars["day"].unique())
        bars["split_factor"] = bars["day"].map(factors_by_day(days, splits))
        bars["dividend_factor"] = bars["day"].map(factors_by_day(days, dividends))
        for column in PRICES:
            bars[f"split_adj_{column}"] = (bars[column] * bars["split_factor"]).round(6)
            bars[f"adj_{column}"] = (bars[column] * bars["split_factor"] * bars["dividend_factor"]).round(6)
        bars["split_adj_volume"] = bars["volume"] / bars["split_factor"]

        columns = ["time", "ticker", "source_ticker", *PRICES, "volume",
                   *[f"split_adj_{column}" for column in PRICES], "split_adj_volume",
                   *[f"adj_{column}" for column in PRICES],
                   "vwap", "trades", "split_factor", "dividend_factor", "in_session"]
        if bar_size == "1day":
            columns.append("day_bar_fixed")
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        bars[columns].to_parquet(OUT_DIR / f"{ticker}_{bar_size}.parquet", index=False)
    print(f"{ticker}: сплитов {len(splits)}, дивидендов {len(dividends)}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Готовые ряды по акциям: все виды цен.")
    who = parser.add_mutually_exclusive_group(required=True)
    who.add_argument("--ticker", nargs="+", help="тикеры акций, например NVDA AAPL")
    who.add_argument("--member", help="имя участника из config/instruments.yaml, например iura")
    who.add_argument("--all", action="store_true", help="все акции из config/instruments.yaml")
    args = parser.parse_args()

    # Часы основной сессии: день → открытие и закрытие, с учётом коротких дней.
    schedule = mcal.get_calendar(STOCKS_CALENDAR).schedule(start_date=START, end_date=END)
    schedule.index = schedule.index.strftime("%Y-%m-%d")

    for stock in stocks_to_process(load_instruments(), args.member, args.ticker):
        prepare_stock(stock["ticker"], schedule)


if __name__ == "__main__":
    main()
