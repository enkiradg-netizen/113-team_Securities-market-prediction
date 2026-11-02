"""
Загрузка баров OHLCV по фьючерсам (15 минут, 1 час, 4 часа).

По плану контрактов (его строит contract_plan.py) смотрим, какие
контракты были фронтом или следующим и в какие дни. Для каждого
такого контракта качаем готовые бары Massive за эти дни и сохраняем
как пришли, без обработки.

Запуск (из корня проекта):
    python -m src.data.download_bars --product CL
    python -m src.data.download_bars --member iura

Результат: data/raw/futures/bars/<таймфрейм>/<код>/<контракт>.csv
"""

import argparse
from datetime import timedelta

import pandas as pd

from src.data.massive_api import get_api_key, massive_get_all
from src.data.project_config import (FUTURES_BAR_SIZES, RAW_DIR, futures_to_process,
                                     load_instruments, load_plan)

BARS_DIR = RAW_DIR / "futures" / "bars"


def contract_periods(plan: pd.DataFrame) -> dict:
    """
    Для каждого контракта из плана находит первый и последний день,
    когда он был фронтом или следующим. Возвращает {контракт: (с, по)}.
    """
    both = pd.concat([plan[["date", "front"]].rename(columns={"front": "ticker"}),
                      plan[["date", "next"]].rename(columns={"next": "ticker"})])
    periods = both.groupby("ticker")["date"].agg(["min", "max"])
    return {ticker: (row["min"], row["max"]) for ticker, row in periods.iterrows()}


def download_contract_bars(ticker: str, bar_size: str, first_day: str, last_day: str, api_key: str) -> pd.DataFrame:
    """
    Скачивает бары одного контракта за дни с first_day по last_day.

    Торговый день начинается накануне вечером, поэтому начинаем запрос
    на день раньше. Заканчиваем в начале следующего дня (не включая его):
    дата в запросе означает полночь UTC, а сессия идёт до вечера.
    Потом оставляем только бары нужных торговых дней.
    """
    bars = massive_get_all(
        f"/futures/v1/aggs/{ticker}",
        {
            "resolution": bar_size,
            "window_start.gte": (pd.Timestamp(first_day) - timedelta(days=1)).date().isoformat(),
            "window_start.lt": (pd.Timestamp(last_day) + timedelta(days=1)).date().isoformat(),
            "sort": "window_start.asc",
            "limit": 50000,
        },
        api_key,
    )
    bars = pd.DataFrame(bars)
    if bars.empty:
        return bars
    in_period = (bars["session_end_date"] >= first_day) & (bars["session_end_date"] <= last_day)
    return bars[in_period]


def download_bars(product: str, api_key: str) -> None:
    """Качает бары всех таймфреймов для контрактов из плана одного фьючерса."""
    periods = contract_periods(load_plan(product))
    print(f"{product}: контрактов для загрузки — {len(periods)}")

    for bar_size in FUTURES_BAR_SIZES:
        out_dir = BARS_DIR / bar_size / product
        out_dir.mkdir(parents=True, exist_ok=True)
        for ticker, (first_day, last_day) in periods.items():
            bars = download_contract_bars(ticker, bar_size, first_day, last_day, api_key)
            bars.to_csv(out_dir / f"{ticker}.csv", index=False)
            print(f"  {bar_size:6} {ticker}: {first_day} — {last_day}, баров {len(bars)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Загрузка баров OHLCV фьючерсов по плану контрактов.")
    who = parser.add_mutually_exclusive_group(required=True)
    who.add_argument("--member", help="имя участника из config/instruments.yaml, например iura")
    who.add_argument("--product", nargs="+", help="коды фьючерсов, например CL NG")
    args = parser.parse_args()

    config = load_instruments()
    api_key = get_api_key("futures")
    for future in futures_to_process(config, args.member, args.product):
        download_bars(future["ticker"], api_key)


if __name__ == "__main__":
    main()
