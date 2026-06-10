from sqlalchemy import select, update
import re

from db.connection import AsyncSessionLocal
from db.models import Candidate


class DBService:

    @staticmethod
    def _clean_company_name(name: str) -> str:
        """
        Убирает юр. статусы (ООО, АО, ПАО) и кавычки для точного сравнения.
        'ООО «Яндекс»' -> 'яндекс'
        """
        # Убираем аббревиатуры
        clean = re.sub(
            r"\b(ООО|АО|ЗАО|ПАО|ИП|НКО|ОАО|LLC|Ltd)\b", "", name, flags=re.IGNORECASE
        )
        # Убираем кавычки (разных типов) и лишние пробелы
        clean = (
            clean.replace('"', "").replace("«", "").replace("»", "").replace("'", "")
        )
        return clean.strip().lower()

    @staticmethod
    async def create_candidate(tg_id: str, data: dict) -> Candidate:
        """
        Создает новую карточку кандидата и привязывает её к HR.
        """
        async with AsyncSessionLocal() as session:
            candidate = Candidate(tg_id=tg_id, **data)
            session.add(candidate)
            await session.commit()
            await session.refresh(candidate)
            return candidate

    @staticmethod
    async def update_candidate(candidate_id: str, data: dict) -> Candidate | None:
        """
        Обновляет данные конкретного кандидата (например, если HR решил поменять резюме).
        """
        async with AsyncSessionLocal() as session:
            candidate = await session.get(Candidate, candidate_id)
            if not candidate:
                return None

            for key, value in data.items():
                setattr(candidate, key, value)

            await session.commit()
            await session.refresh(candidate)
            return candidate

    @staticmethod
    async def get_hr_candidates(tg_id: str) -> list[Candidate]:
        """
        Возвращает список всех кандидатов, которых ведет данный HR.
        Нужно для вывода кнопок в Telegram меню.
        """
        async with AsyncSessionLocal() as session:
            stmt = select(Candidate).where(Candidate.tg_id == tg_id)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    @staticmethod
    async def get_candidate_by_id(candidate_id: str) -> Candidate | None:
        """Получает карточку конкретного кандидата."""
        async with AsyncSessionLocal() as session:
            return await session.get(Candidate, candidate_id)

    @staticmethod
    async def add_applied_company(candidate_id: str, company_name: str):
        """
        Добавляет компанию в список отправленных ДЛЯ КОНКРЕТНОГО КАНДИДАТА.
        """
        company_name_clean = DBService._clean_company_name(company_name)

        async with AsyncSessionLocal() as session:
            candidate = await session.get(Candidate, candidate_id)
            if not candidate:
                return

            current_companies = candidate.applied_companies or []

            if company_name_clean not in current_companies:
                current_companies.append(company_name_clean)

                stmt = (
                    update(Candidate)
                    .where(Candidate.id == candidate_id)
                    .values(applied_companies=current_companies)
                )
                await session.execute(stmt)
                await session.commit()

    @staticmethod
    def filter_new_vacancies(candidate: Candidate, raw_vacancies: list) -> list:
        """
        Фильтрует список собранных вакансий, выбрасывая те компании,
        куда ЭТОТ кандидат уже был отправлен.
        """
        applied_set = set(candidate.applied_companies or [])

        filtered = []
        for vac in raw_vacancies:
            comp_name_clean = DBService._clean_company_name(vac.company_name)

            if comp_name_clean not in applied_set:
                filtered.append(vac)
            else:
                print(
                    f"[DB Filter] Пропускаю '{vac.company_name}' для кандидата {candidate.name} — уже отправлен."
                )

        return filtered
