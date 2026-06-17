import asyncio
import os
import re
import shutil
import tempfile
import time
from contextlib import contextmanager
from typing import Awaitable, Callable

import aiohttp
from playwright.async_api import async_playwright, Browser
from pydantic import BaseModel, Field

from services.catalog_detector import SmartCrawler, choose_best_emails
from services.catalog_processor import CatalogProcessor
from services.hh_query_service import generate_hh_queries
from services import hh_auth
from services.form_filler_service import FormFillerOrchestrator
from services.email_service import send_email
from services.cover_letter_service import generate_cover_letter
from services.db_service import DBService
from core.config import settings
from core.errors import PipelineError
from schemas.application import (
    ApplicationStatus,
    EmailStatus,
    FormFillStatus,
    FormScope,
)

HH_API_BASE = "https://api.hh.ru"

OK_STATUSES = (ApplicationStatus.SUCCESS, ApplicationStatus.DRY_RUN_OK)

OnProgress = Callable[[dict], Awaitable[None]]


class RunSummary(BaseModel):
    """сводка прогона одного кандидата"""

    candidate_id: str
    total: int = 0
    ok: int = 0
    failed: int = 0
    by_status: dict[str, int] = Field(default_factory=dict)
    reports: list[dict] = Field(default_factory=list)


class CompanyLead(BaseModel):
    """компания-кандидат на обработку"""

    company_name: str
    site_url: str
    hh_url: str = ""
    vacancy_title: str | None = None
    vacancy_snippet: str | None = None


@contextmanager
def materialize_resume(candidate):
    """временный файл"""
    blob = getattr(candidate, "resume_blob", None)
    if not blob:
        yield None
        return

    tmpdir = tempfile.mkdtemp(prefix="resume_")
    filename = candidate.resume_filename or f"{candidate.id}.pdf"
    path = os.path.join(tmpdir, os.path.basename(filename))
    try:
        with open(path, "wb") as f:
            f.write(blob)
        yield path
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _hh_snippet(item: dict) -> str | None:
    """короткое описание вакансии из HH (требования + обязанности), без тегов"""
    snip = item.get("snippet") or {}
    text = " ".join(
        p for p in (snip.get("requirement"), snip.get("responsibility")) if p
    )
    text = re.sub(r"</?highlighttext>", "", text)
    return text.strip() or None


async def _hh_get(session, url: str, token_box: dict, params: dict | None = None):
    """GET к hh с Bearer, при истечении токена - запись нового"""
    headers = {
        "Authorization": f"Bearer {token_box['t']}",
        "User-Agent": "ResumeAutoApplier/1.0",
    }
    async with session.get(url, headers=headers, params=params) as r:
        if r.status not in (401, 403):
            return r.status, (await r.json() if r.status == 200 else None)
    # если токен протух — обновляем
    token_box["t"] = await hh_auth.get_access_token(stale_token=token_box["t"])
    headers["Authorization"] = f"Bearer {token_box['t']}"
    async with session.get(url, headers=headers, params=params) as r2:
        return r2.status, (await r2.json() if r2.status == 200 else None)


