from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine, async_sessionmaker
from core.config import settings


def get_async_engine() -> AsyncEngine:
    return create_async_engine(
        settings.DATABASE_URL,
        pool_pre_ping=True,  # проверять живость коннекта перед выдачей из пула
        pool_recycle=1800,  # пересоздавать коннект старше 30 мин (idle-таймаут БД)
        # echo=True
    )


# Создаем фабрику сессий, чтобы удобно обращаться к бд из сервисов
engine = get_async_engine()
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)
