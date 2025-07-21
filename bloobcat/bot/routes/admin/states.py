from aiogram.fsm.state import State, StatesGroup


class SendFSM(StatesGroup):
    """Состояния для рассылки сообщений"""
    waiting_for_audience = State()
    waiting_for_message = State()
    waiting_for_confirmation = State()


class UserSearchState(StatesGroup):
    """Состояния для поиска пользователей"""
    waiting_for_user_id = State()
    choosing_user = State()  # Новое состояние для выбора из списка 