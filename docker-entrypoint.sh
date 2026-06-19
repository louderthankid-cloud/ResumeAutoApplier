#!/usr/bin/env bash
set -e

echo "[entrypoint] инициализация схемы БД..."
python -m db.init_db

echo "[entrypoint] запуск бота..."
exec python -m bot.main
