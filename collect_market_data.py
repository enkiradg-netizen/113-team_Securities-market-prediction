import sqlite3
from pathlib import Path

from clean_prices import clean_prices
from download_prices import download_prices
from load_config import load_config
from save_prices import save_prices


def collect_market_data() -> None:
    """Запускает загрузку, очистку и сохранение котировок в SQLite."""
    config = load_config()
    raw = download_prices(config)
    prices = clean_prices(raw)
    save_prices(prices, config)

    path = Path(__file__).resolve().parent / config["database"]
    with sqlite3.connect(path) as connection:
        stored = connection.execute("SELECT COUNT(*) FROM daily_prices").fetchone()[0]

    print("\nИтог загрузки:")
    print(f"Инструменты: {', '.join(prices['ticker'].unique())}")
    print(f"Скачано строк: {len(raw)}")
    print(f"Пропусков в ячейках до очистки: {int(raw.isna().sum().sum())}")
    print(f"Строк с пропусками до очистки: {int(raw.isna().any(axis=1).sum())}")
    print(f"Дублей тикер + дата до очистки: {int(raw.duplicated(['ticker', 'Date']).sum())}")
    print(f"Удалено при очистке: {len(raw) - len(prices)}")
    print(f"Строк после очистки и передано на сохранение: {len(prices)}")
    print(f"Всего строк в базе: {stored}")
    print("\nДанные по инструментам:")
    summary = prices.groupby("ticker").agg(
        строк=("Date", "size"), начало=("Date", "min"), конец=("Date", "max")
    )
    print(summary.to_string())
    missing = raw.isna().sum()
    if missing.any():
        print("\nПропуски по столбцам:")
        print(missing[missing > 0].to_string())
    print(f"\nБаза: {path}")


if __name__ == "__main__":
    collect_market_data()
