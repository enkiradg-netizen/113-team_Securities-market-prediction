"""
Страница просмотра склеенных фьючерсов: свечи, объём и дни склейки.

Что делает страница:
1. Даёт выбрать фьючерс, таймфрейм и период.
2. Читает склеенный ряд из data/processed/futures_continuous.
3. Бары с одним временем объединяет в одну свечу (график не умеет рисовать
   две свечи в одной точке). Файлы при этом не меняются.
4. Находит дни склейки: это место, где в ряду меняется контракт.
5. Рисует свечи и объём библиотекой TradingView Lightweight Charts:
   она рассчитана на большое число свечей, Plotly на таких объёмах тормозит.

Запуск из корня проекта:
    streamlit run src/service/chart_app.py

Если данных ещё нет, сначала: python -m src.data.get_data processed
"""

import json
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

# Streamlit запускает этот файл отдельно, поэтому корень проекта
# добавляем в пути сами, иначе не найдётся пакет src.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.data.project_config import END, FUTURES_BAR_SIZES, PROCESSED_DIR, START, load_instruments

SERIES_DIR = PROCESSED_DIR / "futures_continuous"

# Дневной таймфрейм есть только на странице: свечи собираются из часовых баров.
DAY_BAR = "1day"
DAY_SOURCE = "1hour"

# Точная версия библиотеки графика, чтобы страница не менялась сама по себе.
CHART_LIBRARY = "https://cdn.jsdelivr.net/npm/lightweight-charts@5.2.1/dist/lightweight-charts.standalone.production.js"

CHART_HEIGHT = 680  # высота графика в пикселях
VISIBLE_CANDLES = 300  # сколько свечей видно сразу после открытия графика

# Цвета: рост, падение и всё, что относится к склейке.
UP_COLOR = "#0f9d8a"
DOWN_COLOR = "#d6455d"
ROLL_COLOR = "#3b5bdb"

