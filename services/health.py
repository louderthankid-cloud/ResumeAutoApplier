import os

import aiohttp
from sqlalchemy import text

from db.connection import AsyncSessionLocal
from core.config import settings
from services import hh_auth

HH_API_BASE = "https://api.hh.ru"


async def _check_db() -> tuple[bool, str]:
    try:
        async with AsyncSessionLocal() as s:
            await s.execute(text("SELECT 1"))
        return True, "подключение есть"
    except Exception as e:
        return False, str(e).splitlines()[0][:90]


async def _hh_ping(token: str) -> int:
    async with aiohttp.ClientSession() as s:
        async with s.get(
            f"{HH_API_BASE}/vacancies",
            params={"per_page": 1, "text": "test"},
            headers={
                "Authorization": f"Bearer {token}",
                "User-Agent": "ResumeAutoApplier/1.0",
            },
        ) as r:
            return r.status


async def _check_hh() -> tuple[bool, str]:
    try:
        token = await hh_auth.get_access_token()
        if not token:
            return False, "нет токена и нет HH_CLIENT_ID/SECRET"
        status = await _hh_ping(token)
        if status == 200:
            return True, "токен активен"
        if status in (401, 403):
            # реактивно обновляем (только если реально протух) и пробуем ещё раз
            token = await hh_auth.get_access_token(stale_token=token)
            if not token:
                return False, "токен протух, обновить нечем (нет client_id/secret)"
            status = await _hh_ping(token)
            return (status == 200), (
                "токен обновлён" if status == 200 else f"HTTP {status}"
            )
        return False, f"HTTP {status}"
    except Exception as e:
        return False, str(e).splitlines()[0][:90]


def _check_llm() -> tuple[bool, str]:
    present = [
        name
        for name, val in (
            ("OpenAI", settings.OPENAI_API_KEY),
            ("Gemini", settings.GEMINI_API_KEY),
            ("OpenRouter", settings.OPENROUTER_API_KEY),
            ("GigaChat", settings.GIGACHAT_CREDENTIALS),
        )
        if val
    ]
    return (bool(present), ", ".join(present) if present else "ни один ключ не задан")


def _check_smtp() -> tuple[bool, str]:
    ok = bool(os.getenv("EMAIL_ADDRESS") and os.getenv("EMAIL_APP_PASSWORD"))
    return ok, "задано" if ok else "EMAIL_ADDRESS/EMAIL_APP_PASSWORD не заданы"


async def check_config() -> list[tuple[str, bool, str]]:
    """список (пункт, ok, деталь). DB и HH — живые проверки, остальное — наличие"""
    db = await _check_db()
    hh = await _check_hh()
    return [
        ("База данных", *db),
        ("HH токен", *hh),
        ("LLM ключи", *_check_llm()),
        ("SMTP (почта)", *_check_smtp()),
    ]
