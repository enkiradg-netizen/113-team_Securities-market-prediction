import pandas as pd


def clean_prices(prices: pd.DataFrame) -> pd.DataFrame:
    """Удаляет дубли, пропуски и некорректные котировки."""
    data = prices.copy()
    price_columns = ["Open", "High", "Low", "Close", "Adj Close"]
    numeric_columns = price_columns + ["Volume", "Dividends", "Stock Splits"]

    data["Date"] = pd.to_datetime(data["Date"], errors="coerce").dt.normalize()
    data[numeric_columns] = data[numeric_columns].apply(pd.to_numeric, errors="coerce")
    data[numeric_columns] = data[numeric_columns].replace(
        [float("inf"), float("-inf")], float("nan")
    )
    data["ticker"] = data["ticker"].astype("string").str.strip().str.upper()
    data = data.dropna(subset=["ticker", "Date"] + numeric_columns)

    valid = (
        data["ticker"].ne("")
        & data[price_columns].gt(0).all(axis=1)
        & data["Volume"].ge(0)
        & data["Volume"].mod(1).eq(0)
        & data["High"].ge(data[["Open", "Close", "Low"]].max(axis=1))
        & data["Low"].le(data[["Open", "Close", "High"]].min(axis=1))
        & data["Dividends"].ge(0)
        & data["Stock Splits"].ge(0)
    )
    data = data.loc[valid].drop_duplicates(["ticker", "Date"], keep="last")
    data = data.sort_values(["ticker", "Date"]).reset_index(drop=True)
    if data.empty:
        raise ValueError("После очистки не осталось котировок.")
    data["Volume"] = data["Volume"].astype("int64")
    print(f"Очистка: было {len(prices)}, осталось {len(data)}, удалено {len(prices) - len(data)}.")
    return data
