import re
from urllib.parse import urlparse
from ddgs import DDGS


def _clean_name(company_name: str) -> str:
    return re.sub(
        r"\b(ООО|АО|ЗАО|ПАО|ИП|НКО|ОАО)\b", "", company_name, flags=re.IGNORECASE
    ).strip()


def find_company_site(company_name: str) -> str | None:
    clean_name = _clean_name(company_name)

    junk_domains = [
        "hh.ru",
        "headhunter.ru",
        "rabota.ru",
        "superjob.ru",
        "zarplata.ru",
        "linkedin.com",
        "habr.com",
        "work.ru",
        "dreamjob.ru",
        "wipo.int",
        "wikipedia.org",
        "2gis.ru",
        "yandex.ru",
        "google.com",
        "zoon.ru",
        "kontakt.ru",
    ]

    queries = [
        f"{clean_name} официальный сайт карьера",
        f"{clean_name} official site",
        clean_name,
    ]

    for query in queries:
        print(f"[CompanySearch] Запрос: '{query}'...")
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=5))
            for result in results:
                url = result.get("href", "")
                if url and not any(junk in url for junk in junk_domains):
                    parsed = urlparse(url)
                    root = f"{parsed.scheme}://{parsed.netloc}"
                    print(f"[CompanySearch] Нашёл: {root}")
                    return root
        except Exception as e:
            print(f"[CompanySearch] Ошибка: {e}")

    print(f"[CompanySearch] Не удалось найти '{company_name}'")
    return None
