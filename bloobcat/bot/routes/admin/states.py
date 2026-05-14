from aiogram.fsm.state import State, StatesGroup


class SendFSM(StatesGroup):
    """Состояния для рассылки сообщений."""

    waiting_for_channel = State()
    waiting_for_segment = State()
    waiting_for_segment_value = State()
    waiting_for_push_title = State()
    waiting_for_push_body = State()
    waiting_for_message = State()
    waiting_for_buttons = State()
    waiting_for_confirmation = State()


class UserSearchState(StatesGroup):
    """Состояния для поиска пользователей."""

    waiting_for_user_id = State()
    choosing_user = State()  # Новое состояние для выбора из списка
