"""
Скачивание данных проекта с общего сетевого диска (Hetzner Storage Box).

Для команды и проверяющих: качать из Massive ничего не нужно,
готовые данные лежат на общем диске. Скрипт копирует их в папку
data/ проекта с той же структурой, где их ждут остальные скрипты.

Копирует программа rsync: уже скачанные файлы повторно не передаются,
а после обрыва скачивание продолжается с того же места.

Нужно:
    - программа rsync (Linux: sudo apt install rsync, Mac: есть сразу);
    - в .env строка STORAGE_BOX=логин@адрес (шаблон в .env.example);
    - пароль от диска (скрипт спросит его при запуске) или файл-ключ:
      путь к нему в .env, строка STORAGE_BOX_KEY.

Запуск (из корня проекта):
    python -m src.data.get_data                     # всё
    python -m src.data.get_data processed           # готовые ряды, без сделок
    python -m src.data.get_data raw/futures/bars    # только бары

Чтобы получить данные только своих инструментов, удобнее команда
python -m src.data.pipeline: она спросит, что нужно, и возьмёт только эти файлы.
"""

import argparse

from src.data.project_config import PROJECT_DIR
from src.data.upload_data import DATA_DIR, get_storage_box, run_rsync, ssh_command


def remote_prefix(box: str) -> str:
    """
    Субаккаунт команды (логин вида uXXXXXX-subX) видит только папку data,
    поэтому путь на диске у него начинается сразу с raw/, interim/ или
    processed/. Главный аккаунт видит весь диск, ему нужна приставка data/.
    """
    login = box.split("@")[0]
    return "" if "-sub" in login else "data/"


def download_matching(box: str, patterns: list) -> None:
    """
    Скачивает с диска только файлы, подходящие под шаблоны, например /processed/stocks/NVDA_*.

    --include и --exclude оставляют только нужные файлы, а -m не создаёт пустые папки.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    filters = ["--include=*/", *[f"--include={pattern}" for pattern in patterns], "--exclude=*"]
    command = [
        "rsync", "-a", "-m", "--partial", "--info=stats1,progress2", "-e", ssh_command(), *filters,
        f"{box}:{remote_prefix(box)}./", f"{DATA_DIR}/",
    ]
    run_rsync(command)
    print(f"Готово: {DATA_DIR.relative_to(PROJECT_DIR)}")


def download(box: str, part: str) -> None:
    """
    Запускает rsync: data/<part> на диске → data/<part> проекта.

    -a — копировать с подпапками, сохраняя время изменения файлов;
    --partial — недокачанный файл не выбрасывать, а докачать в следующий раз;
    --exclude .ssh — не скачивать файл с ключами доступа: он лежит прямо в папке данных;
    -e — как подключаться к диску (см. ssh_command).

    Папка processed без сделок: они весят десятки гигабайт, их просят отдельно,
    командой с processed/futures_trades.
    """
    part = part.strip("/")
    local_dir = DATA_DIR / part if part else DATA_DIR
    local_dir.mkdir(parents=True, exist_ok=True)
    prefix = remote_prefix(box)
    remote_dir = f"{prefix}{part}/" if part else f"{prefix}./"
    skip_trades = ["--exclude", "futures_trades"] if part == "processed" else []
    command = [
        "rsync", "-a", "--partial", "--exclude", ".ssh", *skip_trades, "--info=stats1,progress2", "-e", ssh_command(),
        f"{box}:{remote_dir}", f"{local_dir}/",
    ]
    run_rsync(command)
    print(f"Готово: {local_dir.relative_to(PROJECT_DIR)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Скачивание данных проекта с общего сетевого диска.")
    parser.add_argument("part", nargs="?", default="",
                        help="какую папку скачать, например processed или raw/futures/bars (по умолчанию всё)")
    args = parser.parse_args()
    download(get_storage_box(), args.part)


if __name__ == "__main__":
    main()
