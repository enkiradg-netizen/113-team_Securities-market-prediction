"""
Детектор ценовой консолидации, версия 0 (правила, без ML).

Идея: консолидация = цена заперта в узком коридоре (коробке), не имеет
направления и свечи мельче обычного. Зона растёт, пока закрытия остаются
в коробке; первое закрытие за границей дальше delta «обычных свечей» —
пробой, его направление = метка Long / Short.

Все величины считаются в логарифмах цены (ln), поэтому:
  - склейка фьючерсов через factor (умножение цен) на них не влияет;
  - инструменты с разной ценой сравнимы.

«Обычная свеча» s_t — медиана логарифмического true range за последние
season_lookback баров ТОГО ЖЕ часа дня (время Нью-Йорка), без текущего бара.
Так ночные часы, где свечи всегда мелкие, не считаются консолидацией
сами по себе. Для дневного ТФ это просто медиана TR за последние дни.

Метрики окна из n последних баров (значение в строке t посчитано по бар t
включительно — без заглядывания вперёд):
  box_score  = ln(HH/LL) / sqrt(sum s_i^2)
               ширина коридора относительно ширины, которую дало бы
               случайное блуждание из «обычных» свечей. Для одинаковых
               свечей s это (HH-LL)/(ATR*sqrt(n)) = W/sqrt(n).
  er_score   = ER * sqrt(n),  ER = |ln C_t - ln C_{t-n}| / sum |d ln C|
               ~1 у случайного блуждания, <1 — топчется, >1 — тренд.
  comp_score = mean(ltr_i / s_i) — во сколько раз свечи окна мельче
               обычных для своего часа (1 = как обычно).
"""

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd


@dataclass
class BoxV0Params:
    n: int = 12                 # окно формирования коробки, баров
    season_lookback: int = 20   # сколько прошлых баров того же часа для s_t
    c_box: float = 1.0          # порог box_score  (меньше — уже коридор)
    c_er: float = 1.0           # порог er_score   (меньше — меньше направления)
    c_comp: float = 0.9         # порог comp_score (меньше — мельче свечи)
    delta: float = 0.5          # пробой: закрытие дальше delta * s_t за границей
    tz: str = "America/New_York"

    def to_dict(self) -> dict:
        return asdict(self)


def log_true_range(df: pd.DataFrame) -> pd.Series:
    """True range в логарифмах: ln(max(H, C_prev) / min(L, C_prev))."""
    prev_close = df["close"].shift(1)
    hi = np.maximum(df["high"], prev_close.fillna(df["high"]))
    lo = np.minimum(df["low"], prev_close.fillna(df["low"]))
    return np.log(hi / lo)


def seasonal_scale(df: pd.DataFrame, ltr: pd.Series, lookback: int, tz: str) -> pd.Series:
    """«Обычная свеча» этого часа: медиана ltr за прошлые lookback баров того же часа."""
    hour = df["time"].dt.tz_convert(tz).dt.hour
    return ltr.groupby(hour).transform(
        lambda x: x.rolling(lookback, min_periods=max(5, lookback // 2)).median().shift(1)
    )


def compute_metrics(df: pd.DataFrame, p: BoxV0Params) -> pd.DataFrame:
    """Помарные метрики окна из n последних баров (без заглядывания вперёд)."""
    out = pd.DataFrame(index=df.index)
    log_h, log_l, log_c = np.log(df["high"]), np.log(df["low"]), np.log(df["close"])

    ltr = log_true_range(df)
    s = seasonal_scale(df, ltr, p.season_lookback, p.tz)

    hh = log_h.rolling(p.n).max()
    ll = log_l.rolling(p.n).min()
    expected_width = np.sqrt((s ** 2).rolling(p.n).sum())

    path = log_c.diff().abs().rolling(p.n).sum()
    net = (log_c - log_c.shift(p.n)).abs()

    out["ltr"] = ltr
    out["s"] = s
    out["box_hi"] = hh
    out["box_lo"] = ll
    out["box_score"] = (hh - ll) / expected_width
    out["er_score"] = (net / path) * np.sqrt(p.n)
    out["comp_score"] = (ltr / s).rolling(p.n).mean()
    return out


def detect_zones(df: pd.DataFrame, p: BoxV0Params) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Находит зоны консолидации и пробои.

    Возвращает (zones, bars):
      zones — одна строка на зону: начало, бар формирования, конец, бар пробоя,
              направление (+1 Long / -1 Short / 0 — зона не закрылась к концу данных),
              границы коробки и метрики;
      bars  — помарные метрики + флаг in_zone.
    """
    m = compute_metrics(df, p)
    log_c = np.log(df["close"]).to_numpy()
    box = m["box_score"].to_numpy()
    er = m["er_score"].to_numpy()
    comp = m["comp_score"].to_numpy()
    hh = m["box_hi"].to_numpy()
    ll = m["box_lo"].to_numpy()
    s = m["s"].to_numpy()
    s_fallback = np.nanmedian(s)

    in_zone_flag = np.zeros(len(df), dtype=bool)
    zones = []
    in_zone = False
    next_allowed = 0
    start = formed = 0
    hi = lo = np.nan
    formed_scores = (np.nan, np.nan, np.nan)

    for t in range(len(df)):
        if not in_zone:
            if t < next_allowed or not (np.isfinite(box[t]) and np.isfinite(er[t]) and np.isfinite(comp[t])):
                continue
            if box[t] < p.c_box and er[t] < p.c_er and comp[t] < p.c_comp:
                in_zone = True
                start, formed = t - p.n + 1, t
                hi, lo = hh[t], ll[t]
                formed_scores = (box[t], er[t], comp[t])
            continue

        thr = p.delta * (s[t] if np.isfinite(s[t]) else s_fallback)
        if log_c[t] - hi > thr:
            direction = 1
        elif lo - log_c[t] > thr:
            direction = -1
        else:
            continue

        zones.append(_zone_record(df, start, formed, t - 1, t, direction, hi, lo, formed_scores, s))
        in_zone_flag[start:t] = True
        in_zone = False
        next_allowed = t + p.n  # новое окно формирования — только после бара пробоя

    if in_zone:
        end = len(df) - 1
        zones.append(_zone_record(df, start, formed, end, None, 0, hi, lo, formed_scores, s))
        in_zone_flag[start:] = True

    bars = m.copy()
    bars["in_zone"] = in_zone_flag
    return pd.DataFrame(zones), bars


def _zone_record(df, start, formed, end, breakout, direction, hi, lo, scores, s) -> dict:
    s_zone = np.nanmedian(s[start:end + 1])
    return {
        "start_time": df["time"].iloc[start],
        "formed_time": df["time"].iloc[formed],
        "end_time": df["time"].iloc[end],
        "breakout_time": df["time"].iloc[breakout] if breakout is not None else pd.NaT,
        "start_idx": start,
        "formed_idx": formed,
        "end_idx": end,
        "breakout_idx": breakout if breakout is not None else -1,
        "duration": end - start + 1,
        "direction": direction,
        "box_hi": float(np.exp(hi)),          # в ценах склеенного ряда
        "box_lo": float(np.exp(lo)),
        "width_pct": float((np.exp(hi - lo) - 1) * 100),
        "width_in_bars": float((hi - lo) / s_zone) if s_zone > 0 else np.nan,
        "box_score": scores[0],
        "er_score": scores[1],
        "comp_score": scores[2],
    }
