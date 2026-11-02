"""
Загрузка баров OHLCV, сплитов и дивидендов по акциям.

Для каждой акции качаем готовые бары Massive (5 минут, 15 минут, 1 час,
4 часа, 1 день) и сохраняем как пришли, без обработки: со всеми торгами,
включая утренние и вечерние, и без корректировки цен. Скорректированные цены
считает prepare_stock_bars.py.

4-часовые бары качаем кусками между сменами зимнего и летнего времени.
В одном запросе через смену времени их начало сдвигается на час и зависит
от даты начала запроса. Кусками начало всегда 4:00, 8:00, 12:00, 16:00 по Нью-Йорку.

Если компания сменила тикер (FB → META), до даты смены качаем по старому
тикеру. Старые тикеры записаны в config/instruments.yaml. Сплиты и дивиденды
по старому тикеру берём только до даты смены: биржа может отдать освободившийся
тикер другой бумаге (FB сейчас уже не Meta).

Если файл уже есть, его не качаем заново. Поэтому после обрыва
скрипт можно просто запустить ещё раз.

Запуск (из корня проекта):
    python -m src.data.download_stock_bars --ticker NVDA AAPL
    python -m src.data.download_stock_bars --member iura
    python -m src.data.download_stock_bars --all

Результат:
    data/raw/stocks/bars/<таймфрейм>/<тикер>.parquet
    data/raw/stocks/splits/<тикер>.csv
    data/raw/stocks/dividends/<тикер>.csv
"""

import argparse
from datetime import date, timedelta

import pandas as pd

from src.data.massive_api import get_api_key, massive_get_all
from src.data.project_config import END, RAW_DIR, START, load_instruments, stocks_to_process

STOCKS_DIR = RAW_DIR / "stocks"
EXCHANGE_TIMEZONE = "America/New_York"

# Как называется каждый таймфрейм в адресе запроса Massive.
BAR_REQUESTS = {
    "5min": (5, "minute"),
    "15min": (15, "minute"),
    "1hour": (1, "hour"),
    "4hour": (4, "hour"),
    "1day": (1, "day"),
}

# Столбцы файлов сплитов и дивидендов. Задаём их явно, чтобы у акции
# без дивидендов получился файл с заголовком, а не совсем пустой.
SPLIT_COLUMNS = ["id", "ticker", "execution_date", "adjustment_type", "split_from", "split_to",
                 "historical_adjustment_factor"]
DIVIDEND_COLUMNS = ["id", "ticker", "ex_dividend_date", "declaration_date", "record_date", "pay_date",
                    "cash_amount", "split_adjusted_cash_amount", "currency", "distribution_type",
                    "frequency", "historical_adjustment_factor"]


def ticker_periods(stock: dict) -> list:
    """
    Разбивает наш период по тикерам компании: [(тикер, с, по), ...].

    Например, META: FB до 08.06.2022 и META с 09.06.2022.
    """
    changes = sorted(stock.get("previous_tickers", []), key=lambda change: str(change["renamed"]))
    periods = []
    first_day = START
    for change in changes:
        renamed = date.fromisoformat(str(change["renamed"]))
        periods.append((change["ticker"], first_day, renamed - timedelta(days=1)))
        first_day = renamed
    periods.append((stock["ticker"], first_day, END))
    return periods


def same_clock_periods(first_day: date, last_day: date) -> list:
    """Разбивает период на куски между сменами зимнего и летнего времени в Нью-Йорке."""
    days = pd.date_range(first_day, last_day, tz=EXCHANGE_TIMEZONE)
    periods = []
    start = days[0]
    for previous, day in zip(days, days[1:]):
        if day.utcoffset() != previous.utcoffset():
            periods.append((start.date(), previous.date()))
            start = day
    periods.append((start.date(), days[-1].date()))
    return periods


