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
    - пароль от диска — скрипт спросит его при запуске.

Запуск (из корня проекта):
    python -m src.data.get_data                     # всё
    python -m src.data.get_data processed           # только склеенные ряды
    python -m src.data.get_data raw/futures/bars    # только бары
"""

import argparse

from src.data.project_config import PROJECT_DIR
from src.data.upload_data import DATA_DIR, get_storage_box, run_rsync


def download(box: str, part: str) -> None:
    """
    Запускает rsync: data/<part> на диске → data/<part> проекта.

    -a — копировать с подпапками, сохраняя время изменения файлов;
    --partial — недокачанный файл не выбрасывать, а докачать в следующий раз;
    -e "ssh -p 23" — Storage Box принимает rsync на порту 23.

    Субаккаунт команды (логин вида uXXXXXX-subX) видит только папку data,
    поэтому путь на диске у него начинается сразу с raw/, interim/ или
    processed/. Главный аккаунт видит весь диск, ему нужна приставка data/.
    """
    part = part.strip("/")
    local_dir = DATA_DIR / part if part else DATA_DIR
    local_dir.mkdir(parents=True, exist_ok=True)
    login = box.split("@")[0]
    prefix = "" if "-sub" in login else "data/"
    remote_dir = f"{prefix}{part}/" if part else f"{prefix}./"
    command = [
        "rsync", "-a", "--partial", "--info=stats1,progress2", "-e", "ssh -p 23",
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
