from aiogram import Router, F
from aiogram.types import CallbackQuery

from services.db_service import DBService
from core.config import settings
from bot import keyboards as kb
from bot import panel
from bot.callbacks import CandCB, RunCB
from bot.runs import run_manager
from bot.utils import owned_candidate
from bot.formatters import candidate_card, esc

router = Router()


@router.callback_query(CandCB.filter(F.a == "run"))
async def run_scope(cb: CallbackQuery, callback_data: CandCB):
    c = await owned_candidate(cb.from_user.id, callback_data.cid)
    if not c:
        await cb.answer("Кандидат не найден", show_alert=True)
        return
    if run_manager.is_running(c.id):
        await cb.answer("Этот кандидат уже запущен.", show_alert=True)
        return
    if not run_manager.can_start():
        await cb.answer(
            f"Лимит одновременных прогонов ({settings.MAX_CONCURRENT_RUNS}). Дождись завершения.",
            show_alert=True,
        )
        return
    panel.set_panel(cb.message.chat.id, cb.message.message_id)
    await panel.render(
        cb.bot,
        cb.message.chat.id,
        f"Запуск «{esc(c.name or c.target_job)}»\nСколько компаний обработать?",
        kb.scope_kb(c.id),
    )
    await cb.answer()


@router.callback_query(RunCB.filter(F.a == "mode"))
async def run_mode(cb: CallbackQuery, callback_data: RunCB):
    c = await owned_candidate(cb.from_user.id, callback_data.cid)
    if not c:
        await cb.answer("Не найдено", show_alert=True)
        return
    panel.set_panel(cb.message.chat.id, cb.message.message_id)
    await panel.render(
        cb.bot,
        cb.message.chat.id,
        f"Охват: <b>{callback_data.scope}</b> компаний.\n\n"
        "<b>Тест</b> — письма себе, формы НЕ отправляются.\n"
        "<b>Боевой</b> — реальная отправка.",
        kb.mode_kb(c.id, callback_data.scope),
    )
    await cb.answer()


@router.callback_query(RunCB.filter(F.a == "confirm"))
async def run_confirm(cb: CallbackQuery, callback_data: RunCB):
    c = await owned_candidate(cb.from_user.id, callback_data.cid)
    if not c:
        await cb.answer("Не найдено", show_alert=True)
        return
    panel.set_panel(cb.message.chat.id, cb.message.message_id)
    await panel.render(
        cb.bot,
        cb.message.chat.id,
        f"<b>БОЕВОЙ режим</b>\nПисьма уйдут реальным адресатам, формы будут отправлены.\n"
        f"Охват: {callback_data.scope}. Точно запустить?",
        kb.confirm_real_kb(c.id, callback_data.scope),
    )
    await cb.answer()


@router.callback_query(RunCB.filter(F.a == "go"))
async def run_go(cb: CallbackQuery, callback_data: RunCB):
    tg_id = cb.from_user.id
    c = await owned_candidate(tg_id, callback_data.cid)
    if not c:
        await cb.answer("Не найдено", show_alert=True)
        return
    hh_limit = callback_data.scope or 50
    dry_run = callback_data.mode != "r"
    ok, reason = await run_manager.start(
        cb.bot, cb.message.chat.id, tg_id, c, hh_limit, dry_run
    )
    if not ok:
        await cb.answer(reason, show_alert=True)
        return
    panel.set_panel(cb.message.chat.id, cb.message.message_id)
    ost = await DBService.get_outcome_stats(c.id)
    await panel.render(
        cb.bot, cb.message.chat.id, candidate_card(c, ost), kb.candidate_card_kb(c.id)
    )
    await cb.answer("Запущено")


@router.callback_query(RunCB.filter(F.a == "stop"))
async def run_stop(cb: CallbackQuery, callback_data: RunCB):
    stopped = run_manager.stop(callback_data.cid)
    await cb.answer("Останавливаю…" if stopped else "Прогон не найден")