def download_bars(stock: dict, bar_size: str, api_key: str) -> pd.DataFrame:
    """Скачивает бары одной акции за весь период, без корректировки цен."""
    multiplier, timespan = BAR_REQUESTS[bar_size]
    tables = []
    for symbol, first_day, last_day in ticker_periods(stock):
        pieces = same_clock_periods(first_day, last_day) if bar_size == "4hour" else [(first_day, last_day)]
        bars = []
        for piece_start, piece_end in pieces:
            bars += massive_get_all(
                f"/v2/aggs/ticker/{symbol}/range/{multiplier}/{timespan}/{piece_start}/{piece_end}",
                {"adjusted": "false", "sort": "asc", "limit": 50000},
                api_key,
            )
        table = pd.DataFrame(bars)
        if table.empty:
            print(f"    {symbol} {bar_size}: баров нет за {first_day} — {last_day}")
            continue
        table.insert(0, "source_ticker", symbol)
        tables.append(table)
    if not tables:
        return pd.DataFrame()
    result = pd.concat(tables, ignore_index=True)
    result.insert(0, "ticker", stock["ticker"])
    return result


def download_events(stock: dict, path: str, date_column: str, columns: list, api_key: str) -> pd.DataFrame:
    """Скачивает сплиты или дивиденды компании: по старым тикерам — только до даты смены."""
    events = []
    for symbol, _, last_day in ticker_periods(stock):
        params = {"ticker": symbol, "limit": 5000}
        if symbol != stock["ticker"]:
            params[f"{date_column}.lte"] = last_day.isoformat()
        events += massive_get_all(path, params, api_key)
    return pd.DataFrame(events, columns=columns).drop_duplicates("id")


def save_atomic(table: pd.DataFrame, out_file) -> None:
    """
    Пишет сначала во временный файл и только потом переименовывает:
    если запись прервётся, неполный файл не появится.
    """
    out_file.parent.mkdir(parents=True, exist_ok=True)
    tmp_file = out_file.with_name(out_file.name + ".tmp")
    if out_file.suffix == ".csv":
        table.to_csv(tmp_file, index=False)
    else:
        table.to_parquet(tmp_file, index=False)
    tmp_file.rename(out_file)


def download_stock(stock: dict, api_key: str) -> None:
    """Качает всё по одной акции: сплиты, дивиденды и бары всех таймфреймов."""
    ticker = stock["ticker"]
    for name, path, date_column, columns in [
        ("splits", "/stocks/v1/splits", "execution_date", SPLIT_COLUMNS),
        ("dividends", "/stocks/v1/dividends", "ex_dividend_date", DIVIDEND_COLUMNS),
    ]:
        out_file = STOCKS_DIR / name / f"{ticker}.csv"
        if not out_file.exists():
            save_atomic(download_events(stock, path, date_column, columns, api_key), out_file)

    for bar_size in BAR_REQUESTS:
        out_file = STOCKS_DIR / "bars" / bar_size / f"{ticker}.parquet"
        if out_file.exists():
            continue  # уже скачано раньше — не качаем заново
        bars = download_bars(stock, bar_size, api_key)
        if bars.empty:
            print(f"  {ticker} {bar_size}: баров нет, файл не сохраняю")
            continue
        save_atomic(bars, out_file)
        print(f"  {ticker} {bar_size}: баров {len(bars)}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Загрузка баров, сплитов и дивидендов по акциям.")
    who = parser.add_mutually_exclusive_group(required=True)
    who.add_argument("--ticker", nargs="+", help="тикеры акций, например NVDA AAPL")
    who.add_argument("--member", help="имя участника из config/instruments.yaml, например iura")
    who.add_argument("--all", action="store_true", help="все акции из config/instruments.yaml")
    args = parser.parse_args()

    stocks = stocks_to_process(load_instruments(), args.member, args.ticker)
    api_key = get_api_key("stocks")
    print(f"Акций: {len(stocks)}")
    for number, stock in enumerate(stocks, start=1):
        print(f"[{number}/{len(stocks)}] {stock['ticker']}", flush=True)
        download_stock(stock, api_key)


if __name__ == "__main__":
    main()
