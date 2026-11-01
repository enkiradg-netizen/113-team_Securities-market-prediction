"""
Выгрузка данных проекта на общий сетевой диск (Hetzner Storage Box).

Копирует папку data/ проекта на диск в папку data/ с той же структурой.
Копирует программа rsync: уже выгруженные файлы повторно не передаются,
а после обрыва выгрузка продолжается с того же места.

На диске ничего не удаляется, даже если файл удалили локально:
так случайная ошибка у себя не испортит общие данные.

Запускаем руками и только после проверки данных, чтобы на диск
не попало непроверенное.

Нужно:
    - программа rsync (sudo apt install rsync);
    - в .env строка STORAGE_BOX=логин@адрес (шаблон в .env.example);
    - SSH-ключ этого компьютера добавлен на диск.

Запуск (из корня проекта):
    python -m src.data.upload_data --dry-run   # только показать, что будет выгружено
    python -m src.data.upload_data
"""

import argparse
import subprocess

from dotenv import dotenv_values

from src.data.project_config import PROJECT_DIR

DATA_DIR = PROJECT_DIR / "data"


def get_storage_box() -> str:
    """Берёт адрес диска (логин@адрес) из файла .env."""
    box = dotenv_values(PROJECT_DIR / ".env").get("STORAGE_BOX")
    if not box:
        raise ValueError("В .env нет STORAGE_BOX. Пример строки — в .env.example.")
    return box


def upload(box: str, dry_run: bool) -> None:
    """
    Запускает rsync: data/ проекта → data/ на диске.

    -a — копировать с подпапками, сохраняя время изменения файлов;
    --partial — недокачанный файл не выбрасывать, а докачать в следующий раз;
    --exclude *.tmp — не выгружать временные файлы, которые ещё пишутся;
    -e "ssh -p 23" — Storage Box принимает rsync на порту 23.
    """
    command = [
        "rsync", "-a", "--partial", "--exclude", "*.tmp",
        "--info=stats1,progress2", "-e", "ssh -p 23",
        f"{DATA_DIR}/", f"{box}:data/",
    ]
    if dry_run:
        command.insert(1, "--dry-run")
        command.insert(2, "--itemize-changes")
    subprocess.run(command, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Выгрузка data/ на общий сетевой диск.")
    parser.add_argument("--dry-run", action="store_true", help="только показать, что будет выгружено")
    args = parser.parse_args()
    upload(get_storage_box(), args.dry_run)


if __name__ == "__main__":
    main()