async def load_from_hh(
    queries: list[str] | str,
    limit: int,
    blocked: set[str] | None = None,
    log: Callable[..., None] = print,
) -> list["CompanyLead"]:
    """Собирает с hh компаниии"""
    if isinstance(queries, str):
        queries = [queries]
    if limit <= 0 or not queries:
        return []
    blocked = blocked or set()

    token = await hh_auth.get_access_token()
    if not token:
        log("   [HH] нет токена (HH_ACCESS_TOKEN / HH_CLIENT_ID+SECRET) — HH пропущен")
        return []
    token_box = {"t": token}

    result: list[CompanyLead] = []
    seen_employers: set = set()
    PER_PAGE = 100
    MAX_PAGE = 2000 // PER_PAGE  # лимит hh: page*per_page <= 2000

    async with aiohttp.ClientSession() as session:
        for query in queries:
            if len(result) >= limit:
                break
            log(f"   [HH] запрос: '{query}'  (свежих {len(result)}/{limit})")
            page = 0
            while len(result) < limit and page < MAX_PAGE:
                params = {
                    "text": query,
                    "search_field": "name",
                    "per_page": PER_PAGE,
                    "page": page,
                    "area": 113,
                    "order_by": "publication_time",
                }
                status, data = await _hh_get(
                    session, f"{HH_API_BASE}/vacancies", token_box, params
                )
                if status != 200 or not data:
                    break
                items = data.get("items", [])
                if not items:
                    break  # запрос исчерпан — к следующему варианту

                for item in items:
                    if len(result) >= limit:
                        break
                    emp = item.get("employer") or {}
                    emp_id = emp.get("id")
                    if not emp_id or emp_id in seen_employers:
                        continue
                    seen_employers.add(emp_id)

                    # уже обработанных пропускаем по имени из вакансии — без GET деталей
                    emp_name = emp.get("name") or ""
                    if emp_name and DBService._clean_company_name(emp_name) in blocked:
                        continue

                    await asyncio.sleep(0.2)
                    estatus, edata = await _hh_get(
                        session, f"{HH_API_BASE}/employers/{emp_id}", token_box
                    )
                    if estatus != 200 or not edata:
                        continue
                    if edata.get("type") == "agency":
                        log(f"   [HH] скип (кадровое агентство): {edata.get('name')}")
                        continue
                    site = (edata.get("site_url") or "").rstrip("/")
                    if not site:
                        continue
                    # подстраховка: точное имя из деталей тоже сверяем с blocked
                    if (
                        DBService._clean_company_name(edata.get("name") or "")
                        in blocked
                    ):
                        continue

                    result.append(
                        CompanyLead(
                            company_name=edata.get("name", "Unknown"),
                            site_url=site,
                            hh_url=item.get("alternate_url", ""),
                            vacancy_title=item.get("name"),
                            vacancy_snippet=_hh_snippet(item),
                        )
                    )
                    log(f"   [HH] +{edata.get('name')} → {site}")

                page += 1

    log(f"   [HH] набрано {len(result)}/{limit} свежих")
    return result


def resolve_status(
    form_status,
    email_status: EmailStatus,
    dry_run: bool,
    d_type: str,
    catalog_fail: str | None = None,
):
    form_ok = form_status in (FormFillStatus.SUBMITTED, FormFillStatus.FILLED_DRY_RUN)
    email_ok = email_status in (EmailStatus.SENT, EmailStatus.DRY_RUN)

    if form_ok and email_ok:
        channel = "both"
    elif form_ok:
        channel = "form"
    elif email_ok:
        channel = "email"
    else:
        channel = None

    # успех
    real_success = (form_status == FormFillStatus.SUBMITTED) or (
        email_status == EmailStatus.SENT
    )
    if real_success and not dry_run:
        return ApplicationStatus.SUCCESS, channel
    if form_ok or email_ok:
        return ApplicationStatus.DRY_RUN_OK, channel

    # провал
    if form_status == FormFillStatus.BLOCKED_CAPTCHA:
        return ApplicationStatus.CAPTCHA, channel
    if form_status == FormFillStatus.REQUIRES_LOGIN:
        return ApplicationStatus.REQUIRES_LOGIN, channel
    if form_status == FormFillStatus.LOAD_FAILED:
        return ApplicationStatus.FORM_LOAD_FAILED, channel

    if (d_type == "CATALOG" or catalog_fail in ("no_match", "scrape_empty")) and (
        form_status in (FormFillStatus.FORM_NOT_FOUND, None)
    ):
        return ApplicationStatus.VACANCY_NOT_FOUND, channel

    if form_status in (
        FormFillStatus.FILL_FAILED,
        FormFillStatus.FIELD_IMPOSSIBLE,
        FormFillStatus.UNEXPECTED_ERROR,
    ):
        return ApplicationStatus.FORM_FILL_FAILED, channel

    if email_status == EmailStatus.FAILED:
        return ApplicationStatus.EMAIL_FAILED, channel

    return ApplicationStatus.NO_CONTACTS_FOUND, channel


