"""
Одна команда, чтобы получить данные проекта.

Без параметров задаёт вопросы: откуда брать данные (с общего диска или через
API Massive), что брать (фьючерсы, акции или всё) и чьё (всё или только акции
и фьючерсы одного участника). Те же ответы можно передать параметрами, тогда
вопросов не будет и команду легко повторить.

С диска берутся готовые проверенные данные, это быстрее всего. Через API
результат получается такой же, но нужны ключи Massive и больше времени.

Сам скрипт ничего не считает, он только запускает обычные шаги по порядку:
    фьючерсы: contract_plan → download_bars → continuous → download_trades (по желанию);
    акции: download_stock_bars → prepare_stock_bars.

Запуск (из корня проекта):
    python -m src.data.pipeline
    python -m src.data.pipeline --source disk --asset stocks --member iura
    python -m src.data.pipeline --source api --asset futures --codes CL NG --trades no
"""

import argparse
import subprocess
import sys

from src.data.get_data import download_matching
from src.data.massive_api import get_api_key
from src.data.project_config import futures_to_process, load_instruments, stocks_to_process
from src.data.upload_data import get_storage_box


def ask(question: str, options: list, default: str | None = None) -> str:
    """Задаёт вопрос с вариантами и возвращает выбранный. options — [(значение, подпись), ...]."""
    print(f"\n{question}")
    for number, (value, label) in enumerate(options, start=1):
        print(f"  {number}. {label}{' (по умолчанию)' if value == default else ''}")
    while True:
        try:
            answer = input("Номер: ").strip()
        except EOFError:
            raise SystemExit("Нет ответа на вопрос. Передайте ответы параметрами, см. --help.")
        if not answer and default is not None:
            return default
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return options[int(answer) - 1][0]
        print(f"Введите число от 1 до {len(options)}.")


def all_futures(config: dict) -> list:
    """Коды всех фьючерсов из конфига."""
    return [item["ticker"] for group in config["futures"].values() for item in group]


def chosen_futures(config: dict, member: str | None, codes: list | None) -> list:
    """Коды выбранных фьючерсов: участника, перечисленные или все."""
    if member:
        return [item["ticker"] for item in futures_to_process(config, member, None)]
    return [item["ticker"] for item in futures_to_process(config, None, codes or all_futures(config))]


def chosen_stocks(config: dict, member: str | None, codes: list | None) -> list:
    """Тикеры выбранных акций: участника, перечисленные или все."""
    return [item["ticker"] for item in stocks_to_process(config, member, codes)]


def futures_patterns(codes: list, trades: bool) -> list:
    """Какие файлы фьючерсов брать с диска."""
    patterns = []
    for code in codes:
        patterns += [f"/processed/futures_continuous/{code}_*", f"/interim/futures_plan/{code}.csv",
                     f"/raw/futures/bars/*/{code}/**"]
        if trades:
            patterns.append(f"/raw/futures/trades/{code}/**")
    return patterns


def stocks_patterns(tickers: list) -> list:
    """Какие файлы акций брать с диска."""
    patterns = []
    for ticker in tickers:
        patterns += [f"/processed/stocks/{ticker}_*", f"/raw/stocks/bars/*/{ticker}.parquet",
                     f"/raw/stocks/splits/{ticker}.csv", f"/raw/stocks/dividends/{ticker}.csv"]
    return patterns


def run_step(module: str, arguments: list) -> None:
    """Запускает один шаг так же, как его запускают руками."""
    print(f"\n=== {module}")
    if subprocess.run([sys.executable, "-m", f"src.data.{module}", *arguments]).returncode != 0:
        raise SystemExit(f"Шаг {module} не прошёл. Исправьте причину и запустите ту же команду ещё раз: "
                         "готовое скачиваться заново не будет.")


def run_api_futures(member: str | None, codes: list, trades: bool) -> None:
    """Собирает фьючерсы через API: план, бары, склейка и, если нужно, сделки."""
    who = ["--member", member] if member else ["--product", *codes]
    for module in ["contract_plan", "download_bars", "continuous"]:
        run_step(module, who)
    if trades:
        run_step("download_trades", who)


def run_api_stocks(member: str | None, codes: list | None) -> None:
    """Собирает акции через API: бары, сплиты, дивиденды и готовые ряды."""
    who = ["--member", member] if member else ["--ticker", *codes] if codes else ["--all"]
    for module in ["download_stock_bars", "prepare_stock_bars"]:
        run_step(module, who)


def main() -> None:
    parser = argparse.ArgumentParser(description="Получить данные проекта: с диска или через API Massive.")
    parser.add_argument("--source", choices=["disk", "api"], help="откуда брать данные")
    parser.add_argument("--asset", choices=["futures", "stocks", "all"], help="что брать")
    who = parser.add_mutually_exclusive_group()
    who.add_argument("--member", help="только один участник из config/instruments.yaml, например iura")
    who.add_argument("--everyone", action="store_true", help="все инструменты")
    who.add_argument("--codes", nargs="+", help="только эти коды, с --asset futures или --asset stocks")
    parser.add_argument("--trades", choices=["yes", "no"], help="качать ли сделки фьючерсов (около 44 ГБ)")
    args = parser.parse_args()

    config = load_instruments()
    if args.codes and args.asset not in ("futures", "stocks"):
        raise SystemExit("С --codes укажите --asset futures или --asset stocks.")

    source = args.source or ask("Откуда брать данные?", [
        ("disk", "С общего диска: готовые проверенные данные, быстро"),
        ("api", "Через API Massive: нужны ключи, намного дольше"),
    ])
    asset = args.asset or ask("Что брать?", [
        ("all", "Фьючерсы и акции"), ("futures", "Только фьючерсы"), ("stocks", "Только акции"),
    ])
    member = args.member
    if not (member or args.everyone or args.codes):
        options = [("", "Все инструменты")]
        for name, groups in config["team"].items():
            options.append((name, f"Участник {name}: фьючерсы {', '.join(groups['futures'])}; акции {', '.join(groups['stocks'])}"))
        member = ask("Чьи данные?", options) or None

    want_futures = asset in ("futures", "all")
    want_stocks = asset in ("stocks", "all")
    trades = False
    if want_futures:
        answer = args.trades or ask("Качать сделки фьючерсов? Все вместе около 44 ГБ, через API это очень долго.",
                                    [("no", "Нет"), ("yes", "Да")], default="no")
        trades = answer == "yes"

    # Проверяем выбор и ключи сразу, чтобы не остановиться на полпути.
    futures_codes = chosen_futures(config, member, args.codes) if want_futures else []
    stock_tickers = chosen_stocks(config, member, args.codes) if want_stocks else []
    print(f"\nФьючерсов: {len(futures_codes)}, акций: {len(stock_tickers)}, сделки: {'да' if trades else 'нет'}")

    if source == "disk":
        patterns = futures_patterns(futures_codes, trades) + stocks_patterns(stock_tickers)
        download_matching(get_storage_box(), patterns)
    else:
        if want_futures:
            get_api_key("futures")
        if want_stocks:
            get_api_key("stocks")
        if want_futures:
            run_api_futures(member, futures_codes, trades)
        if want_stocks:
            run_api_stocks(member, args.codes)
    print("\nГотово. Данные лежат в папке data/.")


if __name__ == "__main__":
    main()
