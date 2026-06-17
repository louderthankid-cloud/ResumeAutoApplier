from aiogram.exceptions import TelegramBadRequest

# chat_id -> message_id текущей панели
_panels: dict[int, int] = {}


def set_panel(chat_id: int, message_id: int) -> None:
    """сделать это сообщение активной панелью чата"""
    _panels[chat_id] = message_id


async def render(
    bot, chat_id: int, text: str, reply_markup=None, *, force_new: bool = False
) -> int:
    mid = _panels.get(chat_id)

    if mid is not None and not force_new:
        try:
            await bot.edit_message_text(
                text, chat_id=chat_id, message_id=mid, reply_markup=reply_markup
            )
            return mid
        except TelegramBadRequest as e:
            if "message is not modified" in str(e).lower():
                return mid
            # сообщение нельзя редактировать (удалено/устарело) — отправим новое ниже

    msg = await bot.send_message(chat_id, text, reply_markup=reply_markup)
    if mid is not None and mid != msg.message_id:
        try:
            await bot.delete_message(chat_id, mid)
        except TelegramBadRequest:
            pass
    _panels[chat_id] = msg.message_id
    return msg.message_id
