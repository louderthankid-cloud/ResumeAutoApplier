import re
import asyncio
import os
from bs4 import BeautifulSoup
import aiohttp

from sources.base import BaseSourceAdapter
from schemas.vacancy import Vacancy
from services.company_search import find_company_site

HH_API_BASE = "https://api.hh.ru"

from dotenv import load_dotenv

load_dotenv()


class HHAdapter(BaseSourceAdapter):

    def __init__(self):
        self.token = os.getenv("HH_ACCESS_TOKEN")
        if not self.token:
            raise ValueError("HH_ACCESS_TOKEN не задан в .env")

    @property
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.token}",
            "User-Agent": "ResumeAutoApplier/1.0",
        }

    async def _get_company_site(
        self, session: aiohttp.ClientSession, employer_id: str
    ) -> str | None:
        """Получаем сайт компании через /employers/{id}, фоллбэк на DDG"""
        try:
            url = f"{HH_API_BASE}/employers/{employer_id}"
            async with session.get(url, headers=self._headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    site = data.get("site_url")
                    if site:
                        return site.rstrip("/")
        except Exception as e:
            print(f"[HHAdapter] Ошибка получения работодателя {employer_id}: {e}")

        return None  # фоллбэк на DDG вызывается снаружи в data_loader

    def _clean_description(self, html: str) -> str:
        """Убираем HTML теги из описания вакансии"""
        if not html:
            return ""
        return BeautifulSoup(html, "html.parser").get_text(separator="\n").strip()

    def _build_vacancy(
        self, data: dict, description_html: str, company_site: str | None
    ) -> Vacancy:
        """Собираем объект Vacancy из данных API"""
        employer = data.get("employer", {})
        vacancy_id = str(data["id"])

        return Vacancy(
            id=f"hh_{vacancy_id}",
            title=data.get("name", "Без названия"),
            company_name=employer.get("name", "Неизвестная компания"),
            vacancy_url=data.get("alternate_url", ""),
            description=self._clean_description(description_html),
            company_site=company_site,
        )

    async def fetch_vacancies(self, query: str, limit: int = 20) -> list[Vacancy]:
        """Поиск вакансий через HH API"""
        print(f"[HHAdapter] Ищу вакансии: '{query}' (лимит: {limit})")

        async with aiohttp.ClientSession() as session:
            # 1. Получаем список вакансий
            params = {
                "text": query,
                "per_page": limit,
                "search_field": "name",
                "area": 113,  # Россия
                "only_with_salary": "false",
                "order_by": "publication_time",
            }
            async with session.get(
                f"{HH_API_BASE}/vacancies", headers=self._headers, params=params
            ) as resp:
                if resp.status != 200:
                    print(f"[HHAdapter] Ошибка поиска: {resp.status}")
                    return []
                result = await resp.json()

            items = result.get("items", [])
            print(
                f"[HHAdapter] Найдено: {result.get('found', 0)}, получено: {len(items)}"
            )

            # 2. Для каждой вакансии получаем описание и сайт компании
            vacancies = []
            for item in items:
                vacancy_id = item["id"]
                employer_id = item.get("employer", {}).get("id")

                # Пауза чтобы не словить rate limit
                await asyncio.sleep(0.3)

                try:
                    # Полное описание вакансии
                    async with session.get(
                        f"{HH_API_BASE}/vacancies/{vacancy_id}", headers=self._headers
                    ) as resp:
                        vacancy_data = await resp.json() if resp.status == 200 else item

                    # Сайт компании
                    company_site = None
                    if employer_id:
                        company_site = await self._get_company_site(
                            session, employer_id
                        )

                    description_html = vacancy_data.get("description", "")
                    vacancy = self._build_vacancy(
                        vacancy_data, description_html, company_site
                    )
                    vacancies.append(vacancy)
                    print(f"[HHAdapter] ✓ {vacancy.title} — {vacancy.company_name}")

                except Exception as e:
                    print(f"[HHAdapter] Ошибка обработки вакансии {vacancy_id}: {e}")

            return vacancies

    async def fetch_by_url(self, url: str) -> Vacancy:
        """Получить конкретную вакансию по HH ссылке"""
        print(f"[HHAdapter] Парсю вакансию: {url}")

        match = re.search(r"/vacancy/(\d+)", url)
        if not match:
            raise ValueError(f"Не удалось найти ID вакансии в ссылке: {url}")

        vacancy_id = match.group(1)

        async with aiohttp.ClientSession() as session:
            # Данные вакансии
            async with session.get(
                f"{HH_API_BASE}/vacancies/{vacancy_id}", headers=self._headers
            ) as resp:
                if resp.status != 200:
                    raise ValueError(
                        f"HH API вернул {resp.status} для вакансии {vacancy_id}"
                    )
                data = await resp.json()

            # Сайт компании
            employer_id = data.get("employer", {}).get("id")
            company_site = None
            if employer_id:
                company_site = await self._get_company_site(session, employer_id)

            description_html = data.get("description", "")
            return self._build_vacancy(data, description_html, company_site)
