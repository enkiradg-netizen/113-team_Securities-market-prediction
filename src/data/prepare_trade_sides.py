"""
Расчётная сторона сделок фьючерсов: кто начал сделку, покупатель или продавец.

В данных Massive стороны сделки нет, поэтому считаем её сами по котировкам
из Flat Files. Сделка по цене ask и выше — покупка, по цене bid и ниже — продажа.
Котировку берём строго раньше сделки. Если цена внутри спреда, смотрим, к чему
она ближе, а ровно посередине берём правило тика: сравниваем с прошлой ценой.

Котировки весят около 4,5 ТБ, поэтому их не храним. День биржи читается потоком,
а файл сразу удаляется. К каждой сделке сохраняем только bid и ask на её момент.

Сделки начинают считать с самых старых дней: Massive отдаёт только последние
5 лет, и старые дни пропадут первыми. Если скрипт остановился, запустите ту же
команду ещё раз: готовые дни считаться заново не будут.

Нужны ключи к файлам в .env (шаблон в .env.example) и сделки в data/raw/futures/trades.

Запуск (из корня проекта):
    python -m src.data.prepare_trade_sides                                  # все фьючерсы
    python -m src.data.prepare_trade_sides --product CL NG
    python -m src.data.prepare_trade_sides --product CL --days 2025-07-10   # для проверки

Результат: data/processed/futures_trades/<код>/<контракт>/<ГГГГ-ММ>.parquet
Месячный файл собирается, когда готовы все его дни. Столбцы:
    как в сделках Massive: ticker, timestamp (нс, UTC), sequence_number, report_sequence,
        price, size, session_end_date;
    bid_price, ask_price, bid_size, ask_size — последняя котировка строго до сделки;
    est_side — сторона: 1 покупка, -1 продажа, 0 не определена;
    side_method — как определена: 1 по котировке, 2 по близости к bid или ask, 3 по тику;
    maybe_implied — возможно, сделка от спреда между контрактами, без своей стороны.

Готовые файлы для всех фьючерсов за весь период лежат на общем диске. В них
ещё есть side_method 4 и 5: там дыры Massive дополнены из другого источника.
Повторный расчёт через Massive их не даёт.
"""

import argparse
import shutil
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pcc
import pyarrow.csv as pc
import pyarrow.parquet as pq
import urllib3
from minio.error import S3Error

from src.data.massive_api import get_files_client
from src.data.project_config import INTERIM_DIR, PROCESSED_DIR, RAW_DIR, futures_to_process, load_instruments

RAW_TRADES_DIR = RAW_DIR / "futures" / "trades"
DAYS_DIR = INTERIM_DIR / "futures_trade_sides"
OUT_DIR = PROCESSED_DIR / "futures_trades"

MAX_QUOTE_AGE_NS = 60 * 10**9  # котировка старше 60 секунд и без обновления после прошлой сделки считается потерянной
MIN_FREE_MEMORY_GB = 3  # если свободно меньше, ждём, а не рискуем зависанием сервера
MIN_FREE_DISK_GB = 30  # если места меньше, останавливаемся
PART_SIZE, PARALLEL_PARTS = 8 * 2**20, 10  # файл дня качаем кусками по 8 МБ, по 10 одновременно
MAX_TRIES, RETRY_PAUSE = 8, 30  # попыток на один кусок и секунд между ними

# Столбцы файлов Massive и их типы. Типы задаём сами, чтобы они не менялись от дня к дню.
QUOTE_TYPES = {"ticker": pa.string(), "timestamp": pa.int64(), "sequence_number": pa.int64(),
               "bid_price": pa.float64(), "ask_price": pa.float64(), "bid_size": pa.int64(), "ask_size": pa.int64()}
TRADE_TYPES = {"ticker": pa.string(), "timestamp": pa.int64(), "sequence_number": pa.int64(),
               "report_sequence": pa.int64(), "price": pa.float64(), "size": pa.int64(),
               "session_end_date": pa.string()}

