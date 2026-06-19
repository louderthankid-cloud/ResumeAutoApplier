import asyncio

from sqlalchemy import text

from db.connection import engine

from db.models import Base


async def init(retries: int = 10, delay: float = 2.0) -> None:
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            async with engine.begin() as conn:
                await conn.execute(text("SELECT 1"))
                await conn.run_sync(Base.metadata.create_all)
            print("[init_db] схема готова")
            await engine.dispose()
            return
        except Exception as e:
            last_err = e
            head = str(e).splitlines()[0][:120]
            print(f"[init_db] БД не готова (попытка {attempt}/{retries}): {head}")
            await asyncio.sleep(delay)

    await engine.dispose()
    raise SystemExit(f"[init_db] не удалось подключиться к БД: {last_err}")


if __name__ == "__main__":
    asyncio.run(init())
