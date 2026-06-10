from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine, async_sessionmaker
from core.config import settings


def get_async_engine() -> AsyncEngine:
    return create_async_engine(
        settings.DATABASE_URL,
        pool_pre_ping=True,
        # echo=True # Включи, если захочешь видеть SQL-запросы в консоли
    )


# Создаем фабрику сессий, чтобы удобно обращаться к БД из сервисов
engine = get_async_engine()
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)
