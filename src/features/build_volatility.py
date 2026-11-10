"""
Считает дневные признаки волатильности и добавляет их в файлы признаков
каждого инструмента и таймфрейма (см. store.py).

Признаки (окно 30 торговых дней, формулы в volatility.py):
    atr_30, atr_30_pct, std_30, std_30_pct — посчитаны по день t-1 включительно,
        то есть известны до начала дня t. Так во всех файлах, включая 1day;
    day_change, day_change_pct — изменение close - open текущего дня (только в 1day);
    prev_day_change, prev_day_change_pct — то же за предыдущий день (во внутридневных
        файлах: изменение текущего дня в течение дня ещё неизвестно);
    trade_date — торговый день бара, по нему дневные значения переносятся на бары.

У фьючерсов дневного ряда в data/processed нет, поэтому для них дополнительно
создаётся файл 1day, собранный из часовых баров (daily.py).

Запуск из корня проекта:
    python -m src.features.build_volatility --member vlad
    python -m src.features.build_volatility --asset futures --ticker GC ES
"""

import argparse
import time

import pandas as pd

from src.data.project_config import (FUTURES_BAR_SIZES, STOCK_BAR_SIZES, futures_to_process,
                                     load_instruments, stocks_to_process)
from src.features.daily import daily_bars, load_source, trade_dates
from src.features.store import add_columns
from src.features.volatility import WINDOW, daily_volatility, known_before_day

LEVELS = [f"atr_{WINDOW}", f"atr_{WINDOW}_pct", f"std_{WINDOW}", f"std_{WINDOW}_pct"]


def build_instrument(asset: str, ticker: str) -> None:
    """Все таймфреймы одного инструмента."""
    days = daily_bars(asset, ticker)
    daily = daily_volatility(days)
    before = known_before_day(daily)

    # 1day: уровни на t-1, изменение — текущего дня.
    if asset == "futures":
        base = days.reset_index()[["trade_date", "time", "contract", "open", "high", "low", "close",
                                   "volume", "transactions", "factor"]]
    else:
        base = load_source("stocks", ticker, "1day")
    columns = pd.DataFrame({"trade_date": days.index})
    for name in LEVELS:
        columns[name] = before[name].to_numpy()
    columns["day_change"] = daily["day_change"].to_numpy()
    columns["day_change_pct"] = daily["day_change_pct"].to_numpy()
    if asset == "stocks":
        # Порядок строк 1day совпадает с отсортированными днями: проверяем.
        if not trade_dates(base, "stocks").reset_index(drop=True).equals(pd.Series(days.index)):
            raise ValueError(f"{ticker}: порядок дней 1day не совпадает")
    add_columns(asset, ticker, "1day", base, columns)

    # Внутридневные таймфреймы: дневные значения по торговому дню бара.
    bar_sizes = FUTURES_BAR_SIZES if asset == "futures" else [b for b in STOCK_BAR_SIZES if b != "1day"]
    for bar_size in bar_sizes:
        bars = load_source(asset, ticker, bar_size)
        dates = trade_dates(bars, asset)
        columns = pd.DataFrame({"trade_date": dates.to_numpy()})
        for name in LEVELS:
            columns[name] = dates.map(before[name]).to_numpy()
        columns["prev_day_change"] = dates.map(daily["day_change"].shift(1)).to_numpy()
        columns["prev_day_change_pct"] = dates.map(daily["day_change_pct"].shift(1)).to_numpy()
        missing = (~dates.isin(days.index)).sum()
        if missing:
            print(f"    {ticker} {bar_size}: {missing} баров в дни без дневного бара (признаки пустые)")
        add_columns(asset, ticker, bar_size, bars, columns)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--member", help="участник из config/instruments.yaml")
    parser.add_argument("--asset", choices=["futures", "stocks", "all"], default="all")
    parser.add_argument("--ticker", nargs="+", help="только эти коды/тикеры")
    args = parser.parse_args()
    if not args.member and not args.ticker:
        parser.error("укажите --member или --ticker")

    config = load_instruments()
    jobs = []
    if args.asset in ("futures", "all"):
        jobs += [("futures", item["ticker"]) for item in futures_to_process(config, args.member, args.ticker if args.asset == "futures" else None)]
    if args.asset in ("stocks", "all"):
        jobs += [("stocks", item["ticker"]) for item in stocks_to_process(config, args.member, args.ticker if args.asset == "stocks" else None)]

    for asset, ticker in jobs:
        start = time.time()
        build_instrument(asset, ticker)
        print(f"{asset} {ticker}: готово за {time.time() - start:.0f} с", flush=True)


if __name__ == "__main__":
    main()