# Сам график: HTML и немного JavaScript. Вместо __DATA__ подставляются данные.
CHART_HTML = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
  html, body { margin: 0; height: 100%; background: #ffffff; }
  #chart { position: absolute; inset: 0; }
  #legend { position: absolute; top: 8px; left: 12px; z-index: 10; pointer-events: none;
            font: 13px/1.5 sans-serif; color: #1f2933; background: rgba(255, 255, 255, 0.85); padding: 2px 6px; }
  #legend .muted { color: #5f6b7a; }
  #error { padding: 16px; font: 14px sans-serif; color: #1f2933; }
</style>
</head>
<body>
<div id="chart"></div>
<div id="legend"></div>
<script src="__LIBRARY__"></script>
<script>
const d = __DATA__;

if (typeof LightweightCharts === "undefined") {
  document.body.innerHTML = '<div id="error">Не загрузилась библиотека графика. Проверьте интернет и обновите страницу.</div>';
  throw new Error("нет библиотеки графика");
}

const chart = LightweightCharts.createChart(document.getElementById("chart"), {
  autoSize: true,
  layout: { background: { color: "#ffffff" }, textColor: "#1f2933" },
  grid: { vertLines: { color: "#eef1f4" }, horzLines: { color: "#eef1f4" } },
  // minBarSpacing ограничивает отдаление: не больше нескольких тысяч свечей на экране
  timeScale: { timeVisible: d.bar !== "__DAY_BAR__", secondsVisible: false, minBarSpacing: 0.1 },
  localization: { locale: "ru-RU" },
});

// Верхняя панель: свечи. Рост и падение различаются ещё и заливкой: свеча роста
// пустая внутри, поэтому их можно различить и без цвета.
const candles = chart.addSeries(LightweightCharts.CandlestickSeries, {
  upColor: "#ffffff", downColor: d.down,
  borderUpColor: d.up, borderDownColor: d.down,
  wickUpColor: d.up, wickDownColor: d.down,
  priceFormat: { type: "price", precision: d.decimals, minMove: Math.pow(10, -d.decimals) },
});

// Нижняя панель: объём. Первый бар нового контракта выделен цветом склейки.
const volume = chart.addSeries(LightweightCharts.HistogramSeries, {
  priceFormat: { type: "volume" },
}, 1);
chart.panes()[0].setStretchFactor(3);
chart.panes()[1].setStretchFactor(1);

const rollTimes = new Set(d.rolls.map(r => r.time));
const indexByTime = new Map();
const candleData = [];
const volumeData = [];
for (let i = 0; i < d.time.length; i++) {
  const t = d.time[i];
  indexByTime.set(t, i);
  candleData.push({ time: t, open: d.open[i], high: d.high[i], low: d.low[i], close: d.close[i] });
  const color = rollTimes.has(t) ? d.roll : (d.close[i] >= d.open[i] ? d.up : d.down);
  volumeData.push({ time: t, value: d.volume[i], color: color });
}
candles.setData(candleData);
volume.setData(volumeData);

// Метки дней склейки над свечами: стрелка и новый контракт.
LightweightCharts.createSeriesMarkers(candles, d.rolls.map(r => ({
  time: r.time, position: "aboveBar", shape: "arrowDown", color: d.roll, text: r.new,
})));
// Такая же стрелка над столбиком объёма: сам столбик при малом масштабе почти не виден.
LightweightCharts.createSeriesMarkers(volume, d.rolls.map(r => ({
  time: r.time, position: "aboveBar", shape: "arrowDown", color: d.roll,
})));

// Подпись слева сверху: значения свечи под курсором, без курсора последней свечи.
const legend = document.getElementById("legend");
const number = x => x.toLocaleString("ru-RU", { minimumFractionDigits: d.decimals, maximumFractionDigits: d.decimals });
function showLegend(i) {
  legend.innerHTML =
    `<b>${d.title}</b> <span class="muted">${d.bar}</span><br>` +
    `О ${number(d.open[i])} &nbsp;М ${number(d.high[i])} &nbsp;Н ${number(d.low[i])} &nbsp;З ${number(d.close[i])}<br>` +
    `<span class="muted">объём ${d.volume[i].toLocaleString("ru-RU")} · контракт ${d.contract[i]} · factor ${d.factor[i]}</span>`;
}
showLegend(d.time.length - 1);
chart.subscribeCrosshairMove(param => {
  const i = param.time === undefined ? d.time.length - 1 : indexByTime.get(param.time);
  showLegend(i);
});

// Сначала видны последние свечи, остальная история открывается сдвигом графика мышью.
const last = d.time.length - 1;
chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, last - __VISIBLE_CANDLES__), to: last + 5 });
</script>
</body>
</html>
"""


def futures_list() -> dict:
    """Фьючерсы из конфига, у которых есть файлы: код → подпись для списка."""
    names = {}
    for items in load_instruments()["futures"].values():
        for item in items:
            if list(SERIES_DIR.glob(f"{item['ticker']}_*.parquet")):
                names[item["ticker"]] = f"{item['ticker']} — {item['name_ru']}"
    return names


def merge_same_time(bars: pd.DataFrame) -> pd.DataFrame:
    """
    Объединяет бары с одним временем в одну свечу.

    Бар на границе двух торговых дней в рядах разделён на два с одним time.
    Цены уже скорректированы склейкой, поэтому их можно объединять даже на границе контрактов.
    """
    rules = {
        "session_end_date": "last", "contract": "last", "open": "first", "high": "max",
        "low": "min", "close": "last", "volume": "sum", "factor": "last",
    }
    return bars.groupby("time", as_index=False).agg(rules)


def merge_into_days(bars: pd.DataFrame) -> pd.DataFrame:
    """
    Собирает дневные свечи: одна свеча на торговый день (session_end_date).

    Торговый день фьючерса начинается вечером предыдущей даты, поэтому
    группируем именно по нему, а не по календарной дате из time.
    """
    rules = {
        "contract": "last", "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum", "factor": "last",
    }
    days = bars.groupby("session_end_date", as_index=False).agg(rules)
    days["time"] = pd.to_datetime(days["session_end_date"], utc=True)
    return days


def source_file(product: str, bar_size: str) -> Path:
    """Файл склеенного ряда, из которого строится выбранный таймфрейм."""
    source = DAY_SOURCE if bar_size == DAY_BAR else bar_size
    return SERIES_DIR / f"{product}_{source}.parquet"


@st.cache_data(max_entries=12)
def load_series(product: str, bar_size: str) -> pd.DataFrame:
    """Читает склеенный ряд и приводит его к свечам выбранного таймфрейма."""
    bars = pd.read_parquet(source_file(product, bar_size))
    if bar_size == DAY_BAR:
        return merge_into_days(bars)
    return merge_same_time(bars)


def find_rolls(series: pd.DataFrame) -> pd.DataFrame:
    """Дни склейки: свечи, на которых контракт отличается от предыдущей свечи."""
    changed = series["contract"] != series["contract"].shift()
    rolls = series.loc[changed, ["time", "session_end_date", "contract"]].copy()
    rolls["old"] = series["contract"].shift()[changed]
    return rolls.iloc[1:].rename(columns={"contract": "new"})


def unix_seconds(times: pd.Series) -> pd.Series:
    """Время в секундах от 1970 года: в таком виде его понимает график."""
    return times.dt.tz_localize(None).astype("datetime64[s]").astype("int64")


def price_decimals(prices: pd.Series) -> int:
    """Сколько знаков показывать у цены: у иены 0.007, а у индексов десятки тысяч."""
    level = prices.median()
    if level < 0.1:
        return 6
    if level < 10:
        return 4
    return 2


def chart_page(series: pd.DataFrame, rolls: pd.DataFrame, title: str, bar_size: str) -> str:
    """Собирает HTML графика вместе с данными выбранного периода."""
    data = {
        "title": title,
        "bar": bar_size,
        "decimals": price_decimals(series["close"]),
        "up": UP_COLOR,
        "down": DOWN_COLOR,
        "roll": ROLL_COLOR,
        "time": unix_seconds(series["time"]).tolist(),
        "open": series["open"].tolist(),
        "high": series["high"].tolist(),
        "low": series["low"].tolist(),
        "close": series["close"].tolist(),
        "volume": series["volume"].tolist(),
        "contract": series["contract"].tolist(),
        "factor": series["factor"].round(6).tolist(),
        "rolls": [
            {"time": int(time), "new": new}
            for time, new in zip(unix_seconds(rolls["time"]), rolls["new"])
        ],
    }
    page = CHART_HTML.replace("__LIBRARY__", CHART_LIBRARY).replace("__VISIBLE_CANDLES__", str(VISIBLE_CANDLES))
    page = page.replace("__DAY_BAR__", DAY_BAR)
    return page.replace("__DATA__", json.dumps(data))


def main() -> None:
    st.set_page_config(page_title="Склеенные фьючерсы", layout="wide")
    # Убираем большой пустой отступ над графиком.
    st.html('<style>[data-testid="stMainBlockContainer"] { padding: 1rem 1.5rem; }</style>')

    futures = futures_list()
    if not futures:
        st.error("Нет данных. Скачайте их из корня проекта: `python -m src.data.get_data processed`")
        st.stop()

    with st.sidebar:
        product = st.selectbox("Фьючерс", list(futures), format_func=futures.get)
        # По умолчанию часовые бары: подробно и не слишком тяжело.
        bar_sizes = [*FUTURES_BAR_SIZES, DAY_BAR]
        bar_size = st.selectbox("Таймфрейм", bar_sizes, index=bar_sizes.index("1hour"))
        date_from = st.date_input("С", START, min_value=START, max_value=END, format="DD.MM.YYYY")
        date_to = st.date_input("По", END, min_value=START, max_value=END, format="DD.MM.YYYY")
        st.caption(
            "График двигается мышью, колесо меняет масштаб. Время UTC.\n\n"
            "Цены скорректированы склейкой (ratio back-adjust): настоящая цена контракта = цена / factor.\n\n"
            "Синие стрелки и синий столбик объёма отмечают первый бар нового контракта.\n\n"
            "О, М, Н, З — открытие, максимум, минимум, закрытие.\n\n"
            "Свечи 1day собраны из часовых баров по торговому дню.\n\n"
            "Графики: [TradingView Lightweight Charts](https://www.tradingview.com/)"
        )

    if date_from > date_to:
        st.warning("Начало периода позже конца. Поправьте даты слева.")
        st.stop()

    path = source_file(product, bar_size)
    if not path.exists():
        st.error(f"Нет файла {path.name}. Скачайте данные: `python -m src.data.get_data processed`")
        st.stop()

    series = load_series(product, bar_size)
    all_rolls = find_rolls(series)

    # Даты торговые: тот же день, что и в session_end_date ряда.
    first, last = date_from.isoformat(), date_to.isoformat()
    shown = series[series["session_end_date"].between(first, last)]
    rolls = all_rolls[all_rolls["session_end_date"].between(first, last)]

    if shown.empty:
        st.warning("За этот период нет данных.")
        st.stop()

    # График слева, дни склейки справа: всё видно сразу, без прокрутки вниз.
    chart_column, rolls_column = st.columns([5, 1])
    with chart_column:
        st.iframe(chart_page(shown, rolls, futures[product], bar_size), height=CHART_HEIGHT)
    with rolls_column:
        st.caption(f"Дни склейки: {len(rolls)}")
        # Свежие сверху, как и график открывается на последних свечах.
        table = rolls.iloc[::-1].rename(columns={"session_end_date": "День", "new": "Контракт"})
        st.dataframe(table[["День", "Контракт"]], hide_index=True, height=CHART_HEIGHT - 40)


if __name__ == "__main__":
    main()
