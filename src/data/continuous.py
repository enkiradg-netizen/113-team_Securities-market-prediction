"""
Склейка фьючерса в один непрерывный ряд (ratio back-adjust).

Каждый день берём бары фронта из плана контрактов. При переходе
на новый контракт цены прыгают: новый контракт обычно стоит иначе,
чем старый (например, CLF2 дешевле CLZ1 примерно на доллар).
Это не настоящее движение рынка, а разница между контрактами.

Чтобы убрать прыжок, в момент перехода считаем коэффициент:
    коэффициент = цена нового контракта / цена старого
на последнем баре перед переходом, где торговались оба. Затем умножаем
всю историю ДО перехода на этот коэффициент. Последний контракт остаётся
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
"""

import argparse

import pandas as pd

from src.data.project_config import (END, FUTURES_BAR_SIZES, FUTURES_PLAN_DIR, PROCESSED_DIR, RAW_DIR,
                                     START, futures_to_process, load_instruments)

BARS_DIR = RAW_DIR / "futures" / "bars"
OUT_DIR = PROCESSED_DIR / "futures_continuous"
PRICES = ["open", "high", "low", "close"]


def load_bars(product: str, bar_size: str, tickers: set) -> dict:
    """Читает скачанные бары контрактов. Возвращает {контракт: таблица баров}."""
    return {ticker: pd.read_csv(BARS_DIR / bar_size / product / f"{ticker}.csv") for ticker in tickers}


def roll_ratio(old: pd.DataFrame, new: pd.DataFrame, roll_day: str) -> float | None:
    """
    Коэффициент при переходе со старого контракта на новый.

    Берём последний бар до дня перехода, где есть оба контракта,
    и делим close нового на close старого.

    Бар определяем по паре «начало бара + торговый день»: у 4-часовых
    баров Massive бар 20:00–24:00 UTC делится на два с одинаковым
    началом — конец одной сессии и начало следующей.
    """
    key = ["window_start", "session_end_date"]
    before = lambda bars: bars[bars["session_end_date"] < roll_day].set_index(key)["close"]
    both = pd.concat([before(old), before(new)], axis=1, keys=["old", "new"]).dropna()
    if both.empty:
        return None
    last = both.iloc[-1]
    return last["new"] / last["old"]


def build_continuous(product: str, bar_size: str, start: str, end: str) -> None:
    """Склеивает бары одного таймфрейма в один ряд и сохраняет его."""
    plan = pd.read_csv(FUTURES_PLAN_DIR / f"{product}.csv")
    plan = plan[(plan["date"] >= start) & (plan["date"] <= end)]
    bars = load_bars(product, bar_size, set(plan["front"]) | set(plan["next"]))

    # 1. Каждый день берём бары фронта.
    pieces = []
    for day, front in zip(plan["date"], plan["front"]):
        front_bars = bars[front]
        pieces.append(front_bars[front_bars["session_end_date"] == day])
    series = pd.concat(pieces, ignore_index=True).rename(columns={"ticker": "contract"})
    series["factor"] = 1.0

    # 2. На каждом переходе считаем коэффициент и умножаем на него всё, что было раньше.
    rolls = plan[plan["front"] != plan["front"].shift()].iloc[1:]
    previous = plan["front"].shift()
    for index, row in rolls.iterrows():
        old, new, day = previous[index], row["front"], row["date"]
        ratio = roll_ratio(bars[old], bars[new], day)
        if ratio is None:
            print(f"  {day} {old} → {new}: нет общего бара, коэффициент не посчитан (оставляем 1)")
            continue
        series.loc[series["session_end_date"] < day, "factor"] *= ratio
        print(f"  {day} {old} → {new}: коэффициент {ratio:.4f}")

    for column in PRICES:
        series[column] = (series[column] * series["factor"]).round(6)
    series["time"] = pd.to_datetime(series["window_start"], unit="ns", utc=True)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    columns = ["time", "session_end_date", "contract", *PRICES, "volume", "transactions", "factor"]
    out_path = OUT_DIR / f"{product}_{bar_size}.parquet"
    series[columns].to_parquet(out_path, index=False)
    print(f"{product} {bar_size}: баров {len(series)}, файл {out_path}\n")


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
        for bar_size in FUTURES_BAR_SIZES:
            print(f"{future['ticker']} {bar_size}:")
            build_continuous(future["ticker"], bar_size, args.start, args.end)


if __name__ == "__main__":
    main()
