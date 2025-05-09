from typing import TYPE_CHECKING

# from bloobcat.db.users import Users # Moved to TYPE_CHECKING

if TYPE_CHECKING:
    from bloobcat.db.users import Users

def get_user_locale(user: 'Users') -> str:
    """
    Определяет язык пользователя по полю language_code, возвращает 'ru' или 'en'.
    """
    code = getattr(user, 'language_code', None)
    return 'ru' if code and code.lower().startswith('ru') else 'en' 