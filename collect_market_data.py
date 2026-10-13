from clean_prices import clean_prices
from download_prices import download_prices
from load_config import load_config
from save_prices import save_prices


def collect_market_data() -> None:
    """Запускает загрузку, очистку и сохранение котировок в SQLite."""
    config = load_config()
    prices = download_prices(config)
    prices = clean_prices(prices)
    save_prices(prices, config)


if __name__ == "__main__":
    collect_market_data()
