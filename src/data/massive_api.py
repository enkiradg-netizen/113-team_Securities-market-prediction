"""
Запросы к API Massive.

Ключи берутся из файла .env (в git он не попадает): отдельно для фьючерсов
и для акций. Шаблон ключей — .env.example.
"""

import time

import requests
from dotenv import dotenv_values
from minio import Minio

from src.data.project_config import PROJECT_DIR

API_URL = "https://api.massive.com"
FILES_HOST = "files.massive.com"  # Flat Files: готовые файлы за день

# Временные ошибки сервера: на них стоит подождать и повторить запрос.
RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_TRIES = 5
RETRY_PAUSE = 30  # секунд между попытками


def get_api_key(asset: str) -> str:
    """
    Берёт ключ Massive из файла .env. Подписки на фьючерсы и акции разные,
    поэтому и ключи разные: asset — это "futures" или "stocks".
    """
    name = f"MASSIVE_API_KEY_{asset.upper()}"
    key = dotenv_values(PROJECT_DIR / ".env").get(name)
    if not key:
        raise SystemExit(f"В .env нет {name}. Шаблон ключей — в .env.example.")
    return key


def get_files_client() -> Minio:
    """
    Подключение к Flat Files Massive. Ключи к файлам отдельные от ключа API, они тоже лежат в .env.
    Файлы лежат в хранилище типа S3, скачиваем их библиотекой minio.
    """
    env = dotenv_values(PROJECT_DIR / ".env")
    access_key = env.get("MASSIVE_S3_ACCESS_KEY_FUTURES")
    secret_key = env.get("MASSIVE_S3_SECRET_KEY_FUTURES")
    if not access_key or not secret_key:
        raise SystemExit("В .env нет MASSIVE_S3_ACCESS_KEY_FUTURES или MASSIVE_S3_SECRET_KEY_FUTURES. "
                         "Шаблон ключей — в .env.example.")
    return Minio(FILES_HOST, access_key=access_key, secret_key=secret_key, secure=True)


def get_page(url: str, params: dict | None, headers: dict) -> dict:
    """
    Запрашивает одну порцию данных у Massive.

    При временной ошибке сервера или сети ждёт и повторяет запрос.
    Постоянная ошибка (например, неверный ключ) сразу останавливает скрипт.
    """
    for attempt in range(1, MAX_TRIES + 1):
        try:
            response = requests.get(url, params=params, headers=headers, timeout=120)
            if response.status_code in (401, 403):
                raise SystemExit(f"Massive отклонил ключ (код ответа {response.status_code}). "
                                 "Проверьте ключ в файле .env и подписку.")
            if response.status_code not in RETRY_STATUSES:
                response.raise_for_status()
                return response.json()
            problem = f"код ответа {response.status_code}"
        except (requests.ConnectionError, requests.Timeout) as error:
            problem = type(error).__name__
        print(f"    Massive: {problem}, попытка {attempt} из {MAX_TRIES}")
        if attempt < MAX_TRIES:
            time.sleep(RETRY_PAUSE)
    raise SystemExit(f"Massive не отвечает ({problem}). Запустите ту же команду ещё раз чуть позже.")


def massive_get_all(path: str, params: dict, api_key: str) -> list:
    """
    Делает запрос к Massive и собирает все страницы ответа.

    Massive отдаёт данные порциями. Если данных больше, в ответе есть
    next_url — ссылка на следующую порцию. Идём по ним, пока не кончатся.
    """
    headers = {"Authorization": f"Bearer {api_key}"}
    url = API_URL + path
    results = []
    while url:
        data = get_page(url, params, headers)
        results += data.get("results", [])
        url = data.get("next_url")
        params = None  # в next_url параметры уже есть
    return results
