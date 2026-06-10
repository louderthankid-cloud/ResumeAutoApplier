from langchain_openai import ChatOpenAI
from langchain_ollama import ChatOllama
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_gigachat.chat_models import GigaChat

from core.config import settings
from core.model_config import model_router


def _create_llm_instance(
    provider: str, model_name: str, temperature: float, json_mode: bool
):
    """Вспомогательная функция, которая создает один инстанс LLM"""
    kwargs = {
        "temperature": temperature,
        "model": model_name,
        "request_timeout": settings.LLM_TIMEOUT,
    }
    model_kwargs = {"response_format": {"type": "json_object"}} if json_mode else {}

    if provider == "openai":
        return ChatOpenAI(
            openai_api_key=settings.OPENAI_API_KEY, model_kwargs=model_kwargs, **kwargs
        )
    elif provider == "openrouter":
        return ChatOpenAI(
            base_url="https://openrouter.ai/api/v1",
            openai_api_key=settings.OPENROUTER_API_KEY,
            model_kwargs=model_kwargs,
            **kwargs,
        )
    elif provider == "ollama":
        return ChatOllama(
            base_url=settings.OLLAMA_BASE_URL,
            format="json" if json_mode else "",
            **kwargs,
        )
    elif provider == "google":
        return ChatGoogleGenerativeAI(google_api_key=settings.GEMINI_API_KEY, **kwargs)
    elif provider == "gigachat":
        kwargs.pop("request_timeout", None)
        return GigaChat(
            credentials=settings.GIGACHAT_CREDENTIALS, verify_ssl_certs=False, **kwargs
        )
    else:
        raise ValueError(f"Unknown provider: {provider}")


def get_llm(task_name: str = "default", json_mode: bool = False):
    """Создает основную модель и цепляет к ней фоллбэки из конфига."""
    config = model_router.get_task_config(task_name)

    provider = config.get("provider", "ollama").lower()
    model_name = config.get("model", "qwen2.5-coder:7b")
    temperature = config.get("temperature", 0.0)

    primary_llm = _create_llm_instance(provider, model_name, temperature, json_mode)

    fallbacks_config = config.get("fallbacks", [])

    if fallbacks_config:
        fallback_llms = []
        for fb in fallbacks_config:
            fb_provider = fb.get("provider", provider).lower()
            fb_model = fb.get("model", model_name)
            fb_temp = fb.get("temperature", temperature)

            fb_instance = _create_llm_instance(
                fb_provider, fb_model, fb_temp, json_mode
            )
            fallback_llms.append(fb_instance)

        return primary_llm.with_fallbacks(fallback_llms)

    return primary_llm
