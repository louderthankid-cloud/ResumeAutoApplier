from abc import ABC, abstractmethod
from schemas.vacancy import Vacancy


class BaseSourceAdapter(ABC):
    @abstractmethod
    async def fetch_vacancies(
        self, search_query: str, limit: int = 20
    ) -> list[Vacancy]:
        """Все адаптеры обязаны возвращать список объектов Vacancy"""
        pass

    @abstractmethod
    async def fetch_by_url(self, url: str) -> Vacancy:
        """Парсинг конкретной вакансии по прямой ссылке"""
        pass