async def _process_company(
    task_id: int,
    lead: CompanyLead,
    candidate,
    resume_path: str | None,
    browser: Browser,
    acquire_slot,
    log: Callable[..., None],
    dry_run: bool,
    on_start=None,
) -> dict:
    async with acquire_slot():
        t_start = time.monotonic()
        company_name = lead.company_name
        site_url = lead.site_url
        hh_url = lead.hh_url
        if on_start:
            await on_start(task_id, company_name)

        report = {
            "id": task_id,
            "company": company_name,
            "site": site_url,
            "hh_url": hh_url,
            "decision": "—",
            "target_url": "—",
            "email": "—",
            "form_url": "—",
            "form_scope": "—",
            "form_status": "—",
            "email_status": "—",
            "app_status": "—",
            "channel": "",
            "email_sent": False,
            "reason": "",
            "catalog_fail": None,
            "error": None,
            "elapsed": 0,
        }

        fill_report = None
        email_sent = False
        best_email = None
        target_url = site_url
        vacancy_url = None
        form_filled_url = None

        form_status_enum = None
        email_status = EmailStatus.NOT_FOUND
        form_scope_val = None
        catalog_reason = ""
        catalog_fail = None

        log(f"\n{'─'*60}")
        log(f"[{task_id}] СТАРТ: {company_name}  ({site_url})")
        if hh_url:
            log(f"[{task_id}] HH-вакансия: {hh_url}")

        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )
        page = await context.new_page()

        try:
            log(f"[{task_id}] Запускаю паука")
            crawler = SmartCrawler(site_url, verbose=False)
            decision = await crawler.run(browser, use_llm=True)

            best_emails = choose_best_emails(crawler.emails, min_score=50)
            best_email = best_emails[0] if best_emails else None

            d_type = decision.get("decision", "NO_HR_FORMS")
            target_url = decision.get("target_url") or site_url

            report["decision"] = d_type
            report["target_url"] = target_url
            report["email"] = best_email or "—"

            log(f"[{task_id}] Решение: {d_type} → {target_url}")
            if best_email:
                log(f"[{task_id}] Email: {best_email}")

            form_url = target_url

            if d_type == "CATALOG":
                log(f"[{task_id}] Каталог — ищу вакансию '{candidate.target_job}'...")
                await page.goto(
                    target_url, wait_until="domcontentloaded", timeout=15000
                )
                await page.wait_for_timeout(2000)

                processor = CatalogProcessor(verbose=False)
                cat_queries = await processor.generate_search_queries(
                    candidate.target_job,
                    vacancy_title=lead.vacancy_title,
                    vacancy_snippet=lead.vacancy_snippet,
                )
                cat = await processor.process_catalog(
                    page, target_url, candidate.target_job, queries=cat_queries
                )
                vacancy_url = cat.get("url")
                catalog_reason = cat.get("reason", "")
                catalog_fail = cat.get("fail_kind")

                if vacancy_url:
                    form_url = vacancy_url
                    form_scope_val = FormScope.VACANCY
                    log(f"[{task_id}] Найдена вакансия: {vacancy_url}")
                else:
                    form_scope_val = FormScope.GENERAL
                    log(
                        f"[{task_id}] Вакансия не выбрана ({catalog_fail}): {catalog_reason[:80]}"
                    )

            elif d_type == "NO_HR_FORMS":
                log(f"[{task_id}] Форм не найдено")

            if d_type != "NO_HR_FORMS":
                log(f"[{task_id}] Заполняю форму: {form_url}")
                form_filled_url = form_url
                report["form_url"] = form_url
                report["form_scope"] = form_scope_val.value if form_scope_val else "—"

                try:
                    await page.goto(
                        form_url, wait_until="domcontentloaded", timeout=15000
                    )
                    await page.wait_for_timeout(2000)
                    loaded = True
                except Exception as nav_e:
                    loaded = False
                    form_status_enum = FormFillStatus.LOAD_FAILED
                    report["form_status"] = FormFillStatus.LOAD_FAILED.value
                    report["error"] = (
                        f"страница формы не загрузилась: {str(nav_e).splitlines()[0]}"
                    )
                    log(
                        f"[{task_id}] Форма не загрузилась: {str(nav_e).splitlines()[0]}"
                    )

                if loaded:
                    filler = FormFillerOrchestrator(verbose=False)
                    fill_report = await filler.run_loop(
                        page=page,
                        candidate_resume=candidate.resume_text,
                        resume_path=resume_path,
                        max_steps=4,
                    )
                    form_status_enum = fill_report.status
                    report["form_status"] = fill_report.status.value
                    if fill_report.ok:
                        log(f"[{task_id}] Форма: {fill_report.status.value}")
                    else:
                        log(
                            f"[{task_id}] Форма не прошла "
                            f"[{fill_report.status.value}]: {fill_report.detail}"
                        )

            if best_email:
                log(f"[{task_id}] Отправляю письмо на {best_email}...")
                try:
                    letter = await generate_cover_letter(
                        resume_text=candidate.resume_text,
                        target_job=candidate.target_job,
                        vacancy_text="",
                        company_name=company_name,
                    )
                    email_sent = await asyncio.to_thread(
                        send_email,
                        to=best_email,
                        subject=f"Отклик на вакансию: {candidate.target_job}",
                        body=letter,
                        resume_path=resume_path,
                        dry_run=dry_run,
                    )
                    if email_sent:
                        email_status = (
                            EmailStatus.DRY_RUN if dry_run else EmailStatus.SENT
                        )
                    else:
                        email_status = EmailStatus.FAILED
                    log(
                        f"[{task_id}] Письмо {'отправлено' if email_sent else 'НЕ отправлено'}"
                    )
                except Exception as e:
                    email_status = EmailStatus.FAILED
                    log(f"[{task_id}] Ошибка письма: {e}")
            else:
                email_status = EmailStatus.NOT_FOUND
                log(f"[{task_id}] Подходящей почты не найдено — email-канал пропущен")

            report["email_status"] = email_status.value

            status, channel = resolve_status(
                form_status_enum, email_status, dry_run, d_type, catalog_fail
            )

            if fill_report and fill_report.detail:
                detail = fill_report.detail
                if fill_report.unfilled_fields:
                    detail += f" | незаполненные поля: {', '.join(fill_report.unfilled_fields)}"
            elif report.get("error"):
                detail = report["error"]
            elif email_status in (EmailStatus.SENT, EmailStatus.DRY_RUN):
                detail = (
                    "письмо отправлено себе (DRY_RUN)"
                    if dry_run
                    else "письмо отправлено"
                )
            elif catalog_reason:
                detail = catalog_reason
            else:
                detail = ""

            report["reason"] = catalog_reason or ""

        except PipelineError as pe:
            status, channel, detail = pe.status, None, pe.detail
            report["error"] = pe.detail
            log(f"[{task_id}] ИНФРА-ОШИБКА [{pe.status.value}]: {pe.detail}")

        except Exception as e:
            status, channel, detail = (
                ApplicationStatus.FORM_FILL_FAILED,
                None,
                f"неожиданная ошибка: {e}",
            )
            report["error"] = str(e)
            log(f"[{task_id}] ОШИБКА: {e}")

        finally:
            await page.close()
            await context.close()

        report["app_status"] = status.value
        report["channel"] = channel or ""
        report["email_sent"] = email_sent
        report["catalog_fail"] = catalog_fail
        if detail and not report["error"]:
            report["error"] = detail

        try:
            await DBService.record_application(
                candidate_id=candidate.id,
                company_name=company_name,
                status=status,
                error_detail=detail,
                site_url=site_url,
                source_url=hh_url or None,
                vacancy_url=vacancy_url,
                target_url=form_filled_url or target_url,
                hr_email=best_email,
                channel=channel,
                email_status=email_status.value if email_status else None,
                form_status=form_status_enum.value if form_status_enum else None,
                form_scope=form_scope_val.value if form_scope_val else None,
                reason=(catalog_reason or None),
            )
        except Exception as db_e:
            log(f"[{task_id}] ошибка записи в бд: {db_e}")

        report["elapsed"] = round(time.monotonic() - t_start, 1)

        icon = "ок" if status in OK_STATUSES else "не ок"
        if not best_email:
            email_note = "почты нет"
        elif email_sent:
            email_note = "отправлено"
        else:
            email_note = "НЕ отправлено"
        log(
            f"[{task_id}] {icon} готово за {report['elapsed']}с  "
            f"| статус: {status.value}  | канал: {channel or '—'}  | письмо: {email_note}"
        )

        return report


