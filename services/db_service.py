from sqlalchemy import select, func, delete
import re

from db.connection import AsyncSessionLocal
from db.models import Candidate, Application
from schemas.application import (
    ApplicationStatus,
    RETRYABLE_STATUSES,
    MAX_ATTEMPTS,
)
from services.resume_parser import extract_resume_text, extract_contacts
from services.candidate_validator import validate_candidate, CandidateCheck


class DBService:

    @staticmethod
    def _clean_company_name(name: str) -> str:
        """
        пример:'ООО «Яндекс»' -> 'яндекс'
        """
        clean = re.sub(
            r"\b(ООО|АО|ЗАО|ПАО|ИП|НКО|ОАО|LLC|Ltd)\b", "", name, flags=re.IGNORECASE
        )
        clean = (
            clean.replace('"', "").replace("«", "").replace("»", "").replace("'", "")
        )
        return clean.strip().lower()

    @staticmethod
    async def create_candidate(tg_id: str, data: dict) -> Candidate:
        """
        создает новую карточку кандидата и привязывает её к HR
        ожидает в data как минимум target_job и resume_text
        """
        if not (data.get("resume_text") or "").strip():
            raise ValueError("create_candidate: обязателен непустой resume_text")

        async with AsyncSessionLocal() as session:
            candidate = Candidate(tg_id=tg_id, **data)
            session.add(candidate)
            await session.commit()
            await session.refresh(candidate)
            return candidate

    @staticmethod
    async def create_candidate_from_file(
        tg_id: str,
        file_bytes: bytes,
        filename: str,
        target_job: str,
        name: str | None = None,
        mime: str | None = None,
    ) -> tuple[Candidate, CandidateCheck]:
        """
        полный путь создания кандидата из присланного файла резюме — это зовёт бот.
        name задаёт HR (для различения кандидатов); email/phone тянем regex из текста.
        """
        resume_text = extract_resume_text(file_bytes, filename)  # мб ResumeParseError
        contacts = extract_contacts(resume_text)

        data = {
            "target_job": target_job,
            "resume_text": resume_text,
            "resume_blob": file_bytes,
            "resume_filename": filename,
            "resume_mime": mime,
            "name": name,
            "email": contacts["email"],
            "phone": contacts["phone"],
        }

        candidate = await DBService.create_candidate(tg_id, data)
        check = validate_candidate(candidate)
        return candidate, check

    @staticmethod
    async def update_candidate(candidate_id: str, data: dict) -> Candidate | None:
        """
        обновляет данные конкретного кандидата
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
        возвращает список всех кандидатов, которых ведет данный hr
        """
        async with AsyncSessionLocal() as session:
            stmt = select(Candidate).where(Candidate.tg_id == tg_id)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    @staticmethod
    async def get_candidate_by_id(candidate_id: str) -> Candidate | None:
        """получает карточку конкретного кандидата"""
        async with AsyncSessionLocal() as session:
            return await session.get(Candidate, candidate_id)

    @staticmethod
    async def delete_candidate(candidate_id: str) -> bool:
        """удаляет кандидата и все его заявки. True — если кандидат был и удалён"""
        async with AsyncSessionLocal() as session:
            candidate = await session.get(Candidate, candidate_id)
            if not candidate:
                return False
            await session.execute(
                delete(Application).where(Application.candidate_id == candidate_id)
            )
            await session.delete(candidate)
            await session.commit()
            return True

    @staticmethod
    async def record_application(
        candidate_id: str,
        company_name: str,
        status: ApplicationStatus,
        error_detail: str | None = None,
        site_url: str | None = None,
        source_url: str | None = None,
        vacancy_url: str | None = None,
        target_url: str | None = None,
        hr_email: str | None = None,
        channel: str | None = None,
        form_status: str | None = None,
        email_status: str | None = None,
        form_scope: str | None = None,
        reason: str | None = None,
    ) -> Application:

        clean = DBService._clean_company_name(company_name)

        async with AsyncSessionLocal() as session:
            stmt = select(Application).where(
                Application.candidate_id == candidate_id,
                Application.company_name_clean == clean,
            )
            app = (await session.execute(stmt)).scalar_one_or_none()

            if app:
                app.attempts += 1
                app.status = status.value
                app.error_detail = error_detail
                app.form_status = form_status
                app.email_status = email_status
                app.form_scope = form_scope
                app.reason = reason
                if site_url:
                    app.site_url = site_url
                if source_url:
                    app.source_url = source_url
                if vacancy_url:
                    app.vacancy_url = vacancy_url
                if target_url:
                    app.target_url = target_url
                if hr_email:
                    app.hr_email = hr_email
                if channel:
                    app.channel = channel
            else:
                app = Application(
                    candidate_id=candidate_id,
                    company_name=company_name,
                    company_name_clean=clean,
                    status=status.value,
                    error_detail=error_detail,
                    site_url=site_url,
                    source_url=source_url,
                    vacancy_url=vacancy_url,
                    target_url=target_url,
                    hr_email=hr_email,
                    channel=channel,
                    form_status=form_status,
                    email_status=email_status,
                    form_scope=form_scope,
                    reason=reason,
                )
                session.add(app)

            await session.commit()
            await session.refresh(app)
            return app

    @staticmethod
    async def get_blocked_companies(candidate_id: str) -> set[str]:

        retryable_values = {s.value for s in RETRYABLE_STATUSES}

        async with AsyncSessionLocal() as session:
            stmt = select(
                Application.company_name_clean,
                Application.status,
                Application.attempts,
            ).where(Application.candidate_id == candidate_id)
            rows = (await session.execute(stmt)).all()

        blocked = set()
        for clean_name, status, attempts in rows:
            if status in retryable_values and attempts < MAX_ATTEMPTS:
                continue  # можно повторить
            blocked.add(clean_name)
        return blocked

    @staticmethod
    async def filter_new_vacancies(candidate: Candidate, raw_vacancies: list) -> list:
        """
        фильтра вакансий
        """
        blocked = await DBService.get_blocked_companies(candidate.id)

        filtered = []
        for vac in raw_vacancies:
            comp_name_clean = DBService._clean_company_name(vac.company_name)

            if comp_name_clean not in blocked:
                filtered.append(vac)
            else:
                print(
                    f"[DB Filter] Пропускаю '{vac.company_name}' для кандидата {candidate.id} — уже обработан."
                )

        return filtered

    @staticmethod
    async def get_stats(candidate_id: str) -> dict[str, int]:
        """
        сводка по кандидату: {'success': xx, 'captcha': xx, 'site_down': xx, ...}
        """
        async with AsyncSessionLocal() as session:
            stmt = (
                select(Application.status, func.count())
                .where(Application.candidate_id == candidate_id)
                .group_by(Application.status)
            )
            rows = (await session.execute(stmt)).all()
        return {status: count for status, count in rows}

    @staticmethod
    async def get_outcome_stats(candidate_id: str) -> dict:
        """сводка по итогу для отображения"""
        async with AsyncSessionLocal() as session:
            rows = (
                await session.execute(
                    select(Application.channel, Application.status, func.count())
                    .where(Application.candidate_id == candidate_id)
                    .group_by(Application.channel, Application.status)
                )
            ).all()

        total = 0
        responded = 0
        channels: dict[str, int] = {"email": 0, "form": 0, "both": 0}
        failed: dict[str, int] = {}
        for channel, status, cnt in rows:
            total += cnt
            if channel:  # дозвонились хотя бы одним каналом
                responded += cnt
                channels[channel] = channels.get(channel, 0) + cnt
            else:
                failed[status] = failed.get(status, 0) + cnt

        return {
            "total": total,
            "responded": responded,
            "channels": channels,
            "failed": failed,
        }

    @staticmethod
    async def get_applications(
        candidate_id: str,
        status: ApplicationStatus | None = None,
        responded: bool | None = None,
    ) -> list[Application]:
        """список заявок кандидата"""
        async with AsyncSessionLocal() as session:
            stmt = select(Application).where(Application.candidate_id == candidate_id)
            if status:
                stmt = stmt.where(Application.status == status.value)
            if responded is True:
                stmt = stmt.where(Application.channel.isnot(None))
            elif responded is False:
                stmt = stmt.where(Application.channel.is_(None))
            stmt = stmt.order_by(Application.updated_at.desc())
            return list((await session.execute(stmt)).scalars().all())
