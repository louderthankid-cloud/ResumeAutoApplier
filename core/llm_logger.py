import logging
import time
import json
from pathlib import Path

# Настройка отдельного логгера для LLM
log_dir = Path("logs")
log_dir.mkdir(exist_ok=True)

llm_logger = logging.getLogger("llm_tracer")
llm_logger.setLevel(logging.INFO)

file_handler = logging.FileHandler(log_dir / "llm_history.log", encoding="utf-8")
file_handler.setFormatter(
    logging.Formatter(
        "\n%(asctime)s [DURATION: %(duration).2fs]\n=== PROMPT ===\n%(prompt)s\n=== RESPONSE ===\n%(response)s\n"
        + "=" * 50
    )
)
llm_logger.addHandler(file_handler)

# Логгер для консоли
console_logger = logging.getLogger("uvicorn")


def log_llm_interaction(
    system_prompt: str, user_prompt: str, response: str, start_time: float
):
    duration = time.time() - start_time

    # Объединяем системный и юзер промпт для лога
    full_prompt = f"[SYSTEM]:\n{system_prompt}\n\n[USER]:\n{user_prompt}"

    # Пишем в файл подробно
    llm_logger.info(
        "LLM Call",
        extra={"duration": duration, "prompt": full_prompt, "response": response},
    )

    # Пишем в консоль кратко
    console_logger.info(f"LLM Request finished in {duration:.2f}s")
