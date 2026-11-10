"""
Файлы признаков: «исходные данные + столбцы признаков».

Для каждого инструмента и таймфрейма — своя папка и один файл:
    data/features/<futures|stocks>/<TICKER>_<bar>/<TICKER>_<bar>_features.parquet

Новый признак не создаёт новый файл, а добавляется столбцом в существующий.
Столбец с тем же именем перезаписывается. Если файла ещё нет, он создаётся
из исходного ряда data/processed.
"""

import os
from pathlib import Path

import pandas as pd

from src.data.project_config import PROJECT_DIR

FEATURES_DIR = PROJECT_DIR / "data" / "features"


def feature_path(asset: str, ticker: str, bar_size: str) -> Path:
    """Путь к файлу признаков инструмента на таймфрейме."""
    name = f"{ticker}_{bar_size}"
    return FEATURES_DIR / asset / name / f"{name}_features.parquet"


def load_features(asset: str, ticker: str, bar_size: str) -> pd.DataFrame:
    """Читает файл признаков."""
    return pd.read_parquet(feature_path(asset, ticker, bar_size))


def add_columns(asset: str, ticker: str, bar_size: str, base: pd.DataFrame, columns: pd.DataFrame) -> Path:
    """
    Добавляет столбцы признаков в файл инструмента.

    base — исходный ряд, по которому посчитаны признаки (нужен, если файла ещё
    нет); columns — признаки с тем же числом строк и в том же порядке.
    Перед записью проверяем, что строки файла совпадают с исходным рядом
    по time (и session_end_date у фьючерсов), чтобы не сдвинуть признаки.
    """
    path = feature_path(asset, ticker, bar_size)
    table = pd.read_parquet(path) if path.exists() else base.reset_index(drop=True)
    keys = [key for key in ["time", "session_end_date", "trade_date"] if key in base.columns and key in table.columns]
    if len(table) != len(base) or not table[keys].reset_index(drop=True).equals(base[keys].reset_index(drop=True)):
        raise ValueError(f"{path.name}: строки файла не совпадают с исходным рядом")
    if len(columns) != len(table):
        raise ValueError(f"{path.name}: у признаков {len(columns)} строк, в файле {len(table)}")
    for name in columns.columns:
        table[name] = columns[name].to_numpy()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    table.to_parquet(temporary, index=False)
    os.replace(temporary, path)
    return path
