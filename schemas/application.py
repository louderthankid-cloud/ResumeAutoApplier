from enum import Enum
from pydantic import BaseModel, Field


class ApplicationStatus(str, Enum):
    """итоговый статус попытки отклика на одну компанию"""

    SUCCESS = "success"  # форма отправлена и/или письмо ушло адресату
    DRY_RUN_OK = "dry_run_ok"  # форма заполнена и/или письмо ушло себе

    # временные ошибки — ретраятся при перезапуске
    SITE_DOWN = "site_down"  # таймаут
    DDOS_PROTECTION = "ddos_protection"  # анти ддос
    CAPTCHA = "captcha"  # каптча помешала заполнить или отправить форму
    FORM_FILL_FAILED = "form_fill_failed"  # агент не справился с формой
    FORM_LOAD_FAILED = "form_load_failed"  # страница формы не загрузилась
    EMAIL_FAILED = "email_failed"  # не отправили мыло

    # постоянные — не ретраятся
    NO_CONTACTS_FOUND = "no_contacts_found"  # на сайте нет ни форм, ни почт
    REQUIRES_LOGIN = "requires_login"  # отклик только через личный кабинет
    VACANCY_NOT_FOUND = (
        "vacancy_not_found"  # в каталоге нет нужной вакансии и нет общей формы
    )

    FILLED_DRY_RUN = "filled_dry_run"


# статусы, которые имеет смысл повторять при перезапуске пайплайна
RETRYABLE_STATUSES = {
    ApplicationStatus.SITE_DOWN,
    ApplicationStatus.DDOS_PROTECTION,
    ApplicationStatus.CAPTCHA,
    ApplicationStatus.FORM_FILL_FAILED,
    ApplicationStatus.FORM_LOAD_FAILED,
    ApplicationStatus.EMAIL_FAILED,
}

MAX_ATTEMPTS = 3


class EmailStatus(str, Enum):
    """исход по каналу email"""

    SENT = "sent"  # почта найдена, письмо отправлено адресату
    DRY_RUN = "dry_run"  # почта найдена, письмо отправлено себе
    NOT_FOUND = "not_found"  # подходящей почты не нашли
    FAILED = "failed"  # почта есть, но smtp не отправил

    @property
    def ok(self) -> bool:
        return self in (EmailStatus.SENT, EmailStatus.DRY_RUN)


class FormFillStatus(str, Enum):
    """самоотчёт работы с формой, семантический слой"""

    SUBMITTED = "submitted"  # форма отправлена
    FILLED_DRY_RUN = "filled_dry_run"  # реально заполнена, сабмит отменён
    BLOCKED_CAPTCHA = "blocked_captcha"  # каптча в форме или после сабмита
    REQUIRES_LOGIN = "requires_login"  # без авторизации не отправить
    FIELD_IMPOSSIBLE = "field_impossible"  # обязательное поле невозможно заполнить
    FORM_NOT_FOUND = "form_not_found"  # формы отклика на странице нет
    LOAD_FAILED = "load_failed"  # страница формы не загрузилась
    FILL_FAILED = "fill_failed"  # прочие провалы (LLM-ошибка, лимит шагов итд)
    UNEXPECTED_ERROR = "unexpected_error"  # неклассифицированный краш

    @property
    def ok(self) -> bool:
        return self in (FormFillStatus.SUBMITTED, FormFillStatus.FILLED_DRY_RUN)


class FormScope(str, Enum):
    """Где именно заполняли форму."""

    VACANCY = "vacancy"
    GENERAL = "general"


class FormFillReport(BaseModel):
    """Результат FormFillerOrchestrator.run_loop()."""

    status: FormFillStatus
    detail: str = ""
    unfilled_fields: list[str] = Field(default_factory=list)
    steps_used: int = 0
    fields_filled: int = 0  # сколько реальных полей кандидата заполнено
    captcha_detected: bool = False  # детккт каптчи

    @property
    def ok(self) -> bool:
        return self.status in (
            FormFillStatus.SUBMITTED,
            FormFillStatus.FILLED_DRY_RUN,
        )


FORM_STATUS_TO_APPLICATION = {
    FormFillStatus.SUBMITTED: ApplicationStatus.SUCCESS,
    FormFillStatus.FILLED_DRY_RUN: ApplicationStatus.DRY_RUN_OK,
    FormFillStatus.BLOCKED_CAPTCHA: ApplicationStatus.CAPTCHA,
    FormFillStatus.REQUIRES_LOGIN: ApplicationStatus.REQUIRES_LOGIN,
    FormFillStatus.FIELD_IMPOSSIBLE: ApplicationStatus.FORM_FILL_FAILED,
    FormFillStatus.FORM_NOT_FOUND: ApplicationStatus.NO_CONTACTS_FOUND,
    FormFillStatus.LOAD_FAILED: ApplicationStatus.FORM_LOAD_FAILED,
    FormFillStatus.FILL_FAILED: ApplicationStatus.FORM_FILL_FAILED,
    FormFillStatus.UNEXPECTED_ERROR: ApplicationStatus.FORM_FILL_FAILED,
}
