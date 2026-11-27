"""
Прогон детектора консолидации v0 на одном инструменте и таймфрейме.

    Из корня репозитория:
    python sandbox/Vladislav/26.11.26/detector_v0/run_box_v0.py --ticker ES --bar 1hour \
        --kind futures --chart-out <путь.html> --chart-days 60

Нужны файлы признаков data/features/... (см. src/features) и пакет plotly для графика.

Пишет:
  data/detector/<kind>/<TICKER>_<bar>/<TICKER>_<bar>_zones_v0.parquet — таблица зон;
  HTML-график последних chart-days дней с зонами и пробоями (если задан --chart-out);
  в консоль — сводную статистику и проверку на случайных окнах.
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from box_v0 import BoxV0Params, detect_zones  # noqa: E402

RNG_SEED = 42


def load_bars(kind: str, ticker: str, bar: str) -> pd.DataFrame:
    path = Path("data/features") / kind / f"{ticker}_{bar}" / f"{ticker}_{bar}_features.parquet"
    df = pd.read_parquet(path, columns=["time", "open", "high", "low", "close", "volume"])
    return df.sort_values("time").reset_index(drop=True)


def summary(zones: pd.DataFrame, bars: pd.DataFrame) -> dict:
    closed = zones[zones["direction"] != 0]
    return {
        "bars_total": int(len(bars)),
        "zones_total": int(len(zones)),
        "zones_closed": int(len(closed)),
        "share_bars_in_zone_pct": round(100 * bars["in_zone"].mean(), 1),
        "zones_per_month": round(len(zones) / (len(bars) / (23 * 21)), 1),
        "duration_bars_q": closed["duration"].quantile([0.1, 0.25, 0.5, 0.75, 0.9]).round(0).to_dict(),
        "width_in_typical_bars_median": round(float(closed["width_in_bars"].median()), 2),
        "long_share_pct": round(100 * (closed["direction"] == 1).mean(), 1),
    }


def expansion_check(zones: pd.DataFrame, bars: pd.DataFrame, horizon: int, n_random: int = 200) -> dict:
    """
    Растёт ли волатильность после зоны сильнее, чем после похожих случайных окон.

    x_t = ltr_t / s_t — размер свечи относительно обычной для её часа.
    Для зоны:     after/in = mean(x) за horizon баров ПОСЛЕ бара пробоя / mean(x) в зоне.
    Нулевая модель: случайные окна той же длины с таким же средним x внутри
    (±10%) — чтобы отделить эффект от простого возврата к среднему
    (после любого тихого окна волатильность в среднем растёт).
    """
    x = (bars["ltr"] / bars["s"]).to_numpy()
    x = np.where(np.isfinite(x), x, np.nan)
    cs = np.concatenate([[0.0], np.nancumsum(x)])
    valid = np.concatenate([[0], np.cumsum(np.isfinite(x))])

    def mean_x(a, b):
        cnt = valid[b] - valid[a]
        return (cs[b] - cs[a]) / cnt if cnt > 0 else np.nan

    rng = np.random.default_rng(RNG_SEED)
    n = len(x)
    zone_ratio, null_ratio = [], []
    for z in zones[zones["direction"] != 0].itertuples():
        a, b = z.start_idx, z.end_idx + 1
        post_a, post_b = z.breakout_idx + 1, z.breakout_idx + 1 + horizon
        if post_b > n:
            continue
        x_in = mean_x(a, b)
        zone_ratio.append(mean_x(post_a, post_b) / x_in)

        length = b - a
        starts = rng.integers(0, n - length - horizon - 2, size=n_random)
        for r in starts:
            r_in = mean_x(r, r + length)
            if not np.isfinite(r_in) or abs(r_in / x_in - 1) > 0.10:
                continue
            null_ratio.append(mean_x(r + length + 1, r + length + 1 + horizon) / r_in)

    zr, nr = np.array(zone_ratio), np.array(null_ratio)
    return {
        "horizon_bars": horizon,
        "zones_used": int(len(zr)),
        "zones_after_in_median": round(float(np.nanmedian(zr)), 3),
        "random_matched_after_in_median": round(float(np.nanmedian(nr)), 3),
        "random_windows_used": int(len(nr)),
    }


def continuation_check(zones: pd.DataFrame, df: pd.DataFrame, horizon: int) -> dict:
    """Справочно (это уже задача части 2): идёт ли цена дальше в сторону пробоя."""
    log_c = np.log(df["close"].to_numpy())
    closed = zones[(zones["direction"] != 0) & (zones["breakout_idx"] + horizon < len(df))]
    moves = [d * (log_c[b + horizon] - log_c[b]) for b, d in zip(closed["breakout_idx"], closed["direction"])]
    moves = np.array(moves)
    return {
        "horizon_bars": horizon,
        "share_continue_pct": round(100 * float((moves > 0).mean()), 1),
        "mean_move_bp": round(float(moves.mean() * 1e4), 1),
    }


def make_chart(df: pd.DataFrame, zones: pd.DataFrame, days: int, title: str, out: Path) -> None:
    import plotly.graph_objects as go

    t_end = df["time"].iloc[-1]
    part = df[df["time"] >= t_end - pd.Timedelta(days=days)]
    labels = part["time"].dt.tz_convert("America/New_York").dt.strftime("%Y-%m-%d %H:%M")
    label_of = dict(zip(part.index, labels))

    fig = go.Figure(go.Candlestick(
        x=labels, open=part["open"], high=part["high"], low=part["low"], close=part["close"],
        name="ES", increasing_line_color="#2e7d32", decreasing_line_color="#c62828",
    ))

    zs = zones[(zones["end_idx"] >= part.index[0])]
    colors = {1: "rgba(46,125,50,0.18)", -1: "rgba(198,40,40,0.18)", 0: "rgba(120,120,120,0.18)"}
    for z in zs.itertuples():
        x0 = label_of.get(max(z.start_idx, part.index[0]))
        x1 = label_of.get(z.end_idx)
        if x0 is None or x1 is None:
            continue
        fig.add_shape(type="rect", xref="x", yref="y", x0=x0, x1=x1, y0=z.box_lo, y1=z.box_hi,
                      fillcolor=colors[z.direction], line=dict(width=1, color="rgba(60,60,60,0.6)"))

    br = zs[(zs["direction"] != 0) & zs["breakout_idx"].isin(part.index)]
    for d, sym, col, name in ((1, "triangle-up", "#1b5e20", "Пробой вверх (Long)"),
                              (-1, "triangle-down", "#b71c1c", "Пробой вниз (Short)")):
        sel = br[br["direction"] == d]
        fig.add_trace(go.Scatter(
            x=[label_of[i] for i in sel["breakout_idx"]],
            y=df.loc[sel["breakout_idx"], "close"],
            mode="markers", marker=dict(symbol=sym, size=11, color=col), name=name,
        ))

    fig.update_layout(
        title=title, height=720, xaxis_rangeslider_visible=False,
        xaxis=dict(type="category", nticks=20, tickangle=-45),
        yaxis_title="Цена (склеенный ряд)", legend=dict(orientation="h", y=1.04),
        margin=dict(l=60, r=20, t=80, b=90), template="plotly_white",
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(out, include_plotlyjs=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", default="futures")
    ap.add_argument("--ticker", default="ES")
    ap.add_argument("--bar", default="1hour")
    ap.add_argument("--chart-out", default=None)
    ap.add_argument("--chart-days", type=int, default=60)
    args = ap.parse_args()

    p = BoxV0Params()
    df = load_bars(args.kind, args.ticker, args.bar)
    zones, bars = detect_zones(df, p)

    out_dir = Path("data/detector") / args.kind / f"{args.ticker}_{args.bar}"
    out_dir.mkdir(parents=True, exist_ok=True)
    zones.to_parquet(out_dir / f"{args.ticker}_{args.bar}_zones_v0.parquet", index=False)

    report = {
        "params": p.to_dict(),
        "summary": summary(zones, bars),
        "expansion": expansion_check(zones, bars, horizon=p.n),
        "continuation_reference": continuation_check(zones, df, horizon=p.n),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))

    if args.chart_out:
        title = (f"{args.ticker} {args.bar}: зоны консолидации v0 "
                 f"(n={p.n}, box<{p.c_box}, ER·√n<{p.c_er}, сжатие<{p.c_comp}, δ={p.delta})")
        make_chart(df, zones, args.chart_days, title, Path(args.chart_out))


if __name__ == "__main__":
    main()
