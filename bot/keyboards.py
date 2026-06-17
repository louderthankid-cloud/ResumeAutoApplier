from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from bot.callbacks import MenuCB, CandCB, RunCB, ResCB
from bot.formatters import STATUS_ORDER, status_label

PAGE_SIZE = 8
RESULTS_PAGE_SIZE = 8


def _btn(text, cb):
    return InlineKeyboardButton(text=text, callback_data=cb)


def home_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("Добавить кандидата", MenuCB(a="add").pack())],
            [_btn("Список кандидатов", MenuCB(a="page", page=0).pack())],
        ]
    )


def candidates_list(
    page_items, page: int, has_prev: bool, has_next: bool
) -> InlineKeyboardMarkup:
    rows = []
    for c, total in page_items:
        label = f"{c.name or 'Без имени'} · {c.target_job} ({total})"
        rows.append([_btn(label[:60], CandCB(a="open", cid=c.id).pack())])
    nav = []
    if has_prev:
        nav.append(_btn("← Пред.", MenuCB(a="page", page=page - 1).pack()))
    if has_next:
        nav.append(_btn("След. →", MenuCB(a="page", page=page + 1).pack()))
    if nav:
        rows.append(nav)
    rows.append([_btn("На главную", MenuCB(a="home").pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def candidate_card_kb(cid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn("Запустить", CandCB(a="run", cid=cid).pack()),
                _btn("Результаты", ResCB(a="open", cid=cid).pack()),
            ],
            [
                _btn("Изменить", CandCB(a="edit", cid=cid).pack()),
                _btn("Удалить", CandCB(a="delete", cid=cid).pack()),
            ],
            [_btn("К списку", MenuCB(a="page", page=0).pack())],
        ]
    )


def edit_menu_kb(cid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("Позиция", CandCB(a="edit_job", cid=cid).pack())],
            [_btn("Имя", CandCB(a="edit_name", cid=cid).pack())],
            [_btn("Заменить резюме", CandCB(a="edit_resume", cid=cid).pack())],
            [_btn("Назад", CandCB(a="open", cid=cid).pack())],
        ]
    )


def confirm_delete_kb(cid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn("Да, удалить", CandCB(a="delete_yes", cid=cid).pack()),
                _btn("Отмена", CandCB(a="open", cid=cid).pack()),
            ]
        ]
    )


def scope_kb(cid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn("20", RunCB(a="mode", cid=cid, scope=20).pack()),
                _btn("50", RunCB(a="mode", cid=cid, scope=50).pack()),
                _btn("100", RunCB(a="mode", cid=cid, scope=100).pack()),
            ],
            [_btn("Отмена", CandCB(a="open", cid=cid).pack())],
        ]
    )


def mode_kb(cid: str, scope: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("Тест", RunCB(a="go", cid=cid, scope=scope, mode="t").pack())],
            [_btn("Боевой", RunCB(a="confirm", cid=cid, scope=scope, mode="r").pack())],
            [_btn("Назад", CandCB(a="run", cid=cid).pack())],
        ]
    )


def confirm_real_kb(cid: str, scope: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn(
                    "Да, отправлять по-настоящему",
                    RunCB(a="go", cid=cid, scope=scope, mode="r").pack(),
                )
            ],
            [_btn("Отмена", CandCB(a="open", cid=cid).pack())],
        ]
    )


def stop_kb(cid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[_btn("Остановить", RunCB(a="stop", cid=cid).pack())]]
    )


def results_open_kb(cid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("Результаты", ResCB(a="open", cid=cid).pack())],
            [_btn("К кандидату", CandCB(a="open", cid=cid).pack())],
        ]
    )


def results_kb(cid: str, ostats: dict) -> InlineKeyboardMarkup:
    rows = []
    if ostats["responded"]:
        rows.append(
            [
                _btn(
                    f"Откликнулись ({ostats['responded']})",
                    ResCB(a="resp", cid=cid).pack(),
                )
            ]
        )
    for st, cnt in sorted(ostats["failed"].items(), key=lambda x: -x[1]):
        if st not in STATUS_ORDER:
            continue
        si = STATUS_ORDER.index(st)
        rows.append(
            [
                _btn(
                    f"{status_label(st)} ({cnt})",
                    ResCB(a="list", cid=cid, si=si).pack(),
                )
            ]
        )
    rows.append([_btn("Выгрузить CSV", ResCB(a="csv", cid=cid).pack())])
    rows.append([_btn("К кандидату", CandCB(a="open", cid=cid).pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def results_list_kb(
    cid: str, action: str, si: int, page: int, has_prev: bool, has_next: bool
) -> InlineKeyboardMarkup:
    nav = []
    if has_prev:
        nav.append(
            _btn("← Пред.", ResCB(a=action, cid=cid, si=si, page=page - 1).pack())
        )
    if has_next:
        nav.append(
            _btn("След. →", ResCB(a=action, cid=cid, si=si, page=page + 1).pack())
        )
    rows = []
    if nav:
        rows.append(nav)
    rows.append([_btn("К результатам", ResCB(a="open", cid=cid).pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def skip_name_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_btn("Пропустить", "skip_name")]])


def cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_btn("Отмена", "fsm_cancel")]])
