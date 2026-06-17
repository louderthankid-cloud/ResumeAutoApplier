from aiogram import Router, F
from aiogram.types import CallbackQuery, BufferedInputFile

from services.db_service import DBService
from schemas.application import ApplicationStatus
from bot import keyboards as kb
from bot.callbacks import ResCB
from bot.utils import owned_candidate, safe_edit, build_applications_csv
from bot.formatters import outcome_summary, status_label, application_line, STATUS_ORDER

router = Router()


def _paginate(apps, page: int):
    size = kb.RESULTS_PAGE_SIZE
    page = max(page, 0)
    start = page * size
    chunk = apps[start : start + size]
    return chunk, page, page > 0, start + size < len(apps)


@router.callback_query(ResCB.filter(F.a == "open"))
async def results_open(cb: CallbackQuery, callback_data: ResCB):
    c = await owned_candidate(cb.from_user.id, callback_data.cid)
    if not c:
        await cb.answer("Не найдено", show_alert=True)
        return
    ost = await DBService.get_outcome_stats(c.id)
    if not ost["total"]:
        text = "Результаты\n\nПока нет заявок — запусти прогон."
    else:
        text = "<b>Результаты</b>\n\n" + outcome_summary(ost)
    await safe_edit(
        cb.bot,
        cb.message.chat.id,
        cb.message.message_id,
        text,
        kb.results_kb(c.id, ost),
    )
    await cb.answer()


@router.callback_query(ResCB.filter(F.a == "resp"))
async def results_responded(cb: CallbackQuery, callback_data: ResCB):
    c = await owned_candidate(cb.from_user.id, callback_data.cid)
    if not c:
        await cb.answer("Не найдено", show_alert=True)
        return
    apps = await DBService.get_applications(c.id, responded=True)
    chunk, page, has_prev, has_next = _paginate(apps, callback_data.page)
    header = f"<b>Откликнулись</b> — {len(apps)}\n\n"
    body = "\n\n".join(application_line(a) for a in chunk) or "пусто"
    await safe_edit(
        cb.bot,
        cb.message.chat.id,
        cb.message.message_id,
        header + body,
        kb.results_list_kb(c.id, "resp", 0, page, has_prev, has_next),
    )
    await cb.answer()


@router.callback_query(ResCB.filter(F.a == "list"))
async def results_list(cb: CallbackQuery, callback_data: ResCB):
    c = await owned_candidate(cb.from_user.id, callback_data.cid)
    if not c:
        await cb.answer("Не найдено", show_alert=True)
        return
    if callback_data.si < 0 or callback_data.si >= len(STATUS_ORDER):
        await cb.answer("Неизвестный статус", show_alert=True)
        return

    status_value = STATUS_ORDER[callback_data.si]
    # только недозвонившиеся с этим статусом
    apps = await DBService.get_applications(
        c.id, ApplicationStatus(status_value), responded=False
    )
    chunk, page, has_prev, has_next = _paginate(apps, callback_data.page)
    header = f"<b>{status_label(status_value)}</b> — {len(apps)}\n\n"
    body = "\n\n".join(application_line(a) for a in chunk) or "пусто"
    await safe_edit(
        cb.bot,
        cb.message.chat.id,
        cb.message.message_id,
        header + body,
        kb.results_list_kb(c.id, "list", callback_data.si, page, has_prev, has_next),
    )
    await cb.answer()


@router.callback_query(ResCB.filter(F.a == "csv"))
async def results_csv(cb: CallbackQuery, callback_data: ResCB):
    c = await owned_candidate(cb.from_user.id, callback_data.cid)
    if not c:
        await cb.answer("Не найдено", show_alert=True)
        return
    apps = await DBService.get_applications(c.id)
    if not apps:
        await cb.answer("Нет заявок для выгрузки", show_alert=True)
        return
    data = build_applications_csv(apps)
    await cb.message.answer_document(
        BufferedInputFile(data, filename=f"results_{c.id[:8]}.csv")
    )
    await cb.answer("Готово")
