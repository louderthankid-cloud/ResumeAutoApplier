import asyncio
import time

from aiogram import Router, F
from aiogram.types import CallbackQuery

from services.pipeline import run_candidate_pipeline
from services.db_service import DBService
from bot import keyboards as kb
from bot.callbacks import CandCB, RunCB
from bot.runs import registry
from bot.utils import owned_candidate, safe_edit
from bot.formatters import progress_panel, outcome_summary, esc

router = Router()


@router.callback_query(CandCB.filter(F.a == "run"))
async def run_scope(cb: CallbackQuery, callback_data: CandCB):
    if registry.is_running(cb.from_user.id):
        await cb.answer(
            "У тебя уже идёт прогон — дождись или останови его.", show_alert=True
        )
        return
    c = await owned_candidate(cb.from_user.id, callback_data.cid)
    if not c:
        await cb.answer("Кандидат не найден", show_alert=True)
        return
    await safe_edit(
        cb.bot,
        cb.message.chat.id,
        cb.message.message_id,
        f"▶ Запуск «{esc(c.name or c.target_job)}»\nСколько компаний обработать?",
        kb.scope_kb(c.id),
    )
    await cb.answer()


@router.callback_query(RunCB.filter(F.a == "mode"))
async def run_mode(cb: CallbackQuery, callback_data: RunCB):
    c = await owned_candidate(cb.from_user.id, callback_data.cid)
    if not c:
        await cb.answer("Не найдено", show_alert=True)
        return
    await safe_edit(
        cb.bot,
        cb.message.chat.id,
        cb.message.message_id,
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
    await safe_edit(
        cb.bot,
        cb.message.chat.id,
        cb.message.message_id,
        f"<b>БОЕВОЙ режим</b>\nПисьма уйдут реальным адресатам, формы будут отправлены.\n"
        f"Охват: {callback_data.scope}. Точно запустить?",
        kb.confirm_real_kb(c.id, callback_data.scope),
    )
    await cb.answer()


@router.callback_query(RunCB.filter(F.a == "go"))
async def run_go(cb: CallbackQuery, callback_data: RunCB):
    tg_id = cb.from_user.id
    if registry.is_running(tg_id):
        await cb.answer("Уже идёт прогон", show_alert=True)
        return
    c = await owned_candidate(tg_id, callback_data.cid)
    if not c:
        await cb.answer("Не найдено", show_alert=True)
        return

    hh_limit = callback_data.scope or 50
    dry_run = callback_data.mode != "r"
    await safe_edit(
        cb.bot,
        cb.message.chat.id,
        cb.message.message_id,
        f"Запускаю… (0/{hh_limit}, {'тест' if dry_run else 'боевой'})",
        kb.stop_kb(c.id),
    )
    task = asyncio.create_task(
        _run_task(
            cb.bot,
            cb.message.chat.id,
            cb.message.message_id,
            c,
            hh_limit,
            dry_run,
            tg_id,
        )
    )
    registry.register(tg_id, task)
    await cb.answer("Поехали")


@router.callback_query(RunCB.filter(F.a == "stop"))
async def run_stop(cb: CallbackQuery, callback_data: RunCB):
    stopped = registry.request_stop(cb.from_user.id)
    await cb.answer("Останавливаю…" if stopped else "Активного прогона нет")


async def _run_task(
    bot, chat_id: int, msg_id: int, candidate, hh_limit: int, dry_run: bool, tg_id: int
):
    inflight: dict[int, str] = {}
    counts = {"done": 0, "ok": 0, "failed": 0, "total": hh_limit}
    last = {"t": 0.0}

    async def render(force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - last["t"] < 2.0:
            return
        last["t"] = now
        await safe_edit(
            bot, chat_id, msg_id,
            progress_panel(
                counts["done"], counts["total"], counts["ok"], counts["failed"],
                list(inflight.values()), dry_run,
            ),
            kb.stop_kb(candidate.id),
        )

    async def on_progress(event: dict) -> None:
        et = event["event"]
        if et == "start":
            counts["total"] = event["total"]
            await render(force=True)
        elif et == "company_start":
            inflight[event["task_id"]] = event["company"]
            await render()
        elif et == "company":
            inflight.pop(event["task_id"], None)
            counts.update(
                done=event["done"], ok=event["ok"], failed=event["failed"], total=event["total"]
            )
            await render()

    head = "тест" if dry_run else "боевой"
    try:
        await run_candidate_pipeline(
            candidate,
            hh_limit=hh_limit,
            dry_run=dry_run,
            acquire_slot=lambda: registry.get_pool().slot(tg_id),
            verbose=False,
            on_progress=on_progress,
        )
        ost = await DBService.get_outcome_stats(candidate.id)
        await safe_edit(
            bot,
            chat_id,
            msg_id,
            f"<b>Готово</b> · {head}\n\n" + outcome_summary(ost),
            kb.results_open_kb(candidate.id),
        )
    except asyncio.CancelledError:
        ost = await DBService.get_outcome_stats(candidate.id)
        await safe_edit(
            bot,
            chat_id,
            msg_id,
            "<b>Остановлено</b>\n\n" + outcome_summary(ost),
            kb.results_open_kb(candidate.id),
        )
    except Exception as e:
        await safe_edit(
            bot,
            chat_id,
            msg_id,
            f"Ошибка прогона: {esc(e)}",
            kb.results_open_kb(candidate.id),
        )
    finally:
        registry.cleanup(tg_id)
