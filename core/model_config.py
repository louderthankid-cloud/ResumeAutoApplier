import yaml
from pathlib import Path
from typing import Any

CONFIG_PATH = Path(__file__).parent.parent / "models.yaml"


class ModelConfig:
    _instance = None
    _config = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ModelConfig, cls).__new__(cls)
            cls._instance._load_config()
        return cls._instance

    def _load_config(self):
        if not CONFIG_PATH.exists():
            self._config = {
                "default": {
                    "provider": "ollama",
                    "model": "qwen2.5-coder:7b",
                    "temperature": 0.0,
                },
                "tasks": {},
            }
            return
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            self._config = yaml.safe_load(f)

    def get_task_config(self, task_name: str) -> dict[str, Any]:
        tasks = self._config.get("tasks", {})
        default = self._config.get("default", {})
        task_conf = tasks.get(task_name, {})
        return {**default, **task_conf}


model_router = ModelConfig()
