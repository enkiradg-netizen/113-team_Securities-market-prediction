import json
from pathlib import Path


def load_config() -> dict:
    """Читает настройки из config.json рядом со скриптом."""
    path = Path(__file__).resolve().parent / "config.json"
    with path.open(encoding="utf-8") as file:
        return json.load(file)


if __name__ == "__main__":
    config = load_config()
    print(json.dumps(config, ensure_ascii=False, indent=2))