# Как определена сторона (столбец side_method).
BY_QUOTE, BY_NEAREST, BY_TICK = 1, 2, 3

# Столбцы готового файла.
OUT_SCHEMA = pa.schema([
    ("ticker", pa.string()), ("timestamp", pa.int64()), ("sequence_number", pa.int64()),
    ("report_sequence", pa.int64()), ("price", pa.float64()), ("size", pa.int64()),
    ("session_end_date", pa.string()), ("bid_price", pa.float64()), ("ask_price", pa.float64()),
    ("bid_size", pa.int64()), ("ask_size", pa.int64()), ("est_side", pa.int8()),
    ("side_method", pa.int8()), ("maybe_implied", pa.bool_()),
])


def contract_days(codes: list) -> pd.DataFrame:
    """Какие контракты и дни есть в сохранённых сделках: столбцы code, contract, month, day."""
    rows = []
    for code in codes:
        for file in sorted((RAW_TRADES_DIR / code).glob("*/*.parquet")):
            days = pq.read_table(file, columns=["session_end_date"]).column("session_end_date").unique().to_pylist()
            rows += [(code, file.parent.name, file.stem, day) for day in days]
    return pd.DataFrame(rows, columns=["code", "contract", "month", "day"])


def check_resources() -> None:
    """Ждёт, если мало памяти, и останавливает скрипт, если мало места на диске."""
    if shutil.disk_usage(INTERIM_DIR).free / 2**30 < MIN_FREE_DISK_GB:
        raise SystemExit(f"На диске меньше {MIN_FREE_DISK_GB} ГБ. Освободите место и запустите команду ещё раз.")
    while True:
        try:
            with open("/proc/meminfo") as file:
                free = int(file.read().split("MemAvailable:")[1].split()[0]) / 2**20
        except FileNotFoundError:  # этот файл есть только на Linux, на других системах память не проверяем
            return
        if free >= MIN_FREE_MEMORY_GB:
            return
        print(f"  Свободно памяти {free:.1f} ГБ, жду минуту", flush=True)
        time.sleep(60)


def download_part(client, key: str, path, start: int, length: int) -> None:
    """Скачивает один кусок файла и записывает его на своё место. При сбое ждёт и повторяет."""
    for attempt in range(1, MAX_TRIES + 1):
        try:
            response = client.get_object("flatfiles", key, offset=start, length=length)
            data = response.read()
            response.close()
            response.release_conn()
            if len(data) == length:
                with open(path, "r+b") as file:
                    file.seek(start)
                    file.write(data)
                return
            problem = f"пришло {len(data)} байт из {length}"
        except (S3Error, urllib3.exceptions.HTTPError, ConnectionError) as error:
            problem = type(error).__name__
        print(f"    {key}: {problem}, попытка {attempt} из {MAX_TRIES}", flush=True)
        time.sleep(RETRY_PAUSE)
    raise RuntimeError(f"{key}: кусок с байта {start} не скачался за {MAX_TRIES} попыток")


def download_day_file(client, exchange: str, kind: str, day: str, path) -> None:
    """
    Скачивает файл одной биржи за один день, kind — это quotes_v1 или trades_v1.

    Файл котировок весит до нескольких ГБ. Одним запросом Massive отдаёт его очень
    медленно и обрывает, поэтому качаем кусками по 8 МБ, по 10 кусков одновременно.
    """
    key = f"us_futures_{exchange}/{kind}/{day[:4]}/{day[5:7]}/{day}.csv.gz"
    try:
        total = client.stat_object("flatfiles", key).size
    except S3Error as error:
        raise RuntimeError(f"Massive не отдал {key}: {error.code}. "
                           "AccessDenied бывает, если день старше 5 лет или ключ не подходит.") from error
    with open(path, "wb") as file:
        file.truncate(total)  # сразу файл нужного размера, куски встают каждый на своё место
    starts = range(0, total, PART_SIZE)
    with ThreadPoolExecutor(max_workers=PARALLEL_PARTS) as pool:
        jobs = [pool.submit(download_part, client, key, path, start, min(PART_SIZE, total - start)) for start in starts]
        for job in jobs:
            job.result()  # если кусок так и не скачался, здесь будет его ошибка


