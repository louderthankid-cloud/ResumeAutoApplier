import html

from schemas.application import ApplicationStatus

STATUS_LABELS: dict[str, str] = {
    ApplicationStatus.SUCCESS.value: "Отклик отправлен",
    ApplicationStatus.DRY_RUN_OK.value: "Откликнулись (тест)",
    ApplicationStatus.FILLED_DRY_RUN.value: "Откликнулись (тест)",
    ApplicationStatus.CAPTCHA.value: "Капча",
    ApplicationStatus.SITE_DOWN.value: "Сайт недоступен",
    ApplicationStatus.DDOS_PROTECTION.value: "Анти-бот защита",
    ApplicationStatus.FORM_FILL_FAILED.value: "Не справились с формой",
    ApplicationStatus.FORM_LOAD_FAILED.value: "Форма не загрузилась",
    ApplicationStatus.EMAIL_FAILED.value: "Письмо не ушло",
    ApplicationStatus.NO_CONTACTS_FOUND.value: "Нет контактов",
    ApplicationStatus.REQUIRES_LOGIN.value: "Нужен личный кабинет",
    ApplicationStatus.VACANCY_NOT_FOUND.value: "Вакансия не найдена",
}

STATUS_ORDER: list[str] = list(STATUS_LABELS.keys())

EMAIL_LABELS: dict[str, str] = {
    "sent": "отправлено",
    "dry_run": "отправлено (тест)",
    "not_found": "почты нет",
    "failed": "ошибка отправки",
}
FORM_LABELS: dict[str, str] = {
    "submitted": "отправлена",
    "filled_dry_run": "заполнена (тест)",
    "blocked_captcha": "капча",
    "requires_login": "нужен логин",
    "field_impossible": "поле не заполнить",
    "form_not_found": "формы нет",
    "load_failed": "не загрузилась",
    "fill_failed": "ошибка заполнения",
    "unexpected_error": "ошибка",
}

DIV = "──────────────"


def esc(value) -> str:
    return html.escape(str(value)) if value is not None else ""


def status_label(value: str) -> str:
    return STATUS_LABELS.get(value, value)


def email_label(value) -> str:
    return EMAIL_LABELS.get(value, value) if value else "—"


def form_label(value) -> str:
    return FORM_LABELS.get(value, value) if value else "—"


def app_outcome(a) -> str:
    """дозвонились или причина провала"""
    return "Откликнулись" if a.channel else status_label(a.status)


def candidate_card(c, ostats: dict) -> str:
    lines = [
        f"<b>{esc(c.name or 'Без имени')}</b>",
        f"Позиция: {esc(c.target_job)}",
    ]
    if c.email:
        lines.append(f"Email: {esc(c.email)}")
    if c.phone:
        lines.append(f"Телефон: {esc(c.phone)}")
    if c.resume_filename:
        lines.append(f"Резюме: {esc(c.resume_filename)}")
    lines.append(DIV)
    lines.append(
        f"Заявок: <b>{ostats['total']}</b> · Откликнулись: <b>{ostats['responded']}</b>"
    )
    return "\n".join(lines)


def validation_note(check) -> str:
    if check.warnings:
        return "Не распознано из резюме: " + ", ".join(esc(w) for w in check.warnings)
    return "Карточка заполнена."


def progress_panel(
    done: int, total: int, ok: int, failed: int, inflight: list[str], dry_run: bool
) -> str:
    mode = "тест" if dry_run else "боевой"
    lines = [
        f"<b>Прогон</b> · {mode}",
        DIV,
        f"Готово: <b>{done}/{total}</b>   Откликнулись: {ok}   Не вышло: {failed}",
    ]
    if inflight:
        lines.append("В работе:")
        lines.extend(f"— {esc(name)}" for name in inflight)
    return "\n".join(lines)


def outcome_summary(stats: dict) -> str:
    """дозвонились и причины недозвона"""
    ch = stats["channels"]
    parts = []
    if ch.get("email"):
        parts.append(f"письмо {ch['email']}")
    if ch.get("form"):
        parts.append(f"форма {ch['form']}")
    if ch.get("both"):
        parts.append(f"оба {ch['both']}")
    sub = f"  ({' · '.join(parts)})" if parts else ""

    lines = [
        f"Всего: <b>{stats['total']}</b>",
        f"Откликнулись: <b>{stats['responded']}</b>{sub}",
    ]
    failed = stats["failed"]
    if failed:
        lines.append(DIV)
        lines.append(f"Не дозвонились: <b>{sum(failed.values())}</b>")
        for st, cnt in sorted(failed.items(), key=lambda x: -x[1]):
            lines.append(f"  {status_label(st)}: {cnt}")
    return "\n".join(lines)


def application_line(a) -> str:
    """итог + оба канала + ссылка"""
    lines = [f"<b>{esc(a.company_name)}</b> — {app_outcome(a)}"]
    lines.append(f"  Письмо: {email_label(a.email_status)}")
    lines.append(f"  Форма: {form_label(a.form_status)}")
    url = a.vacancy_url or a.target_url or a.site_url or ""
    if url:
        lines.append(f"  {esc(url)}")
    return "\n".join(lines)
