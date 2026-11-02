"""
Выгрузка данных проекта на общий сетевой диск (Hetzner Storage Box).

Делает две копии:
    data/ проекта  → data/ на диске — рабочие данные для команды;
    data/raw/      → backup/raw/ на диске — страховка. Сюда у команды
                     доступа нет: её логин видит только папку data.
Страховка нужна для сырых данных: когда закончится подписка Massive,
заново их не скачать. Планы и склейку можно пересчитать скриптами.

Команда может менять файлы в data/ на диске, а своё кладёт в data/team/.
Эту папку скрипт не трогает (у нас локально её нет).

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
import shutil
import subprocess
from pathlib import Path

from dotenv import dotenv_values

from src.data.project_config import PROJECT_DIR

DATA_DIR = PROJECT_DIR / "data"


def get_storage_box() -> str:
    """Берёт адрес диска (логин@адрес) из файла .env."""
    box = dotenv_values(PROJECT_DIR / ".env").get("STORAGE_BOX")
    if not box:
        raise SystemExit("В .env нет STORAGE_BOX. Пример строки — в .env.example.")
    return box


def run_rsync(command: list) -> None:
    """Запускает rsync и понятно сообщает, если что-то пошло не так."""
    if shutil.which("rsync") is None:
        raise SystemExit("Нет программы rsync. Linux: sudo apt install rsync. На Windows данные можно скачать через WinSCP (см. README).")
    if subprocess.run(command).returncode != 0:
        raise SystemExit("rsync завершился с ошибкой. Проверьте логин в .env, пароль и интернет и запустите ту же команду ещё раз.")


def upload(box: str, local_dir: Path, remote_dir: str, dry_run: bool) -> None:
    """
    Запускает rsync: папка проекта → папка на диске.

    -a — копировать с подпапками, сохраняя время изменения файлов;
    --partial — недокачанный файл не выбрасывать, а докачать в следующий раз;
    --exclude *.tmp — не выгружать временные файлы, которые ещё пишутся;
    --mkpath — создать папку на диске, если её ещё нет;
    -e "ssh -p 23" — Storage Box принимает rsync на порту 23.
    """
    print(f"{local_dir} → {remote_dir}")
    command = [
        "rsync", "-a", "--partial", "--mkpath", "--exclude", "*.tmp",
        "--info=stats1,progress2", "-e", "ssh -p 23",
        f"{local_dir}/", f"{box}:{remote_dir}/",
    ]
    if dry_run:
        command.insert(1, "--dry-run")
        command.insert(2, "--itemize-changes")
    run_rsync(command)


def main() -> None:
    parser = argparse.ArgumentParser(description="Выгрузка data/ на общий сетевой диск.")
    parser.add_argument("--dry-run", action="store_true", help="только показать, что будет выгружено")
    args = parser.parse_args()
    box = get_storage_box()
    upload(box, DATA_DIR, "data", args.dry_run)
    upload(box, DATA_DIR / "raw", "backup/raw", args.dry_run)


if __name__ == "__main__":
    main()