def split_quotes(client, exchange: str, day: str, tickers: list, work_dir) -> None:
    """
    Читает котировки биржи за день потоком и раскладывает нужные контракты
    по отдельным файлам. Так в памяти никогда не лежит весь день биржи.
    """
    archive = work_dir / "quotes.csv.gz"
    download_day_file(client, exchange, "quotes_v1", day, archive)
    reader = pc.open_csv(archive, read_options=pc.ReadOptions(block_size=32 * 2**20),
                         convert_options=pc.ConvertOptions(include_columns=list(QUOTE_TYPES), column_types=QUOTE_TYPES))
    writers = {}
    wanted = pa.array(tickers)
    for batch in reader:
        part = batch.filter(pcc.is_in(batch.column("ticker"), value_set=wanted))
        for ticker in pcc.unique(part.column("ticker")).to_pylist():
            rows = pa.Table.from_batches([part.filter(pcc.equal(part.column("ticker"), ticker))])
            if ticker not in writers:
                writers[ticker] = pq.ParquetWriter(work_dir / f"{ticker}.parquet", rows.schema)
            writers[ticker].write_table(rows)
    for writer in writers.values():
        writer.close()
    archive.unlink()


def read_trades(client, exchange: str, day: str, work_dir) -> pd.DataFrame:
    """Читает все сделки биржи за день: и наши контракты, и всё, что торговалось одновременно с ними."""
    archive = work_dir / "trades.csv.gz"
    download_day_file(client, exchange, "trades_v1", day, archive)
    table = pc.read_csv(archive, convert_options=pc.ConvertOptions(include_columns=list(TRADE_TYPES), column_types=TRADE_TYPES))
    archive.unlink()
    trades = table.to_pandas(strings_to_categorical=True)
    return trades[trades["session_end_date"] == day]


def several_instruments(trades: pd.DataFrame) -> pd.DataFrame:
    """Объём каждого инструмента в те наносекунды, когда сделки прошли сразу в нескольких инструментах."""
    pairs = trades[["timestamp", "ticker"]].drop_duplicates()
    moments = pairs.loc[pairs["timestamp"].duplicated(keep=False), "timestamp"].unique()
    chosen = trades[trades["timestamp"].isin(moments)]
    return chosen.groupby(["timestamp", "ticker"], observed=True)["size"].sum().reset_index()


