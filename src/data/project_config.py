"""
Общие настройки проекта: период, пути, конфиг инструментов, торговые дни.

Этот модуль импортируют все скрипты сбора данных, чтобы период
и пути были заданы в одном месте.
"""

from datetime import date
from pathlib import Path

import pandas as pd
import pandas_market_calendars as mcal
import yaml

# Период проекта.
START = date(2021, 11, 15)
END = date(2026, 9, 30)

# Корень проекта и папки с данными (data/ в git не попадает).
PROJECT_DIR = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_DIR / "data" / "raw"              # данные как пришли из Massive
INTERIM_DIR = PROJECT_DIR / "data" / "interim"      # промежуточные: план контрактов
PROCESSED_DIR = PROJECT_DIR / "data" / "processed"  # готовые: склеенные ряды

# Календарь торговых дней CME. Это торговые даты биржи: в праздники
# США (например, День благодарения) отдельного торгового дня нет.
FUTURES_CALENDAR = "CME_TradeDate"

# Таймфреймы баров для фьючерсов (так их называет Massive).
FUTURES_BAR_SIZES = ["15min", "1hour", "4hour"]

# Таймфреймы баров для акций и биржевой календарь NYSE (по нему проверяем
# полноту и определяем основную сессию).
STOCK_BAR_SIZES = ["5min", "15min", "1hour", "4hour", "1day"]
STOCKS_CALENDAR = "NYSE"

# Папка с планом контрактов (его строит contract_plan.py).
FUTURES_PLAN_DIR = INTERIM_DIR / "futures_plan"


def load_instruments() -> dict:
    """Читает общий конфиг инструментов config/instruments.yaml."""
    path = PROJECT_DIR / "config" / "instruments.yaml"
    with path.open(encoding="utf-8") as file:
        return yaml.safe_load(file)


def futures_to_process(config: dict, member: str | None, products: list | None) -> list:
    """
    Возвращает фьючерсы из конфига, которые нужно обработать.

    Либо все группы участника (--member), либо перечисленные коды (--product).
    """
    all_futures = {item["ticker"]: item for group in config["futures"].values() for item in group}
    if products:
        unknown = [code for code in products if code not in all_futures]
        if unknown:
            raise SystemExit(f"Нет таких фьючерсов: {', '.join(unknown)}. Есть: {', '.join(all_futures)}")
        return [all_futures[code] for code in products]
    if member not in config["team"]:
        raise SystemExit(f"Нет участника {member}. Есть: {', '.join(config['team'])}")
    groups = config["team"][member]["futures"]
    return [item for group in groups for item in config["futures"][group]]


def stocks_to_process(config: dict, member: str | None, tickers: list | None) -> list:
    """
    Возвращает акции из конфига, которые нужно обработать.

    Либо группы участника (--member), либо перечисленные тикеры (--ticker),
    либо все акции, если не задано ни то ни другое (--all). Акции из blue_chips
    записаны только тикером, их описание лежит в группах секторов. Одна акция
    может быть в двух группах, поэтому повторы убираем.
    """
    all_stocks = {item["ticker"]: item for group in config["stocks"].values() for item in group
                  if isinstance(item, dict)}
    if tickers:
        unknown = [ticker for ticker in tickers if ticker not in all_stocks]
        if unknown:
            raise SystemExit(f"Нет таких акций: {', '.join(unknown)}. Список: config/instruments.yaml")
        return [all_stocks[ticker] for ticker in dict.fromkeys(tickers)]
    if member is None:
        return list(all_stocks.values())
    if member not in config["team"]:
        raise SystemExit(f"Нет участника {member}. Есть: {', '.join(config['team'])}")
    chosen = [item["ticker"] if isinstance(item, dict) else item
              for group in config["team"][member]["stocks"] for item in config["stocks"][group]]
    return [all_stocks[ticker] for ticker in dict.fromkeys(chosen)]


def load_plan(product: str) -> pd.DataFrame:
    """Читает план контрактов одного фьючерса."""
    path = FUTURES_PLAN_DIR / f"{product}.csv"
    if not path.exists():
        raise SystemExit(f"Нет плана {path.name}. Сначала запустите: python -m src.data.contract_plan --product {product}")
    return pd.read_csv(path)


def trading_days(calendar: str) -> list:
    """Торговые дни биржи за период проекта (без выходных и праздников) в виде строк 'ГГГГ-ММ-ДД'."""
    days = mcal.get_calendar(calendar).valid_days(START, END)
    return [day.strftime("%Y-%m-%d") for day in days]
