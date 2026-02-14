"""
Main menu keyboard.
"""

from aiogram.types import ReplyKeyboardMarkup, KeyboardButton


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    """Main bot menu — always visible at the bottom."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="\U0001f4f1 \u0410\u043a\u043a\u0430\u0443\u043d\u0442\u044b"),    # 📱 Аккаунты
                KeyboardButton(text="\U0001f4ac \u041a\u0430\u043c\u043f\u0430\u043d\u0438\u0438"),    # 💬 Кампании
            ],
            [
                KeyboardButton(text="\U0001f4ca \u0421\u0442\u0430\u0442\u0438\u0441\u0442\u0438\u043a\u0430"),   # 📊 Статистика
                KeyboardButton(text="\u2699\ufe0f \u041d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0438"),   # ⚙️ Настройки
            ],
            [
                KeyboardButton(text="\U0001f310 \u041f\u0440\u043e\u043a\u0441\u0438"),    # 🌐 Прокси
            ],
        ],
        resize_keyboard=True,
    )
