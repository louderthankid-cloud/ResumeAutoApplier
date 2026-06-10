import re
from playwright.async_api import async_playwright
from sources.base import BaseSourceAdapter
from schemas.vacancy import Vacancy


class HHAdapter(BaseSourceAdapter):

    async def fetch_vacancies(
        self, search_query: str, limit: int = 10
    ) -> list[Vacancy]:
        # Пока оставляем мок для поиска из прошлого примера (или заглушку)
        pass

    async def fetch_by_url(self, url: str) -> Vacancy:
        print(f"[HHAdapter] Начинаю парсинг вакансии: {url}")

        # Вытаскиваем ID вакансии из ссылки (например, 132289325)
        match = re.search(r"/vacancy/(\d+)", url)
        if not match:
            raise ValueError("Не удалось найти ID вакансии в ссылке")
        original_id = match.group(1)
        internal_id = f"hh_{original_id}"

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)  # Теперь можно скрыть окно
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            page = await context.new_page()

            await page.goto(url, wait_until="domcontentloaded")
            await page.wait_for_timeout(2000)  # Даем подгрузиться тексту

            # Забираем только те поля, которые мы увидели в разведчике
            data = await page.evaluate("""
                () => {
                    const getText = (selector) => {
                        const el = document.querySelector(selector);
                        return el ? el.innerText.trim() : "";
                    };
                    return {
                        title: getText('[data-qa="vacancy-title"]'),
                        company_name: getText('[data-qa="vacancy-company-name"]'),
                        description: getText('[data-qa="vacancy-description"]')
                    };
                }
            """)

            await browser.close()

        return Vacancy(
            id=internal_id,
            title=data.get("title", "Без названия"),
            company_name=data.get("company_name", "Неизвестная компания"),
            vacancy_url=url,
            description=data.get("description", ""),
            company_site=None,
            hr_email=None,
        )