async def run_candidate_pipeline(
    candidate,
    *,
    hh_limit: int = 50,
    workers: int = 2,
    extra_sites: list[tuple[str, str]] | None = None,
    verbose: bool = True,
    on_progress: OnProgress | None = None,
    dry_run: bool | None = None,
    acquire_slot=None,
) -> RunSummary:
    """полный прогон пайплайна для одного кандидата"""
    log: Callable[..., None] = print if verbose else (lambda *a, **k: None)
    if dry_run is None:
        dry_run = settings.DRY_RUN
    if acquire_slot is None:
        _sem = asyncio.Semaphore(workers)

        def acquire_slot():
            return _sem

    blocked = await DBService.get_blocked_companies(candidate.id)

    leads: list[CompanyLead] = [
        CompanyLead(company_name=n, site_url=u) for n, u in (extra_sites or [])
    ]
    if hh_limit > 0:
        hh_queries = await generate_hh_queries(candidate.target_job, verbose=verbose)
        log(f"Ищу {hh_limit} свежих компаний на HH по {len(hh_queries)} вариантам...")
        leads.extend(await load_from_hh(hh_queries, hh_limit, blocked, log))

    fresh = []
    for lead in leads:
        if DBService._clean_company_name(lead.company_name) in blocked:
            log(f"[DB Filter] Пропускаю '{lead.company_name}' — уже обработан.")
        else:
            fresh.append(lead)
    leads = fresh
    log(f"Компаний к обработке: {len(leads)}\n")

    summary = RunSummary(candidate_id=candidate.id, total=len(leads))

    if on_progress:
        await on_progress({"event": "start", "total": summary.total})

    if not leads:
        log("Все компании уже обработаны. Конец.")
        if on_progress:
            await on_progress({"event": "done", "summary": summary.model_dump()})
        return summary

    # прогон под общим браузером + материализованным резюме
    progress = {"done": 0, "ok": 0, "failed": 0}
    prog_lock = asyncio.Lock()

    async def _emit_start(task_id: int, company: str) -> None:
        if on_progress:
            await on_progress(
                {
                    "event": "company_start",
                    "task_id": task_id,
                    "company": company,
                    "total": summary.total,
                }
            )

    with materialize_resume(candidate) as resume_path:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-features=IsolateOrigins,site-per-process",
                ],
            )

            async def worker(task_id, lead):
                report = await _process_company(
                    task_id,
                    lead,
                    candidate,
                    resume_path,
                    browser,
                    acquire_slot,
                    log,
                    dry_run,
                    on_start=_emit_start,
                )
                async with prog_lock:
                    progress["done"] += 1
                    if report["app_status"] in (
                        ApplicationStatus.SUCCESS.value,
                        ApplicationStatus.DRY_RUN_OK.value,
                    ):
                        progress["ok"] += 1
                    else:
                        progress["failed"] += 1
                    snapshot = dict(progress)
                if on_progress:
                    await on_progress(
                        {
                            "event": "company",
                            "task_id": task_id,
                            "report": report,
                            "total": summary.total,
                            **snapshot,
                        }
                    )
                return report

            tasks = [
                asyncio.create_task(worker(i + 1, lead)) for i, lead in enumerate(leads)
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            await browser.close()

    # сводка
    for r in results:
        if isinstance(r, Exception):
            summary.failed += 1
            summary.by_status["exception"] = summary.by_status.get("exception", 0) + 1
            continue
        summary.reports.append(r)
        st = r["app_status"]
        summary.by_status[st] = summary.by_status.get(st, 0) + 1
        if st in (ApplicationStatus.SUCCESS.value, ApplicationStatus.DRY_RUN_OK.value):
            summary.ok += 1
        else:
            summary.failed += 1

    if on_progress:
        await on_progress({"event": "done", "summary": summary.model_dump()})

    return summary
