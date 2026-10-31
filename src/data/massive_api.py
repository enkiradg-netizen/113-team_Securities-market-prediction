"""
Запросы к API Massive.

Ключ берётся из файла .env (в git он не попадает),
шаблон ключей — .env.example.
"""

import requests
from dotenv import dotenv_values

from src.data.project_config import PROJECT_DIR

API_URL = "https://api.massive.com"


def get_api_key() -> str:
    """Берёт ключ Massive из файла .env."""
    key = dotenv_values(PROJECT_DIR / ".env").get("MASSIVE_API_KEY")
    if not key:
        raise ValueError("В .env нет MASSIVE_API_KEY. Скопируйте .env.example в .env.")
    return key


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
        response = requests.get(url, params=params, headers=headers, timeout=120)
        response.raise_for_status()
        data = response.json()
        results += data.get("results", [])
        url = data.get("next_url")
        params = None  # в next_url параметры уже есть
    return results
