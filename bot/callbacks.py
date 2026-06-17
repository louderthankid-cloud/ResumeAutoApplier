from aiogram.filters.callback_data import CallbackData


class MenuCB(CallbackData, prefix="m"):
    a: str  # home | add | page
    page: int = 0


class CandCB(CallbackData, prefix="c"):
    # open | run | results | edit | edit_name | edit_job | edit_resume | delete | delete_yes
    a: str
    cid: str


class RunCB(CallbackData, prefix="r"):
    # mode (после охвата) | confirm (подтв. боевого) | go | stop
    a: str
    cid: str
    scope: int = 0
    mode: str = ""  # "t" | "r"


class ResCB(CallbackData, prefix="res"):
    # open (сводка) | list (по статусу) | csv
    a: str
    cid: str
    si: int = 0  # индекс статуса в STATUS_ORDER
    page: int = 0