def add_sides(trades: pd.DataFrame, quotes: pd.DataFrame, others: pd.DataFrame) -> pd.DataFrame:
    """
    Добавляет сделкам одного контракта котировку и расчётную сторону.

    others — объёмы других инструментов (см. several_instruments). Биржа может собрать
    встречную заявку из других контрактов и спредов. Тогда агрессор один, а сделки проходят
    сразу в нескольких инструментах. Такие сделки не убираем, а только помечаем maybe_implied.
    """
    trades = trades.sort_values(["timestamp", "sequence_number", "report_sequence"]).reset_index(drop=True)
    quotes = quotes.sort_values(["timestamp", "sequence_number"]).drop_duplicates("timestamp", keep="last")
    columns = ["timestamp", "quote_time", "bid_price", "ask_price", "bid_size", "ask_size"]
    table = pd.merge_asof(trades, quotes.assign(quote_time=quotes["timestamp"])[columns], on="timestamp", allow_exact_matches=False)

    # В файлах Massive котировки иногда обрываются посреди дня, а сделки идут дальше.
    # После сделки биржа обычно обновляет котировку, поэтому старая и не обновлённая
    # с прошлой сделки котировка значит обрыв. Такую не используем: остаётся правило тика.
    moments = table["timestamp"].drop_duplicates()
    previous_trade = table["timestamp"].map(pd.Series(moments.shift().values, index=moments.values))
    lost = (table["quote_time"] < previous_trade) & (table["timestamp"] - table["quote_time"] > MAX_QUOTE_AGE_NS)
    table.loc[lost, ["bid_price", "ask_price", "bid_size", "ask_size"]] = np.nan

    price = table["price"].values
    bid = table["bid_price"].values
    ask = table["ask_price"].values
    middle = (bid + ask) / 2
    tick = np.sign(table["price"].diff()).replace(0, np.nan).ffill().fillna(0).values
    table["est_side"] = np.select([price >= ask, price <= bid, price > middle, price < middle], [1, -1, 1, -1], tick).astype("int8")
    table["side_method"] = np.select([price >= ask, price <= bid, price > middle, price < middle],
                                     [BY_QUOTE, BY_QUOTE, BY_NEAREST, BY_NEAREST], BY_TICK).astype("int8")

    # Вероятная сделка без своего агрессора: наш объём в этот момент не больше, чем у другого инструмента.
    own_size = table.groupby("timestamp")["size"].transform("sum").values
    biggest = others[others["ticker"] != table["ticker"].iloc[0]].groupby("timestamp")["size"].max()
    other_size = table["timestamp"].map(biggest).fillna(0).values
    table["maybe_implied"] = (other_size > 0) & (own_size <= other_size)
    return table


def process_exchange_day(exchange: str, day: str, contracts: pd.DataFrame) -> None:
    """Считает сторону для всех контрактов одной биржи за один день."""
    need = [row for row in contracts.itertuples()
            if not (DAYS_DIR / row.code / row.contract / f"{day}.parquet").exists()]
    if not need:
        return
    started = time.time()
    client = get_files_client()
    work_dir = DAYS_DIR / "_work" / f"{exchange}_{day}"
    shutil.rmtree(work_dir, ignore_errors=True)  # остатки прошлого обрыва
    work_dir.mkdir(parents=True)
    try:
        split_quotes(client, exchange, day, [row.contract for row in need], work_dir)
        all_trades = read_trades(client, exchange, day, work_dir)
        others = several_instruments(all_trades)
        total = 0
        for row in need:
            check_resources()
            trades = all_trades[all_trades["ticker"] == row.contract].copy()
            if trades.empty:
                print(f"  ВНИМАНИЕ: {row.contract} {day}: в файле Massive нет сделок", flush=True)
                continue
            trades["ticker"] = row.contract
            trades["session_end_date"] = day
            quotes_file = work_dir / f"{row.contract}.parquet"
            if quotes_file.exists():
                quotes = pq.read_table(quotes_file).to_pandas()
            else:
                print(f"  ВНИМАНИЕ: {row.contract} {day}: нет котировок, сторона только по тику", flush=True)
                quotes = pd.DataFrame({name: pd.Series(dtype="float64" if "price" in name else "int64")
                                       for name in QUOTE_TYPES if name != "ticker"})
            table = add_sides(trades, quotes, others)
            del quotes, trades
            out_file = DAYS_DIR / row.code / row.contract / f"{day}.parquet"
            out_file.parent.mkdir(parents=True, exist_ok=True)
            tmp_file = out_file.with_suffix(".parquet.tmp")
            pq.write_table(pa.Table.from_pandas(table, schema=OUT_SCHEMA, preserve_index=False), tmp_file)
            tmp_file.rename(out_file)
            total += len(table)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
    print(f"  {exchange} {day}: контрактов {len(need)}, сделок {total}, {time.time() - started:.0f} с", flush=True)


