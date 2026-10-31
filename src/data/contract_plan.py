"""
План загрузки фьючерсов: какие 2 контракта качать в каждый торговый день.

У фьючерса много контрактов, по одному на месяц поставки
(например, CLZ1 — нефть с поставкой в декабре 2021). Торгуют в основном
ближайший из «торгуемых» месяцев — его называют фронтом.

Как строим план:
1. Берём из конфига торгуемые месяцы фьючерса (roll_months) и составляем
   цепочку контрактов по порядку: CLZ1, CLF2, CLG2, ...
2. Скачиваем дневной объём каждого контракта цепочки.
3. Идём по торговым дням биржи: фронт — текущий контракт цепочки,
   следующий — тот, что за ним. Как только следующий наторговал за день
   больше фронта, переходим на него. Назад не возвращаемся,
   через контракт не прыгаем.

Запуск (из корня проекта):
    python -m src.data.contract_plan --member iura
    python -m src.data.contract_plan --product CL NG

Результат: data/interim/futures_plan/<код>.csv с колонками
    date, front, front_volume, next, next_volume
"""

import argparse
from datetime import timedelta

import pandas as pd

from src.data.massive_api import get_api_key, massive_get_all
from src.data.project_config import (END, FUTURES_CALENDAR, FUTURES_PLAN_DIR, START,
                                     futures_to_process, load_instruments, trading_days)

# Буквы месяцев в тикерах фьючерсов: F — январь, G — февраль, ..., Z — декабрь.
MONTH_CODES = "FGHJKMNQUVXZ"

# Сколько дней подряд следующий контракт должен обгонять фронт по объёму,
# чтобы мы на него перешли. Защищает от разовых всплесков: например,
# 12.05.2022 декабрьская кукуруза один день обогнала июльскую,
# хотя торговля на декабрь ушла только 17.06.2022.
CONFIRM_DAYS = 2



def make_chain(product: str, roll_months: str) -> list:
    """
    Составляет цепочку контрактов по порядку: от начала периода до года после конца.

    Тикер = код + буква месяца + год. У Massive год пишется по-разному:
    обычно одной цифрой (CLZ1), но у некоторых фьючерсов с середины 2025
    года — двумя (у газа NGM5, а следующий уже NGN25). Поэтому для каждого
    контракта возвращаем оба варианта: (короткий, длинный).

    Через 10 лет короткие тикеры повторяются (CLZ1 бывает и 2021, и 2031 года),
    но в нашем окне 2021–2027 цифры года не повторяются.
    """
    chain = []
    for year in range(START.year, END.year + 2):
        for letter in roll_months:
            month = MONTH_CODES.index(letter) + 1
            if (year, month) >= (START.year, START.month):
                chain.append((f"{product}{letter}{year % 10}", f"{product}{letter}{year % 100}"))
    return chain


def get_daily_volumes(ticker: str, api_key: str) -> dict:
    """
    Скачивает дневной объём торгов одного контракта за наш период.

    Берём дневные бары (resolution=1session). Торговый день фьючерса
    начинается накануне вечером, поэтому день определяем по session_end_date.
    Возвращает словарь {дата: объём}.
    """
    bars = massive_get_all(
        f"/futures/v1/aggs/{ticker}",
        {
            "resolution": "1session",
            "window_start.gte": (START - timedelta(days=1)).isoformat(),
            "window_start.lte": END.isoformat(),
            "limit": 50000,
        },
        api_key,
    )
    return {bar["session_end_date"]: bar["volume"] for bar in bars
            if START.isoformat() <= bar["session_end_date"] <= END.isoformat()}


