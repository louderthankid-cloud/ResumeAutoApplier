import csv
import io

from aiogram.exceptions import TelegramBadRequest

from services.db_service import DBService


async def owned_candidate(user_id: int, cid: str):
    """возвращает кандидата, только если он принадлежит этому hr'у, иначе - none"""
    c = await DBService.get_candidate_by_id(cid)
    if not c or c.tg_id != str(user_id):
        return None
    return c


async def safe_edit(
    bot, chat_id: int, message_id: int, text: str, reply_markup=None
) -> None:
    """edit_message_text без падения на 'message is not modified' и т.п."""
    try:
        await bot.edit_message_text(
            text, chat_id=chat_id, message_id=message_id, reply_markup=reply_markup
        )
    except TelegramBadRequest:
        pass


async def safe_delete(bot, chat_id: int, message_id: int) -> None:
    """удаление сообщения без падения"""
    try:
        await bot.delete_message(chat_id, message_id)
    except TelegramBadRequest:
        pass


def build_applications_csv(apps) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(
        [
            "Компания",
            "Статус",
            "Канал",
            "Email",
            "Вакансия",
            "Сайт",
            "Попыток",
            "Ошибка",
        ]
    )
    for a in apps:
        w.writerow(
            [
                a.company_name,
                a.status,
                a.channel or "",
                a.hr_email or "",
                a.vacancy_url or "",
                a.site_url or "",
                a.attempts,
                (a.error_detail or "").replace("\n", " "),
            ]
        )
    return buf.getvalue().encode("utf-8-sig")
