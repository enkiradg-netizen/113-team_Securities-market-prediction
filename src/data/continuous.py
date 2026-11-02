"""
Склейка фьючерса в один непрерывный ряд (ratio back-adjust).

Каждый день берём бары фронта из плана контрактов. При переходе
на новый контракт цены прыгают: новый контракт обычно стоит иначе,
чем старый (например, CLF2 дешевле CLZ1 примерно на доллар).
Это не настоящее движение рынка, а разница между контрактами.

Чтобы убрать прыжок, в момент перехода считаем коэффициент:
    коэффициент = цена нового контракта / цена старого
на последнем часовом баре перед переходом, где торговались оба. Затем
умножаем всю историю ДО перехода на этот коэффициент. Коэффициенты
считаем один раз по часовым барам и применяем ко всем таймфреймам,
чтобы 15 минут, 1 час и 4 часа были склеены одинаково. Последний контракт остаётся
с настоящими ценами, а прошлое подстраивается под него.

Почему умножаем, а не прибавляем разницу: при сдвиге на разницу
(«панамский» метод) за 5 лет разрывы копятся, и старые цены Brent,
бензина и дизеля уходили ниже нуля. При умножении цены всегда
положительные, а процентные изменения (доходности) не меняются.

Запуск (из корня проекта, после download_bars):
    python -m src.data.continuous --product CL
    python -m src.data.continuous --product CL --start 2021-11-15 --end 2022-01-31

Результат: data/processed/futures_continuous/<код>_<таймфрейм>.parquet
    time, session_end_date, contract, open, high, low, close, volume,
    transactions, factor (на сколько умножены цены этого бара;
    настоящая цена контракта = цена / factor)
Время бара может повторяться: бар на границе двух торговых дней делится
на два с одним началом. Строку однозначно задаёт пара time + session_end_date.
"""

import argparse

import pandas as pd

from src.data.project_config import (END, FUTURES_BAR_SIZES, FUTURES_PLAN_DIR, PROCESSED_DIR, RAW_DIR,
                                     START, futures_to_process, load_instruments)

BARS_DIR = RAW_DIR / "futures" / "bars"
OUT_DIR = PROCESSED_DIR / "futures_continuous"
PRICES = ["open", "high", "low", "close"]
RATIO_BAR_SIZE = "1hour"  # по этим барам считаем коэффициенты переходов


def load_bars(product: str, bar_size: str, tickers: set) -> dict:
    """Читает скачанные бары контрактов. Возвращает {контракт: таблица баров}."""
    return {ticker: pd.read_csv(BARS_DIR / bar_size / product / f"{ticker}.csv") for ticker in tickers}


def roll_ratio(old: pd.DataFrame, new: pd.DataFrame, roll_day: str) -> float | None:
    """
    Коэффициент при переходе со старого контракта на новый.

    Берём последний бар до дня перехода, где есть оба контракта,
    и делим close нового на close старого.

    Бар определяем по паре «начало бара + торговый день»: бар на границе
    сессий Massive делит на два с одинаковым началом — конец одной сессии
    и начало следующей. У часовых баров это редкость (ES, 30.01.2025).
    """
    key = ["window_start", "session_end_date"]
    before = lambda bars: bars[bars["session_end_date"] < roll_day].set_index(key)["close"]
    both = pd.concat([before(old), before(new)], axis=1, keys=["old", "new"]).dropna()
    if both.empty:
        return None
    last = both.iloc[-1]
    return last["new"] / last["old"]


def roll_ratios(product: str, plan: pd.DataFrame) -> list:
    """
    Считает коэффициенты всех переходов фьючерса по часовым барам.
    Возвращает список (день перехода, коэффициент).
    """
    bars = load_bars(product, RATIO_BAR_SIZE, set(plan["front"]))
    ratios = []
    rolls = plan[plan["front"] != plan["front"].shift()].iloc[1:]
    previous = plan["front"].shift()
    for index, row in rolls.iterrows():
        old, new, day = previous[index], row["front"], row["date"]
        ratio = roll_ratio(bars[old], bars[new], day)
        if ratio is None:
            print(f"  {day} {old} → {new}: нет общего бара, коэффициент не посчитан (оставляем 1)")
            continue
        ratios.append((day, ratio))
        print(f"  {day} {old} → {new}: коэффициент {ratio:.4f}")
    return ratios


def build_continuous(product: str, bar_size: str, plan: pd.DataFrame, ratios: list) -> None:
    """Склеивает бары одного таймфрейма в один ряд и сохраняет его."""
    # Нужны только бары фронтов: при переходе новый контракт тоже становится фронтом.
    # Дальние «следующие» в конце периода могут быть ещё пустыми (например, UBH27).
    bars = load_bars(product, bar_size, set(plan["front"]))

    # 1. Каждый день берём бары фронта.
    pieces = []
    for day, front in zip(plan["date"], plan["front"]):
        front_bars = bars[front]
        pieces.append(front_bars[front_bars["session_end_date"] == day])
    series = pd.concat(pieces, ignore_index=True).rename(columns={"ticker": "contract"})

    # 2. На каждом переходе умножаем всё, что было раньше, на его коэффициент.
    series["factor"] = 1.0
    for day, ratio in ratios:
        series.loc[series["session_end_date"] < day, "factor"] *= ratio

    for column in PRICES:
        series[column] = (series[column] * series["factor"]).round(6)
    series["time"] = pd.to_datetime(series["window_start"], unit="ns", utc=True)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    columns = ["time", "session_end_date", "contract", *PRICES, "volume", "transactions", "factor"]
    out_path = OUT_DIR / f"{product}_{bar_size}.parquet"
    series[columns].to_parquet(out_path, index=False)
    print(f"{product} {bar_size}: баров {len(series)}, файл {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Склейка фьючерса в непрерывный ряд (ratio back-adjust).")
    who = parser.add_mutually_exclusive_group(required=True)
    who.add_argument("--member", help="имя участника из config/instruments.yaml, например iura")
    who.add_argument("--product", nargs="+", help="коды фьючерсов, например CL NG")
    parser.add_argument("--start", default=START.isoformat(), help="с какой даты (для тестов), ГГГГ-ММ-ДД")
    parser.add_argument("--end", default=END.isoformat(), help="по какую дату (для тестов), ГГГГ-ММ-ДД")
    args = parser.parse_args()

    config = load_instruments()
    for future in futures_to_process(config, args.member, args.product):
        product = future["ticker"]
        plan = pd.read_csv(FUTURES_PLAN_DIR / f"{product}.csv")
        plan = plan[(plan["date"] >= args.start) & (plan["date"] <= args.end)]
        print(f"{product}: переходы")
        ratios = roll_ratios(product, plan)
        for bar_size in FUTURES_BAR_SIZES:
            build_continuous(product, bar_size, plan, ratios)
        print()


if __name__ == "__main__":
    main()
