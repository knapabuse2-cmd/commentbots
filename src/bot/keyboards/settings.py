"""
Settings keyboards — notification preferences toggle.
"""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def settings_menu_keyboard(prefs: dict) -> InlineKeyboardMarkup:
    """
    Settings menu with notification toggles.

    Args:
        prefs: Current notification preferences dict.
            {"comments": True, "bans": True, "errors": True, "rotations": True}
    """

    def _toggle_text(label: str, key: str) -> str:
        """Generate toggle button text with on/off indicator."""
        enabled = prefs.get(key, True)
        icon = "\u2705" if enabled else "\u274c"  # ✅ / ❌
        return f"{icon} {label}"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_toggle_text(
                        "\U0001f4ac \u041a\u043e\u043c\u043c\u0435\u043d\u0442\u0430\u0440\u0438\u0438",  # 💬 Комментарии
                        "comments",
                    ),
                    callback_data="settings:toggle:comments",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_toggle_text(
                        "\U0001f6ab \u0411\u0430\u043d\u044b",  # 🚫 Баны
                        "bans",
                    ),
                    callback_data="settings:toggle:bans",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_toggle_text(
                        "\U0001f534 \u041e\u0448\u0438\u0431\u043a\u0438",  # 🔴 Ошибки
                        "errors",
                    ),
                    callback_data="settings:toggle:errors",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_toggle_text(
                        "\U0001f504 \u0420\u043e\u0442\u0430\u0446\u0438\u0438",  # 🔄 Ротации
                        "rotations",
                    ),
                    callback_data="settings:toggle:rotations",
                ),
            ],
        ],
    )
