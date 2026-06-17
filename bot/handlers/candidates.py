import os
import asyncio
from io import BytesIO

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from services.db_service import DBService
from services.resume_parser import (
    extract_resume_text,
    ResumeParseError,
    SUPPORTED_EXTENSIONS,
)
from bot import keyboards as kb
from bot import panel
from bot.callbacks import MenuCB, CandCB
from bot.states import CreateCandidate, EditCandidate
from bot.formatters import candidate_card, validation_note, esc
from bot.utils import owned_candidate, safe_delete

router = Router()

MAX_RESUME_BYTES = 10 * 1024 * 1024


async def _home_view(tg_id: int):
    cands = await DBService.get_hr_candidates(str(tg_id))
    text = (
        "<b>Resume Auto Applier</b>\n"
        f"Кандидатов: <b>{len(cands)}</b>\n\n"
        "Добавь кандидата или открой список."
    )
    return text, kb.home_kb()


async def _list_view(tg_id: int, page: int):
    cands = await DBService.get_hr_candidates(str(tg_id))
    if not cands:
        return "Пока нет кандидатов.\nНажми «Добавить кандидата».", kb.home_kb()

    start = page * kb.PAGE_SIZE
    if start >= len(cands):
        page, start = 0, 0
    page_cands = cands[start : start + kb.PAGE_SIZE]

    items = []
    for c in page_cands:
        ost = await DBService.get_outcome_stats(c.id)
        items.append((c, ost["total"]))

    has_prev = page > 0
    has_next = start + kb.PAGE_SIZE < len(cands)
    return f"<b>Кандидаты</b> ({len(cands)})", kb.candidates_list(
        items, page, has_prev, has_next
    )


async def _card_view(c):
    ost = await DBService.get_outcome_stats(c.id)
    return candidate_card(c, ost), kb.candidate_card_kb(c.id)


# команды


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await safe_delete(message.bot, message.chat.id, message.message_id)
    text, markup = await _home_view(message.from_user.id)
    await panel.render(message.bot, message.chat.id, text, markup, force_new=True)


@router.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext):
    await state.clear()
    await safe_delete(message.bot, message.chat.id, message.message_id)
    text, markup = await _home_view(message.from_user.id)
    await panel.render(message.bot, message.chat.id, text, markup, force_new=True)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await safe_delete(message.bot, message.chat.id, message.message_id)
    text, markup = await _home_view(message.from_user.id)
    await panel.render(message.bot, message.chat.id, text, markup)


@router.callback_query(F.data == "fsm_cancel")
async def cb_cancel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    panel.set_panel(cb.message.chat.id, cb.message.message_id)
    text, markup = await _home_view(cb.from_user.id)
    await panel.render(cb.bot, cb.message.chat.id, text, markup)
    await cb.answer("Отменено")


# home


