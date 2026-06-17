from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    OPENAI_API_KEY: str | None = None
    GEMINI_API_KEY: str | None = None
    OPENROUTER_API_KEY: str | None = None
    GIGACHAT_CREDENTIALS: str | None = None
    OLLAMA_BASE_URL: str = "http://localhost:11434"

    DATABASE_URL: str

    # HH_ACCESS_TOKEN — стартовый токен из .env, потом берётся из временного
    HH_CLIENT_ID: str | None = None
    HH_CLIENT_SECRET: str | None = None
    HH_ACCESS_TOKEN: str | None = None

    LLM_TIMEOUT: int = 600

    DRY_RUN: bool = True  # тест ран
    LLM_CALL_LOG: bool = True

    PIPELINE_POOL_SIZE: int = 6  # общий пул параллельных компаний на все прогоны
    TELEGRAM_BOT_TOKEN: str | None = None

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()