def make_plan(chain: list, volumes: dict, days: list) -> pd.DataFrame:
    """
    Идёт по торговым дням и выбирает фронт и следующий контракт.

    volumes — {тикер: {дата: объём}}. Переходим на следующий контракт
    цепочки, когда его объём за день больше, чем у фронта.
    """
    volume = lambda ticker, day: volumes[ticker].get(day, 0)

    # В первый день с данными фронт — контракт цепочки с самым большим объёмом.
    first_day = next(day for day in days if any(day in by_day for by_day in volumes.values()))
    i = max(range(len(chain) - 1), key=lambda k: volume(chain[k], first_day))

    # Последний день, когда контракт вообще торговался (по данным Massive).
    last_day = {ticker: max(by_day, default="") for ticker, by_day in volumes.items()}

    plan = []
    days_ahead = 0  # сколько дней подряд следующий обгоняет фронт
    for day in days:
        front_volume = volume(chain[i], day)
        next_volume = volume(chain[i + 1], day)
        # Если у Massive за этот день просто нет данных по фронту
        # (такие дыры бывают, например CLK2 23.03.2022), день не считаем,
        # иначе получился бы ложный переход. Исключение — фронт уже
        # закончил торговаться: тогда переходим сразу.
        front_finished = day > last_day[chain[i]]
        if front_volume > 0 or front_finished:
            days_ahead = days_ahead + 1 if next_volume > front_volume else 0
        if i + 2 < len(chain) and (days_ahead >= CONFIRM_DAYS or (front_finished and next_volume > 0)):
            i += 1
            days_ahead = 0
        front, nxt = chain[i], chain[i + 1]
        plan.append({
            "date": day,
            "front": front,
            "front_volume": volume(front, day),
            "next": nxt,
            "next_volume": volume(nxt, day),
        })
    return pd.DataFrame(plan)


def print_rolls(plan: pd.DataFrame) -> None:
    """Печатает даты переходов с контракта на контракт, чтобы проверить план глазами."""
    changed = plan[plan["front"] != plan["front"].shift()]
    print("Переходы:")
    for _, row in changed.iterrows():
        print(f"  {row['date']}  фронт {row['front']}  (следующий {row['next']})")


def build_plan(future: dict, api_key: str) -> None:
    """Строит и сохраняет план для одного фьючерса."""
    product = future["ticker"]
    pairs = make_chain(product, future["roll_months"])
    print(f"{product}: контрактов в цепочке — {len(pairs)}, качаю объёмы...")

    # Для каждого контракта пробуем короткий тикер, а если по нему
    # нет данных — длинный (с двумя цифрами года).
    chain, volumes = [], {}
    for short, long in pairs:
        ticker, by_day = short, get_daily_volumes(short, api_key)
        if not by_day:
            ticker, by_day = long, get_daily_volumes(long, api_key)
        chain.append(ticker)
        volumes[ticker] = by_day

    # Дни плана — торговые дни биржи. Добавляем и дни, где у Massive
    # есть данные, хотя календарь считает их выходными (так было 09.01.2025).
    massive_days = {day for by_day in volumes.values() for day in by_day}
    days = sorted(set(trading_days(FUTURES_CALENDAR)) | massive_days)

    plan = make_plan(chain, volumes, days)
    print_rolls(plan)

    FUTURES_PLAN_DIR.mkdir(parents=True, exist_ok=True)
    plan.to_csv(FUTURES_PLAN_DIR / f"{product}.csv", index=False)
    no_data = (plan["front_volume"] == 0).sum()
    print(f"{product}: дней в плане — {len(plan)}, из них без данных Massive по фронту — {no_data}")
    print(f"Файл: {FUTURES_PLAN_DIR / f'{product}.csv'}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="План загрузки фьючерсов: фронт и следующий контракт на каждый день.")
    who = parser.add_mutually_exclusive_group(required=True)
    who.add_argument("--member", help="имя участника из config/instruments.yaml, например iura")
    who.add_argument("--product", nargs="+", help="коды фьючерсов, например CL NG")
    args = parser.parse_args()

    config = load_instruments()
    api_key = get_api_key()
    for future in futures_to_process(config, args.member, args.product):
        build_plan(future, api_key)


if __name__ == "__main__":
    main()