@router.callback_query(MenuCB.filter(F.a == "home"))
async def cb_home(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    panel.set_panel(cb.message.chat.id, cb.message.message_id)
    text, markup = await _home_view(cb.from_user.id)
    await panel.render(cb.bot, cb.message.chat.id, text, markup)
    await cb.answer()


@router.callback_query(MenuCB.filter(F.a == "page"))
async def cb_page(cb: CallbackQuery, callback_data: MenuCB, state: FSMContext):
    await state.clear()
    panel.set_panel(cb.message.chat.id, cb.message.message_id)
    text, markup = await _list_view(cb.from_user.id, callback_data.page)
    await panel.render(cb.bot, cb.message.chat.id, text, markup)
    await cb.answer()


@router.callback_query(CandCB.filter(F.a == "open"))
async def cb_card(cb: CallbackQuery, callback_data: CandCB, state: FSMContext):
    await state.clear()
    c = await owned_candidate(cb.from_user.id, callback_data.cid)
    if not c:
        await cb.answer("Кандидат не найден", show_alert=True)
        return
    panel.set_panel(cb.message.chat.id, cb.message.message_id)
    text, markup = await _card_view(c)
    await panel.render(cb.bot, cb.message.chat.id, text, markup)
    await cb.answer()


# создание кандидата


@router.callback_query(MenuCB.filter(F.a == "add"))
async def create_start(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    panel.set_panel(cb.message.chat.id, cb.message.message_id)
    await state.set_state(CreateCandidate.waiting_resume)
    await panel.render(
        cb.bot,
        cb.message.chat.id,
        "Пришли файл резюме (PDF, DOCX или TXT).",
        kb.cancel_kb(),
    )
    await cb.answer()


@router.message(CreateCandidate.waiting_resume, F.document)
async def create_resume(message: Message, state: FSMContext):
    bot, chat_id = message.bot, message.chat.id
    await safe_delete(bot, chat_id, message.message_id)
    doc = message.document
    fname = doc.file_name or ""
    ext = os.path.splitext(fname)[1].lower()
    if ext not in SUPPORTED_EXTENSIONS:
        await panel.render(
            bot,
            chat_id,
            f"Формат «{ext or '?'}» не подходит. Нужен PDF/DOCX/TXT.\n\nПришли файл ещё раз.",
            kb.cancel_kb(),
        )
        return
    if doc.file_size and doc.file_size > MAX_RESUME_BYTES:
        await panel.render(
            bot, chat_id, "Файл больше 10 МБ. Пришли поменьше.", kb.cancel_kb()
        )
        return

    bio = BytesIO()
    await bot.download(doc, destination=bio)
    data = bio.getvalue()
    try:
        await asyncio.to_thread(extract_resume_text, data, fname)
    except ResumeParseError as e:
        await panel.render(
            bot,
            chat_id,
            f"Не смог извлечь текст: {esc(e)}\n\nПришли текстовый PDF/DOCX.",
            kb.cancel_kb(),
        )
        return

    await state.update_data(
        resume_bytes=data, resume_filename=fname, resume_mime=doc.mime_type
    )
    await state.set_state(CreateCandidate.waiting_name)
    await panel.render(
        bot,
        chat_id,
        f"Резюме принято: {esc(fname)}\n\nИмя кандидата?",
        kb.skip_name_kb(),
    )


@router.message(CreateCandidate.waiting_resume)
async def create_resume_invalid(message: Message):
    await safe_delete(message.bot, message.chat.id, message.message_id)
    await panel.render(
        message.bot,
        message.chat.id,
        "Нужен файл-документ (PDF/DOCX/TXT).",
        kb.cancel_kb(),
    )


@router.callback_query(CreateCandidate.waiting_name, F.data == "skip_name")
async def create_skip_name(cb: CallbackQuery, state: FSMContext):
    await state.update_data(name=None)
    await state.set_state(CreateCandidate.waiting_job)
    panel.set_panel(cb.message.chat.id, cb.message.message_id)
    await panel.render(
        cb.bot,
        cb.message.chat.id,
        "На какую позицию ищем? (одна строка)",
        kb.cancel_kb(),
    )
    await cb.answer()


@router.message(CreateCandidate.waiting_name, F.text)
async def create_name(message: Message, state: FSMContext):
    await safe_delete(message.bot, message.chat.id, message.message_id)
    await state.update_data(name=message.text.strip())
    await state.set_state(CreateCandidate.waiting_job)
    await panel.render(
        message.bot,
        message.chat.id,
        "На какую позицию ищем? (одна строка)",
        kb.cancel_kb(),
    )


@router.message(CreateCandidate.waiting_job, F.text)
async def create_job(message: Message, state: FSMContext):
    bot, chat_id = message.bot, message.chat.id
    await safe_delete(bot, chat_id, message.message_id)
    data = await state.get_data()
    target_job = message.text.strip()
    await state.clear()
    try:
        candidate, check = await DBService.create_candidate_from_file(
            tg_id=str(message.from_user.id),
            file_bytes=data["resume_bytes"],
            filename=data["resume_filename"],
            target_job=target_job,
            name=data.get("name"),
            mime=data.get("resume_mime"),
        )
    except ResumeParseError as e:
        text, markup = await _home_view(message.from_user.id)
        await panel.render(bot, chat_id, f"Ошибка разбора резюме: {esc(e)}", markup)
        return
    ost = await DBService.get_outcome_stats(candidate.id)
    await panel.render(
        bot,
        chat_id,
        candidate_card(candidate, ost) + "\n\n" + validation_note(check),
        kb.candidate_card_kb(candidate.id),
    )


# изменение


@router.callback_query(CandCB.filter(F.a == "edit"))
async def edit_menu(cb: CallbackQuery, callback_data: CandCB):
    c = await owned_candidate(cb.from_user.id, callback_data.cid)
    if not c:
        await cb.answer("Не найдено", show_alert=True)
        return
    panel.set_panel(cb.message.chat.id, cb.message.message_id)
    await panel.render(
        cb.bot, cb.message.chat.id, "Что изменить?", kb.edit_menu_kb(c.id)
    )
    await cb.answer()


async def _edit_start(cb: CallbackQuery, state: FSMContext, cid: str, st, prompt: str):
    c = await owned_candidate(cb.from_user.id, cid)
    if not c:
        await cb.answer("Не найдено", show_alert=True)
        return
    panel.set_panel(cb.message.chat.id, cb.message.message_id)
    await state.set_state(st)
    await state.update_data(cid=c.id)
    await panel.render(cb.bot, cb.message.chat.id, prompt, kb.cancel_kb())
    await cb.answer()


@router.callback_query(CandCB.filter(F.a == "edit_job"))
async def edit_job_start(cb: CallbackQuery, callback_data: CandCB, state: FSMContext):
    await _edit_start(
        cb, state, callback_data.cid, EditCandidate.waiting_job, "Новая позиция:"
    )


@router.callback_query(CandCB.filter(F.a == "edit_name"))
async def edit_name_start(cb: CallbackQuery, callback_data: CandCB, state: FSMContext):
    await _edit_start(
        cb, state, callback_data.cid, EditCandidate.waiting_name, "Новое имя:"
    )


@router.callback_query(CandCB.filter(F.a == "edit_resume"))
async def edit_resume_start(
    cb: CallbackQuery, callback_data: CandCB, state: FSMContext
):
    await _edit_start(
        cb,
        state,
        callback_data.cid,
        EditCandidate.waiting_resume,
        "Пришли новый файл резюме (PDF/DOCX/TXT):",
    )


async def _render_card(bot, chat_id: int, cid: str):
    c = await DBService.get_candidate_by_id(cid)
    ost = await DBService.get_outcome_stats(cid)
    await panel.render(bot, chat_id, candidate_card(c, ost), kb.candidate_card_kb(cid))


@router.message(EditCandidate.waiting_job, F.text)
async def edit_job_save(message: Message, state: FSMContext):
    bot, chat_id = message.bot, message.chat.id
    await safe_delete(bot, chat_id, message.message_id)
    cid = (await state.get_data())["cid"]
    await state.clear()
    if not await owned_candidate(message.from_user.id, cid):
        return
    await DBService.update_candidate(cid, {"target_job": message.text.strip()})
    await _render_card(bot, chat_id, cid)


@router.message(EditCandidate.waiting_name, F.text)
async def edit_name_save(message: Message, state: FSMContext):
    bot, chat_id = message.bot, message.chat.id
    await safe_delete(bot, chat_id, message.message_id)
    cid = (await state.get_data())["cid"]
    await state.clear()
    if not await owned_candidate(message.from_user.id, cid):
        return
    await DBService.update_candidate(cid, {"name": message.text.strip()})
    await _render_card(bot, chat_id, cid)


@router.message(EditCandidate.waiting_resume, F.document)
async def edit_resume_save(message: Message, state: FSMContext):
    bot, chat_id = message.bot, message.chat.id
    await safe_delete(bot, chat_id, message.message_id)
    data = await state.get_data()
    cid = data["cid"]
    doc = message.document
    fname = doc.file_name or ""
    ext = os.path.splitext(fname)[1].lower()
    if ext not in SUPPORTED_EXTENSIONS:
        await panel.render(
            bot,
            chat_id,
            f"Формат «{ext or '?'}» не подходит. Нужен PDF/DOCX/TXT.",
            kb.cancel_kb(),
        )
        return
    if doc.file_size and doc.file_size > MAX_RESUME_BYTES:
        await panel.render(bot, chat_id, "Файл больше 10 МБ.", kb.cancel_kb())
        return

    bio = BytesIO()
    await bot.download(doc, destination=bio)
    raw = bio.getvalue()
    try:
        text = await asyncio.to_thread(extract_resume_text, raw, fname)
    except ResumeParseError as e:
        await panel.render(
            bot,
            chat_id,
            f"Не смог извлечь текст: {esc(e)}\n\nПришли другой файл.",
            kb.cancel_kb(),
        )
        return

    await state.clear()
    if not await owned_candidate(message.from_user.id, cid):
        return
    await DBService.update_candidate(
        cid,
        {
            "resume_text": text,
            "resume_blob": raw,
            "resume_filename": fname,
            "resume_mime": doc.mime_type,
        },
    )
    await _render_card(bot, chat_id, cid)


@router.message(EditCandidate.waiting_resume)
async def edit_resume_invalid(message: Message):
    await safe_delete(message.bot, message.chat.id, message.message_id)
    await panel.render(
        message.bot,
        message.chat.id,
        "Нужен файл-документ (PDF/DOCX/TXT).",
        kb.cancel_kb(),
    )


# удаление


@router.callback_query(CandCB.filter(F.a == "delete"))
async def delete_confirm(cb: CallbackQuery, callback_data: CandCB):
    c = await owned_candidate(cb.from_user.id, callback_data.cid)
    if not c:
        await cb.answer("Не найдено", show_alert=True)
        return
    panel.set_panel(cb.message.chat.id, cb.message.message_id)
    await panel.render(
        cb.bot,
        cb.message.chat.id,
        f"Удалить «{esc(c.name or c.target_job)}» и все его заявки?",
        kb.confirm_delete_kb(c.id),
    )
    await cb.answer()


@router.callback_query(CandCB.filter(F.a == "delete_yes"))
async def delete_do(cb: CallbackQuery, callback_data: CandCB):
    c = await owned_candidate(cb.from_user.id, callback_data.cid)
    if not c:
        await cb.answer("Не найдено", show_alert=True)
        return
    await DBService.delete_candidate(c.id)
    panel.set_panel(cb.message.chat.id, cb.message.message_id)
    text, markup = await _list_view(cb.from_user.id, 0)
    await panel.render(cb.bot, cb.message.chat.id, "Удалён.\n\n" + text, markup)
    await cb.answer("Удалено")
