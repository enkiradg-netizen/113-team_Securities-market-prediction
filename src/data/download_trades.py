"""
Загрузка тиковых сделок (time & sales) по фьючерсам.

По плану контрактов (его строит contract_plan.py) смотрим, какие
контракты были фронтом или следующим и в какие дни. Для каждого
такого контракта качаем все сделки за эти дни и сохраняем как пришли,
без обработки. Один файл — один контракт за один месяц.

Если файл уже есть, его не качаем заново. Поэтому после обрыва
скрипт можно просто запустить ещё раз.

Запуск (из корня проекта):
    python -m src.data.download_trades --product CL
    python -m src.data.download_trades --member iura
    python -m src.data.download_trades --product CL --start 2021-11 --end 2021-12

Результат: data/raw/futures/trades/<код>/<контракт>/<ГГГГ-ММ>.parquet
"""

import argparse
import time

import pandas as pd

from src.data.massive_api import get_api_key, massive_get_all
from src.data.project_config import (END, FUTURES_PLAN_DIR, RAW_DIR, START,
                                     futures_to_process, load_instruments)

TRADES_DIR = RAW_DIR / "futures" / "trades"


def contract_months(plan: pd.DataFrame) -> dict:
    """
    Раскладывает план по файлам: для каждого контракта и месяца — список
    дней, когда контракт был фронтом или следующим, и его объём за день.
    Возвращает {(контракт, "ГГГГ-ММ"): [(день, объём), ...]}.
    """
    months = {}
    for _, row in plan.iterrows():
        for ticker, volume in [(row["front"], row["front_volume"]), (row["next"], row["next_volume"])]:
            months.setdefault((ticker, row["date"][:7]), []).append((row["date"], volume))
    return months


def download_day_trades(ticker: str, day: str, api_key: str) -> pd.DataFrame:
    """
    Скачивает все сделки одного контракта за один торговый день.

    Торговый день фьючерса начинается накануне вечером, поэтому
    спрашиваем по session_end_date — дате торгового дня.
    """
    # Без limit Massive отдаёт по 10 сделок за запрос, максимум — 50 000
    # (документация: https://massive.com/docs/rest/futures/trades-quotes/trades).
    trades = massive_get_all(
        f"/futures/v1/trades/{ticker}",
        {"session_end_date": day, "limit": 50000},
        api_key,
    )
    return pd.DataFrame(trades)


def download_month(ticker: str, days: list, api_key: str) -> pd.DataFrame:
    """
    Качает сделки контракта за все его дни месяца и склеивает в одну таблицу.

    Для проверки сравниваем сумму объёмов сделок за день с дневным
    объёмом из плана (он взят из дневного бара Massive): если всё
    скачалось, они совпадают. Исключение — зерновые за 9 дней: там дневной
    бар включает ещё и предыдущий день, и предупреждение ожидаемо (см. README).
    """
    tables = []
    for day, volume in days:
        trades = download_day_trades(ticker, day, api_key)
        if trades.empty:
            print(f"    {day} {ticker}: сделок нет")
            continue
        if volume == 0:
            print(f"    {day} {ticker}: нет дневного бара, проверить объём нечем")
        elif trades["size"].sum() != volume:
            print(f"    {day} {ticker}: ВНИМАНИЕ: объём сделок {trades['size'].sum()}, а в плане {volume}")
        tables.append(trades)
    if not tables:
        return pd.DataFrame()
    return pd.concat(tables, ignore_index=True)


def download_trades(product: str, start_month: str, end_month: str, api_key: str) -> None:
    """Качает сделки всех контрактов из плана одного фьючерса, по месяцам."""
    plan = pd.read_csv(FUTURES_PLAN_DIR / f"{product}.csv")
    plan = plan[(plan["date"].str[:7] >= start_month) & (plan["date"].str[:7] <= end_month)]
    months = contract_months(plan)
    print(f"{product}: файлов (контракт + месяц) — {len(months)}")

    for (ticker, month), days in months.items():
        out_file = TRADES_DIR / product / ticker / f"{month}.parquet"
        if out_file.exists():
            continue  # уже скачано раньше — не качаем заново
        started = time.time()
        trades = download_month(ticker, days, api_key)
        if trades.empty:
            print(f"  {ticker} {month}: сделок нет, файл не сохраняю")
            continue
        # Пишем сначала во временный файл и только потом переименовываем:
        # если запись прервётся, неполный .parquet не появится
        # и при следующем запуске этот месяц скачается заново.
        out_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_file = out_file.with_suffix(".parquet.tmp")
        trades.to_parquet(tmp_file, index=False)
        tmp_file.rename(out_file)
        print(f"  {ticker} {month}: дней {len(days)}, сделок {len(trades)}, {time.time() - started:.0f} с")


def main() -> None:
    parser = argparse.ArgumentParser(description="Загрузка тиковых сделок фьючерсов по плану контрактов.")
    who = parser.add_mutually_exclusive_group(required=True)
    who.add_argument("--member", help="имя участника из config/instruments.yaml, например iura")
    who.add_argument("--product", nargs="+", help="коды фьючерсов, например CL NG")
    # Границы — целые месяцы, а не дни: иначе тест мог бы сохранить
    # полмесяца, и при полной загрузке этот файл пропустился бы как готовый.
    parser.add_argument("--start", default=START.isoformat()[:7], help="с какого месяца (для тестов), ГГГГ-ММ")
    parser.add_argument("--end", default=END.isoformat()[:7], help="по какой месяц (для тестов), ГГГГ-ММ")
    args = parser.parse_args()

    config = load_instruments()
    api_key = get_api_key("futures")
    for future in futures_to_process(config, args.member, args.product):
        download_trades(future["ticker"], args.start, args.end, api_key)


if __name__ == "__main__":
    main()
