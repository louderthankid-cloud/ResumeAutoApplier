import asyncio
import json
import os
import time

import aiohttp

from core.config import settings

HH_TOKEN_URL = "https://hh.ru/oauth/token"
TOKEN_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".hh_token.json"
)
_SKEW = 60

_lock = asyncio.Lock()
_cache: dict | None = None  # токен, который МЫ получили: {"access_token", "expires_at"}
_loaded = False


def _ensure_loaded() -> None:
    global _cache, _loaded
    if _loaded:
        return
    _loaded = True
    try:
        with open(TOKEN_FILE, encoding="utf-8") as f:
            _cache = json.load(f)
    except Exception:
        _cache = None


def _fresh(data) -> bool:
    return bool(
        data
        and data.get("access_token")
        and data.get("expires_at", 0) - _SKEW > time.time()
    )


def _save(data: dict) -> None:
    try:
        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception as e:
        print(f"[hh_auth] не смог сохранить токен: {e}")


async def _request_token() -> dict:
    if not (settings.HH_CLIENT_ID and settings.HH_CLIENT_SECRET):
        raise RuntimeError(
            "HH_CLIENT_ID/HH_CLIENT_SECRET не заданы — токен не обновить"
        )
    data = {
        "grant_type": "client_credentials",
        "client_id": settings.HH_CLIENT_ID,
        "client_secret": settings.HH_CLIENT_SECRET,
    }
    async with aiohttp.ClientSession() as s:
        async with s.post(
            HH_TOKEN_URL, data=data, headers={"User-Agent": "ResumeAutoApplier/1.0"}
        ) as r:
            body = await r.json()
            if r.status != 200:
                raise RuntimeError(f"hh не выдал токен ({r.status}): {body}")
    return {
        "access_token": body["access_token"],
        "expires_at": time.time() + int(body.get("expires_in", 0)),
    }


async def get_access_token(stale_token: str | None = None) -> str | None:
    """отдаёт рабочий токен"""
    global _cache
    _ensure_loaded()

    if stale_token is None:
        if _fresh(_cache):
            return _cache["access_token"]
        if _cache is None and settings.HH_ACCESS_TOKEN:
            return settings.HH_ACCESS_TOKEN  # бутстрап из .env

    async with _lock:
        if _fresh(_cache) and (
            stale_token is None or _cache["access_token"] != stale_token
        ):
            return _cache["access_token"]
        if not (settings.HH_CLIENT_ID and settings.HH_CLIENT_SECRET):
            return settings.HH_ACCESS_TOKEN
        token = await _request_token()
        _cache = token
        _save(token)
        print(
            "[hh_auth] получен новый токен, годен до "
            + time.strftime("%Y-%m-%d %H:%M", time.localtime(token["expires_at"]))
        )
        return token["access_token"]
