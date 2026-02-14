"""
Account management keyboards.
"""

import uuid

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def accounts_menu_keyboard() -> InlineKeyboardMarkup:
    """Account management menu."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="\u2795 \u0414\u043e\u0431\u0430\u0432\u0438\u0442\u044c (\u0442\u0435\u043b\u0435\u0444\u043e\u043d)",     # ➕ Добавить (телефон)
                    callback_data="acc:add_phone",
                ),
                InlineKeyboardButton(
                    text="\U0001f4c1 \u0417\u0430\u0433\u0440\u0443\u0437\u0438\u0442\u044c session",    # 📁 Загрузить session
                    callback_data="acc:add_session",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="\U0001f4cb \u0421\u043f\u0438\u0441\u043e\u043a \u0430\u043a\u043a\u0430\u0443\u043d\u0442\u043e\u0432",    # 📋 Список аккаунтов
                    callback_data="acc:list:0",
                ),
            ],
        ],
    )


def account_list_keyboard(
    accounts: list,
    offset: int = 0,
    total: int = 0,
    page_size: int = 10,
) -> InlineKeyboardMarkup:
    """
    List of accounts with pagination.

    Each account is a button that opens its detail view.
    """
    buttons: list[list[InlineKeyboardButton]] = []

    # Status emoji mapping
    status_emoji = {
        "active": "\u2705",     # ✅
        "paused": "\u23f8",     # ⏸
        "banned": "\U0001f6ab", # 🚫
        "error": "\u274c",      # ❌
        "pending": "\u23f3",    # ⏳
        "auth_code": "\U0001f4e9", # 📩
        "auth_2fa": "\U0001f512",  # 🔒
    }

    for acc in accounts:
        emoji = status_emoji.get(acc.status.value, "\u2753")  # ❓
        label = f"{emoji} {acc.display_name}"
        buttons.append([
            InlineKeyboardButton(
                text=label,
                callback_data=f"acc:detail:{acc.id}",
            )
        ])

    # Pagination
    nav_buttons: list[InlineKeyboardButton] = []
    if offset > 0:
        nav_buttons.append(
            InlineKeyboardButton(text="\u25c0 \u041d\u0430\u0437\u0430\u0434", callback_data=f"acc:list:{offset - page_size}")  # ◀ Назад
        )
    if offset + page_size < total:
        nav_buttons.append(
            InlineKeyboardButton(text="\u0412\u043f\u0435\u0440\u0451\u0434 \u25b6", callback_data=f"acc:list:{offset + page_size}")  # Вперёд ▶
        )
    if nav_buttons:
        buttons.append(nav_buttons)

    # Back to menu
    buttons.append([
        InlineKeyboardButton(text="\u2b05 \u041d\u0430\u0437\u0430\u0434", callback_data="acc:menu")  # ⬅ Назад
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def account_detail_keyboard(account_id: uuid.UUID, status: str) -> InlineKeyboardMarkup:
    """Detail view for a single account."""
    buttons: list[list[InlineKeyboardButton]] = []

    if status == "active":
        buttons.append([
            InlineKeyboardButton(
                text="\u23f8 \u041f\u0430\u0443\u0437\u0430",     # ⏸ Пауза
                callback_data=f"acc:pause:{account_id}",
            ),
        ])
    elif status == "paused":
        buttons.append([
            InlineKeyboardButton(
                text="\u25b6 \u0412\u043e\u0437\u043e\u0431\u043d\u043e\u0432\u0438\u0442\u044c",  # ▶ Возобновить
                callback_data=f"acc:resume:{account_id}",
            ),
        ])

    buttons.append([
        InlineKeyboardButton(
            text="\U0001f5d1 \u0423\u0434\u0430\u043b\u0438\u0442\u044c",    # 🗑 Удалить
            callback_data=f"acc:delete:{account_id}",
        ),
    ])

    buttons.append([
        InlineKeyboardButton(
            text="\u2b05 \u041a \u0441\u043f\u0438\u0441\u043a\u0443",    # ⬅ К списку
            callback_data="acc:list:0",
        ),
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def cancel_keyboard() -> InlineKeyboardMarkup:
    """Cancel button for any FSM flow."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="\u274c \u041e\u0442\u043c\u0435\u043d\u0430", callback_data="cancel")]  # ❌ Отмена
        ],
    )
