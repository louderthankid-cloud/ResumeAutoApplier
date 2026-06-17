from aiogram.fsm.state import State, StatesGroup


class CreateCandidate(StatesGroup):
    waiting_resume = State()
    waiting_name = State()
    waiting_job = State()


class EditCandidate(StatesGroup):
    waiting_name = State()
    waiting_job = State()
    waiting_resume = State()
