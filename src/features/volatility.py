"""
Признаки волатильности по дневным барам.

ATR (Average True Range) — по Уайлдеру (J. W. Wilder, «New Concepts in Technical
Trading Systems», 1978):
    TR_t  = max(H_t - L_t, |H_t - C_{t-1}|, |L_t - C_{t-1}|),  у первого дня TR = H - L;
    ATR_n = среднее первых n значений TR (затравка),
    ATR_t = (ATR_{t-1} * (n - 1) + TR_t) / n  — сглаживание Уайлдера (RMA, alpha = 1/n).

STD — стандартное отклонение дневных изменений цены close-to-close в $
за последние n торговых дней (ddof = 1).

Окно n = 30 торговых дней.

Фьючерсы: TR и изменение close-to-close считаем по склеенному ряду (на дне
перехода нет ложного гэпа между контрактами) и делим на factor дня — получаем
реальные $ контракта, который торговался в этот день. Проценты считаем
от реального close того же дня, factor в них сокращается.
"""

import numpy as np
import pandas as pd

WINDOW = 30


def true_range(days: pd.DataFrame) -> pd.Series:
    """True range по скорректированным ценам дня (в единицах склеенного ряда)."""
    previous_close = days["close"].shift(1)
    ranges = pd.concat([
        days["high"] - days["low"],
        (days["high"] - previous_close).abs(),
        (days["low"] - previous_close).abs(),
    ], axis=1)
    return ranges.max(axis=1, skipna=True)


def wilder_average(values: pd.Series, window: int) -> pd.Series:
    """Сглаживание Уайлдера: затравка — простое среднее первых window значений."""
    result = np.full(len(values), np.nan)
    data = values.to_numpy(dtype=float)
    if len(data) >= window:
        result[window - 1] = data[:window].mean()
        for i in range(window, len(data)):
            result[i] = (result[i - 1] * (window - 1) + data[i]) / window
    return pd.Series(result, index=values.index)


def daily_volatility(days: pd.DataFrame, window: int = WINDOW) -> pd.DataFrame:
    """
    Признаки по дням, значение дня t посчитано по день t включительно.

    atr_{n}, std_{n} — в реальных $; *_pct — в процентах от реального close дня;
    day_change — close - open дня в реальных $, day_change_pct — в % от open.
    """
    tr_real = true_range(days) / days["factor"]
    change_real = days["close"].diff() / days["factor"]
    out = pd.DataFrame(index=days.index)
    out[f"atr_{window}"] = wilder_average(tr_real, window)
    out[f"std_{window}"] = change_real.rolling(window, min_periods=window).std(ddof=1)
    out[f"atr_{window}_pct"] = out[f"atr_{window}"] / days["real_close"] * 100
    out[f"std_{window}_pct"] = out[f"std_{window}"] / days["real_close"] * 100
    out["day_change"] = days["real_close"] - days["real_open"]
    out["day_change_pct"] = out["day_change"] / days["real_open"] * 100
    return out


def known_before_day(features: pd.DataFrame) -> pd.DataFrame:
    """Сдвиг на день: в строке дня t — значения, посчитанные по день t-1 включительно."""
    return features.shift(1)