def assemble_month(code: str, contract: str, month: str, days: list) -> str:
    """
    Собирает месячный файл из готовых дней и сверяет каждый день с исходным файлом сделок:
    число сделок и объём должны совпасть. Возвращает: done, wait или bad.
    """
    out_file = OUT_DIR / code / contract / f"{month}.parquet"
    if out_file.exists():
        return "done"
    day_files = [DAYS_DIR / code / contract / f"{day}.parquet" for day in sorted(days)]
    if not all(file.exists() for file in day_files):
        return "wait"
    raw = pd.read_parquet(RAW_TRADES_DIR / code / contract / f"{month}.parquet", columns=["session_end_date", "size"])
    expected = raw.groupby("session_end_date")["size"].agg(["count", "sum"])
    out_file.parent.mkdir(parents=True, exist_ok=True)
    tmp_file = out_file.with_suffix(".parquet.tmp")
    with pq.ParquetWriter(tmp_file, OUT_SCHEMA) as writer:
        for file in day_files:
            table = pq.read_table(file, schema=OUT_SCHEMA)
            day = file.stem
            size = pcc.sum(table.column("size")).as_py()
            if table.num_rows != expected.loc[day, "count"] or size != expected.loc[day, "sum"]:
                print(f"  ОШИБКА: {contract} {day}: сделок {table.num_rows} и объём {size}, "
                      f"а в исходном файле {expected.loc[day, 'count']} и {expected.loc[day, 'sum']}", flush=True)
                tmp_file.unlink(missing_ok=True)
                return "bad"
            writer.write_table(table)
    tmp_file.rename(out_file)
    return "done"


def main() -> None:
    parser = argparse.ArgumentParser(description="Расчётная сторона сделок фьючерсов по котировкам Massive.")
    who = parser.add_mutually_exclusive_group()
    who.add_argument("--member", help="имя участника из config/instruments.yaml, например iura")
    who.add_argument("--product", nargs="+", help="коды фьючерсов, например CL NG; без параметров считаются все")
    parser.add_argument("--days", nargs="+", help="только эти дни, для проверки, например 2025-07-10")
    args = parser.parse_args()

    config = load_instruments()
    all_futures = [item for group in config["futures"].values() for item in group]
    futures = futures_to_process(config, args.member, args.product) if args.member or args.product else all_futures
    exchange_of = {item["ticker"]: item["exchange"].lower() for item in futures}

    get_files_client()  # проверяем ключи сразу, чтобы не ждать первого дня
    plan = contract_days(list(exchange_of))
    if plan.empty:
        raise SystemExit("Нет сделок в data/raw/futures/trades. Сначала получите их: python -m src.data.pipeline")
    plan["exchange"] = plan["code"].map(exchange_of)

    DAYS_DIR.mkdir(parents=True, exist_ok=True)
    errors = 0
    chosen = plan[plan["day"].isin(args.days)] if args.days else plan
    for day in sorted(chosen["day"].unique()):  # от старых дней к новым
        for exchange, contracts in chosen[chosen["day"] == day].groupby("exchange"):
            check_resources()
            try:
                # Отдельный процесс на каждый день биржи: когда он заканчивается,
                # вся его память возвращается системе и не копится за сотни дней.
                with ProcessPoolExecutor(max_workers=1) as pool:
                    pool.submit(process_exchange_day, exchange, day, contracts).result()
            except Exception as error:  # один плохой день не должен останавливать остальные
                errors += 1
                print(f"  ОШИБКА: {exchange} {day}: {type(error).__name__}: {error}", flush=True)

    results = [assemble_month(code, contract, month, group["day"].tolist())
               for (code, contract, month), group in plan.groupby(["code", "contract", "month"])]
    bad = results.count("bad")
    print(f"Месячных файлов готово: {results.count('done')}, ждут дней: {results.count('wait')}, "
          f"не сошлись с исходными: {bad}. Ошибок дней: {errors}.")
    if errors or bad:
        raise SystemExit("Не всё сошлось, смотрите строки ОШИБКА выше. "
                         "Запустите ту же команду ещё раз: готовые дни считаться заново не будут.")


if __name__ == "__main__":
    main()
