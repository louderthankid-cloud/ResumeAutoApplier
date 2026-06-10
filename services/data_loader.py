from schemas.vacancy import Vacancy
from sources.hh_adapter import HHAdapter
from services.company_search import find_company_site


def _get_adapter(source: str):
    """Возвращает нужный адаптер по имени источника"""
    adapters = {
        "hh": HHAdapter,
        # "telegram": TelegramAdapter,  # когда-нибудь потом
    }
    cls = adapters.get(source)
    if not cls:
        raise ValueError(
            f"Неизвестный источник: '{source}'. Доступны: {list(adapters.keys())}"
        )
    return cls()


def _detect_source(url: str) -> str:
    """Определяем источник по URL"""
    if "hh.ru" in url:
        return "hh"
    raise ValueError(f"Не удалось определить источник для URL: {url}")


async def load_vacancies(
    query: str, source: str = "hh", limit: int = 20
) -> list[Vacancy]:
    """
    Загружает список вакансий из указанного источника.
    Для вакансий без company_site автоматически ищет через DDG.
    """
    adapter = _get_adapter(source)
    vacancies = await adapter.fetch_vacancies(query, limit)

    # Фоллбэк: если адаптер не нашёл сайт компании — ищем через DDG
    for vacancy in vacancies:
        if not vacancy.company_site:
            print(
                f"[DataLoader] Нет site_url для '{vacancy.company_name}', ищу через DDG..."
            )
            vacancy.company_site = find_company_site(vacancy.company_name)

    return vacancies


async def load_by_url(url: str) -> Vacancy:
    """
    Загружает конкретную вакансию по ссылке.
    Источник определяется автоматически по домену.
    """
    source = _detect_source(url)
    adapter = _get_adapter(source)
    vacancy = await adapter.fetch_by_url(url)

    # Фоллбэк на DDG если нет сайта
    if not vacancy.company_site:
        print(
            f"[DataLoader] Нет site_url для '{vacancy.company_name}', ищу через DDG..."
        )
        vacancy.company_site = find_company_site(vacancy.company_name)

    return vacancy
